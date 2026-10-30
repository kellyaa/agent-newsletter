"""SQLite bootstrap and shared helpers.

The schema is idempotent — calling init_db() repeatedly is safe.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import edition as _edition

REPO_ROOT = Path(__file__).resolve().parent.parent
_CONTENT_ROOT_ENV = os.environ.get("CONTENT_ROOT")
CONTENT_ROOT = Path(_CONTENT_ROOT_ENV) if _CONTENT_ROOT_ENV else REPO_ROOT
DB_PATH = _edition.runtime_paths(CONTENT_ROOT).state_db


def run_date() -> str:
    """The date this run produces, as ``YYYY-MM-DD``.

    This is the single source of "which day is this run" for every stage: the
    issue filename, ``items.first_seen_date``/``last_seen_date``, ``runs.date``,
    ``run_attempts.date``, ``run_stage_stats.date`` and the fetch-completed
    marker. ``run_edition.sh`` exports ``RUN_DATE`` once per run, so every stage
    keys on the same day even if the run crosses midnight or is re-run by hand
    for an earlier date. Without ``RUN_DATE`` (a stage run directly), it falls
    back to the local date.

    Wall-clock *instants* (``fetched_at``, recency windows, attempt timestamps)
    are not run dates and keep using the clock.
    """
    return os.environ.get("RUN_DATE") or date.today().isoformat()


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
  times_competed INTEGER NOT NULL DEFAULT 0,
  published_date TEXT
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
  notes TEXT,
  papers_expired_unjudged INTEGER,
  papers_prerank_cut INTEGER
);

-- Per-day counters written by prefilter and copied onto the runs row by
-- publish (#297). Staged here because the runs row only exists once publish
-- succeeds, and creating it earlier would trip publish's idempotent skip.
CREATE TABLE IF NOT EXISTS run_stage_stats (
  date TEXT PRIMARY KEY,
  papers_expired_unjudged INTEGER NOT NULL DEFAULT 0,
  papers_prerank_cut INTEGER NOT NULL DEFAULT 0
);

-- One row per run.sh invocation, failed ones included (scripts/run_attempt.py).
-- Unlike runs, nothing in the pipeline reads it, so it can't change behaviour.
CREATE TABLE IF NOT EXISTS run_attempts (
  date TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  outcome TEXT NOT NULL,   -- started | ok | failed; 'started' = died mid-run
  failed_stage TEXT,
  exit_code INTEGER,
  code_commit TEXT,
  code_dirty INTEGER,
  host TEXT,
  llm_calls INTEGER,
  tokens_in INTEGER,
  tokens_out INTEGER,
  models TEXT,
  tokens_by_model TEXT,
  PRIMARY KEY (date, attempt)
);

-- Small per-edition key/value store. Holds `rubric_hash` (sha256 of the rendered
-- rank prompt), which used to be an untracked .rubric_hash file at the repo root.
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
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

# Schemes allowed for item URLs that flow into rendered <a href> on the
# public site (and RSS feed links). Anything else — javascript:, data:,
# file:, vbscript:, etc. — is a click-to-execute or local-file-disclosure
# vector and must be rejected at ingest, before it ever reaches state.db.
# See issue #248.
ALLOWED_URL_SCHEMES = {"http", "https"}


def is_allowed_url(url: str) -> bool:
    """Return True iff `url` parses to an http(s) scheme.

    Used at ingest (fetch.py) to reject non-http(s) URLs — javascript:,
    data:, file:, etc. — before they're persisted to state.db and
    eventually rendered as a clickable link on the site or in RSS.
    """
    try:
        scheme = urlparse(url.strip()).scheme.lower()
    except (AttributeError, ValueError):
        return False
    return scheme in ALLOWED_URL_SCHEMES


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    # Each edition's DB sits in its own editions/<slug>/ folder, which does not
    # exist yet for a brand-new edition.
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
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
    if "published_date" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN published_date TEXT")
    run_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    for col in ("papers_expired_unjudged", "papers_prerank_cut"):
        if col not in run_cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} INTEGER")


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def count_items_seen_on(conn: sqlite3.Connection, date: str) -> int:
    """Items whose ``last_seen_date`` is `date`: the run's "items considered".

    fetch (idempotent-skip check), write (issue metadata) and publish (the
    ``runs`` row) all report this number, so they share one definition.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM items WHERE last_seen_date = ?", (date,)
    ).fetchone()
    return int(row[0]) if row else 0


def count_items_in_status(conn: sqlite3.Connection, *statuses: str) -> int:
    """Items whose ``status`` is any of `statuses` (pass ``models.Status`` values)."""
    if not statuses:
        raise ValueError("count_items_in_status needs at least one status")
    marks = ", ".join("?" * len(statuses))
    row = conn.execute(
        f"SELECT COUNT(*) FROM items WHERE status IN ({marks})", tuple(statuses)
    ).fetchone()
    return int(row[0]) if row else 0


def record_stage_stats(
    conn: sqlite3.Connection,
    date: str,
    *,
    expired_unjudged: int = 0,
    prerank_cut: int = 0,
) -> None:
    """Stage today's papers pool counters for publish to copy onto `runs` (#297).

    `expired_unjudged` accumulates across the day's attempts (each attempt's
    age-out is distinct work). `prerank_cut` keeps the day's maximum: a retry
    re-cuts the same pool, so adding would double count, and a no-op re-run
    after publish would otherwise overwrite it with zero.
    """
    conn.execute(
        """
        INSERT INTO run_stage_stats (date, papers_expired_unjudged, papers_prerank_cut)
        VALUES (?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            papers_expired_unjudged =
                run_stage_stats.papers_expired_unjudged + excluded.papers_expired_unjudged,
            papers_prerank_cut =
                MAX(run_stage_stats.papers_prerank_cut, excluded.papers_prerank_cut)
        """,
        (date, expired_unjudged, prerank_cut),
    )
    conn.commit()


def load_stage_stats(conn: sqlite3.Connection, date: str) -> dict[str, int] | None:
    """Return the staged counters for `date`, or None if prefilter never ran."""
    row = conn.execute(
        "SELECT papers_expired_unjudged, papers_prerank_cut "
        "FROM run_stage_stats WHERE date = ?",
        (date,),
    ).fetchone()
    if row is None:
        return None
    return {
        "papers_expired_unjudged": row[0],
        "papers_prerank_cut": row[1],
    }


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
