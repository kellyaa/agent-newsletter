"""SQLite bootstrap and shared helpers.

The schema is idempotent — calling init_db() repeatedly is safe.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

REPO_ROOT = Path(__file__).resolve().parent.parent
_CONTENT_ROOT_ENV = os.environ.get("CONTENT_ROOT")
CONTENT_ROOT = Path(_CONTENT_ROOT_ENV) if _CONTENT_ROOT_ENV else REPO_ROOT
DB_PATH = CONTENT_ROOT / "state.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  url TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT,
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  raw_text TEXT,
  score INTEGER,
  tags TEXT,
  section TEXT,
  section_override TEXT,
  keyword_gate_bypass INTEGER NOT NULL DEFAULT 0,
  recency_days_override INTEGER,
  why TEXT,
  status TEXT NOT NULL,
  first_seen_date TEXT NOT NULL,
  last_seen_date TEXT NOT NULL,
  appearances INTEGER NOT NULL DEFAULT 1,
  times_competed INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_fetched_at ON items(fetched_at);
CREATE INDEX IF NOT EXISTS idx_items_section ON items(section);

CREATE TABLE IF NOT EXISTS runs (
  date TEXT PRIMARY KEY,
  items_fetched INTEGER,
  items_candidate INTEGER,
  items_featured INTEGER,
  items_papers INTEGER,
  items_news INTEGER,
  items_blogs INTEGER,
  duration_seconds INTEGER,
  tokens_in INTEGER,
  tokens_out INTEGER,
  cost_usd REAL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS topics_covered (
  topic TEXT NOT NULL,
  date TEXT NOT NULL,
  item_id TEXT NOT NULL,
  PRIMARY KEY (topic, date, item_id)
);
"""

TRACKING_PARAM_PREFIXES = ("utm_", "ref_")
TRACKING_PARAMS = {"ref", "fbclid", "gclid", "mc_cid", "mc_eid", "source"}


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that newer schema versions introduced.

    SCHEMA uses CREATE TABLE IF NOT EXISTS, so existing DBs miss new columns.
    Each ALTER is wrapped in a try/except so re-running on a fresh DB (where
    the column already exists from SCHEMA) is a no-op.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
    if "keyword_gate_bypass" not in cols:
        conn.execute(
            "ALTER TABLE items ADD COLUMN keyword_gate_bypass INTEGER NOT NULL DEFAULT 0"
        )
    if "recency_days_override" not in cols:
        conn.execute(
            "ALTER TABLE items ADD COLUMN recency_days_override INTEGER"
        )
    if "times_competed" not in cols:
        conn.execute(
            "ALTER TABLE items ADD COLUMN times_competed INTEGER NOT NULL DEFAULT 0"
        )


def canonicalize_url(url: str) -> str:
    """Return a normalized URL safe to use as a dedup key.

    - Lowercase scheme + host
    - Strip tracking query params
    - Strip trailing slash on path
    - Resolve arxiv pdf/abs/v<N> variants to the abs form
    """
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"

    if netloc.endswith("arxiv.org"):
        # /pdf/2401.12345.pdf, /pdf/2401.12345v2, /abs/2401.12345v3 → /abs/2401.12345
        import re
        m = re.match(r"^/(?:pdf|abs|html)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?(?:\.pdf)?/?$", path)
        if m:
            path = f"/abs/{m.group(1)}"

    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    kept = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=False):
        kl = k.lower()
        if kl in TRACKING_PARAMS:
            continue
        if any(kl.startswith(p) for p in TRACKING_PARAM_PREFIXES):
            continue
        kept.append((k, v))
    query = urlencode(kept)

    return urlunparse((scheme, netloc, path, "", query, ""))


def url_id(url: str) -> str:
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    init_db()
    print(f"Initialized {DB_PATH}")
