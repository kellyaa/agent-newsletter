"""SQLite bootstrap and shared helpers.

The schema is idempotent — calling init_db() repeatedly is safe.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import edition as _edition
from models import AttemptOutcome, Section, Status
# URL policy (scheme allowlist, dedup canonicalization, item ids) lives in
# urls.py; re-exported because callers and tests import it from db.
from urls import (  # noqa: F401
    ALLOWED_URL_SCHEMES, TRACKING_PARAM_PREFIXES, TRACKING_PARAMS,
    canonicalize_url, is_allowed_url, url_id,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def content_root() -> Path:
    """The content worktree: ``$CONTENT_ROOT`` if set, else the repo root.

    Read from the environment on every call, so it never goes stale.
    """
    env = os.environ.get("CONTENT_ROOT")
    return Path(env) if env else REPO_ROOT


def db_path() -> Path:
    """The selected edition's ``state.db``, resolved from the env at call time.

    Raises :class:`edition.EditionError` when ``EDITION`` is unset. Importing
    this module does not.
    """
    return _edition.runtime_paths(content_root()).state_db


def __getattr__(name: str):
    # Deprecated aliases, resolved on every access (#446): ``DB_PATH`` for
    # db_path(), so ``import db`` does not require an edition to be selected,
    # and ``CONTENT_ROOT`` for content_root(), so a caller that changes
    # ``$CONTENT_ROOT`` after import never sees the old worktree.
    if name == "DB_PATH":
        return db_path()
    if name == "CONTENT_ROOT":
        return content_root()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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

-- One row per source per fetch run, written by fetch.py including the
-- zero-item case. A feed that returns nothing without raising is otherwise
-- indistinguishable from a quiet one (#572). `error` is NULL on success.
CREATE TABLE IF NOT EXISTS source_fetch_outcomes (
  run_date TEXT NOT NULL,
  label TEXT NOT NULL,       -- collector label, e.g. rss/latent_space
  family TEXT NOT NULL,      -- rss | arxiv | hn | reddit | html | gh
  source_id TEXT NOT NULL,
  items_seen INTEGER NOT NULL,
  items_new INTEGER NOT NULL,
  error TEXT,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (run_date, label)
);
"""

SOURCE_FETCH_RETENTION_DAYS = 90


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the state DB at ``path``, default :func:`db_path`."""
    path = db_path() if path is None else path
    # Each edition's DB sits in its own editions/<slug>/ folder, which does not
    # exist yet for a brand-new edition.
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing state DB read-only (``mode=ro``); never creates it.

    For the watchdog readers (missed_days, dead_sources), which inspect
    every edition's DB and must not create or migrate one.
    """
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=30)


def load_attempt_dates(path: Path) -> set[str]:
    """Every distinct ``run_attempts.date`` in the DB at `path` (read-only).

    Empty if the DB has no ``run_attempts`` table yet.
    """
    conn = connect_readonly(path)
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT date FROM run_attempts")}
    except sqlite3.OperationalError:
        return set()
    finally:
        conn.close()


def load_source_fetch_outcomes(path: Path) -> list[tuple[str, str, int, str | None]]:
    """``(label, run_date, items_seen, error)`` for every outcome row (read-only).

    Empty if the DB has no ``source_fetch_outcomes`` table yet.
    """
    conn = connect_readonly(path)
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT label, run_date, items_seen, error FROM source_fetch_outcomes"
        )]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    conn = connect(path)
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
    # topics_covered was a never-read, never-written stub; dropped per #619.
    conn.execute("DROP TABLE IF EXISTS topics_covered")


def record_source_fetch(
    conn: sqlite3.Connection, run_date: str, label: str, *, items_seen: int,
    items_new: int, error: str | None, fetched_at: str,
) -> None:
    """Record one source's outcome for the fetch run of `run_date`. Commits.

    `label` is ``<family>/<source id>`` (``gh/<owner>/<repo>`` for releases). A
    re-run of the same day replaces that source's row.
    """
    family, _, source_id = label.partition("/")
    conn.execute(
        "INSERT INTO source_fetch_outcomes "
        "(run_date, label, family, source_id, items_seen, items_new, error, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_date, label) DO UPDATE SET "
        "family = excluded.family, source_id = excluded.source_id, "
        "items_seen = excluded.items_seen, items_new = excluded.items_new, "
        "error = excluded.error, fetched_at = excluded.fetched_at",
        (run_date, label, family, source_id, items_seen, items_new, error, fetched_at),
    )
    conn.commit()


def prune_source_fetches(
    conn: sqlite3.Connection, today: str, keep_days: int = SOURCE_FETCH_RETENTION_DAYS,
) -> int:
    """Delete outcome rows older than `keep_days` before `today`; returns rows deleted."""
    cutoff = (date.fromisoformat(today) - timedelta(days=keep_days)).isoformat()
    cur = conn.execute("DELETE FROM source_fetch_outcomes WHERE run_date < ?", (cutoff,))
    conn.commit()
    return cur.rowcount or 0


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


# --- shared item reads: candidate pool and lookup by id ---------------------
# candidates.py and replay_writer.py used to run these SELECTs inline
# (advisory: "No data access layer").
_CANDIDATE_COLUMNS = (
    "id, source, url, title, author, published_at, raw_text, "
    "section, section_override, score, tags, why"
)
_ITEM_LOOKUP_COLUMNS = (
    "id, source, url, canonical_url, title, author, published_at, raw_text, "
    "score, tags, why, section"
)


def load_candidate_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every row in ``candidate`` status (the pool rank judges), unordered."""
    return conn.execute(
        f"SELECT {_CANDIDATE_COLUMNS} FROM items "
        f"WHERE status = '{Status.CANDIDATE}'"
    ).fetchall()


def load_item_by_id(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    """The item with id `item_id` in any status, or None if it isn't stored."""
    return conn.execute(
        f"SELECT {_ITEM_LOOKUP_COLUMNS} FROM items WHERE id = ?", (item_id,)
    ).fetchone()

# --- prefilter's item-state transitions --------------------------------------
# prefilter.py used to run these statements inline; they live here so the
# stage reads as gating logic and the SQL has one home (advisory: "No data
# access layer"). Column list matches models.PrefilterItem.
_PREFILTER_COLUMNS = (
    "id, source, url, canonical_url, title, author, published_at, "
    "fetched_at, raw_text, status, appearances, section_override, "
    "keyword_gate_bypass, recency_days_override"
)


def load_prefilter_eligible(
    conn: sqlite3.Connection, max_appendix_appearances: int
) -> list[dict]:
    """Rows prefilter gates this run: every ``new`` item, plus ``appendix``
    items that have appeared fewer than `max_appendix_appearances` times."""
    rows = conn.execute(
        f"SELECT {_PREFILTER_COLUMNS} FROM items "
        f"WHERE status = '{Status.NEW}' "
        f"OR (status = '{Status.APPENDIX}' AND appearances < ?)",
        (max_appendix_appearances,),
    ).fetchall()
    return [dict(r) for r in rows]


def promote_to_candidates(
    conn: sqlite3.Connection, assignments: list[tuple[str, str]]
) -> None:
    """Mark each ``(item_id, section)`` as a ``candidate`` in `section`. Commits."""
    if not assignments:
        return
    conn.executemany(
        f"UPDATE items SET status = '{Status.CANDIDATE}', section = ? WHERE id = ?",
        [(section, item_id) for item_id, section in assignments],
    )
    conn.commit()


def drop_items(conn: sqlite3.Connection, item_ids: list[str]) -> int:
    """Move `item_ids` to ``dropped``. Commits; returns the rows updated."""
    if not item_ids:
        return 0
    marks = ",".join("?" * len(item_ids))
    cur = conn.execute(
        f"UPDATE items SET status = '{Status.DROPPED}' WHERE id IN ({marks})",
        list(item_ids),
    )
    conn.commit()
    return cur.rowcount or 0


def clear_candidate_scores(conn: sqlite3.Connection, section: str) -> int:
    """NULL the cached score of every scored ``candidate`` in `section`.

    Does not commit: the caller pairs this with the meta write that records
    why (prefilter's rubric hash), so both land in one transaction.
    """
    cur = conn.execute(
        "UPDATE items SET score = NULL "
        f"WHERE status = '{Status.CANDIDATE}' AND section = ? AND score IS NOT NULL",
        (section,),
    )
    return cur.rowcount or 0


def count_never_scored_expired(conn: sqlite3.Connection, section: str) -> int:
    """Items in `section` that left the pool (``dropped``/``expired_unjudged``)
    without ever receiving a score: cumulative, across all runs."""
    row = conn.execute(
        "SELECT COUNT(*) FROM items WHERE section = ? AND score IS NULL "
        f"AND status IN ('{Status.DROPPED}', '{Status.EXPIRED_UNJUDGED}')",
        (section,),
    ).fetchone()
    return int(row[0]) if row else 0


# --- rank's item-state transitions -------------------------------------------
# rank.py used to run these statements inline; same rationale as prefilter's
# helpers above (advisory: "No data access layer").
def apply_rank_decisions(
    conn: sqlite3.Connection,
    rows: list[tuple[str, float | int | None, str, str, str]],
) -> None:
    """Write each ``(item_id, score, tags_json, why, status)`` ranker verdict.

    `tags_json` is the already-filtered, JSON-encoded tag list. Commits.
    """
    if not rows:
        return
    conn.executemany(
        "UPDATE items SET score = ?, tags = ?, why = ?, status = ? WHERE id = ?",
        [(score, tags, why, status, item_id)
         for item_id, score, tags, why, status in rows],
    )
    conn.commit()


def reset_unpublished_featured(conn: sqlite3.Connection) -> int:
    """Send every still-``featured`` item back to ``candidate``, scores cleared.

    ``publish`` moves featured items to ``published``, so a ``featured`` row at
    the start of rank is left over from an earlier run that failed after rank
    (e.g. at write). Without this, each retry adds its new winners to the last
    attempt's and the writer sees an ever-larger set (#587). Commits; returns
    the rows reset.
    """
    cur = conn.execute(
        f"UPDATE items SET status = '{Status.CANDIDATE}', score = NULL, tags = NULL, "
        f"why = NULL WHERE status = '{Status.FEATURED}'"
    )
    conn.commit()
    return cur.rowcount or 0


def bump_times_competed(conn: sqlite3.Connection, item_ids: list[str]) -> int:
    """Increment ``times_competed`` for `item_ids` still in the ``candidate`` pool.

    Gating on status means sealed (featured/appendix) winners are never
    inflated. Commits; returns the rows bumped.
    """
    if not item_ids:
        return 0
    marks = ",".join("?" * len(item_ids))
    cur = conn.execute(
        "UPDATE items SET times_competed = times_competed + 1 "
        f"WHERE status = '{Status.CANDIDATE}' AND id IN ({marks})",
        list(item_ids),
    )
    conn.commit()
    return cur.rowcount or 0


# --- papers-pool age-out and backfill promotion ------------------------------
# prefilter.py and backfill.py used to run these UPDATEs inline (advisory: "No
# data access layer"). The age-out statement is shared by both so they can't
# drift.
#
# Papers leave the pool when they hit the competition cap or pass the recency
# ceiling. A paper never sent to the ranker (times_competed = 0, score IS NULL)
# because PAPER_PRERANK_CAP kept truncating it is NOT marked 'dropped': an
# unscored expiry is indistinguishable from an editorial rejection, so it gets
# the distinct terminal status 'expired_unjudged'. Expiring it at all keeps the
# pool (and the per-run work in candidates._load) bounded when daily inflow
# exceeds the prerank cap (issue #289). Parameters: (max_competes, now,
# max_age_days).
PAPER_AGE_OUT_SQL = f"""
    UPDATE items
    SET status = CASE
        WHEN times_competed = 0 AND score IS NULL THEN '{Status.EXPIRED_UNJUDGED}'
        ELSE '{Status.DROPPED}'
    END
    WHERE status = '{Status.CANDIDATE}'
      AND section = '{Section.PAPERS}'
      AND (times_competed >= ?
           OR julianday(?) - julianday(COALESCE(published_at, fetched_at)) >= ?)
"""


def age_out_papers_pool(
    conn: sqlite3.Connection,
    *,
    max_competes: int,
    now: str,
    max_age_days: int,
    commit: bool = True,
) -> int:
    """Move papers candidates past the competition cap or age ceiling out of the pool.

    ``now`` is an SQLite-parsable timestamp (``julianday(now)``), passed in so
    callers can pin the clock. Returns the rows moved. ``commit=False`` lets
    the caller read the post-update state before committing.
    """
    cur = conn.execute(PAPER_AGE_OUT_SQL, (max_competes, now, max_age_days))
    if commit:
        conn.commit()
    return cur.rowcount or 0


def mark_candidates_published(
    conn: sqlite3.Connection, item_ids: list[str], date: str,
) -> int:
    """Promote `item_ids` still in ``candidate`` status to ``published`` on ``date``.

    Sets ``published_date`` like :func:`publish_selected_items`: every path
    that seals a row as ``published`` records the issue date, which
    :func:`reset_processed_items_for` (``reset_day --force``) matches on to
    restore pool papers. Gated on status so re-runs (or rows promoted by
    another path) are left alone. Commits; returns the rows promoted.
    """
    if not item_ids:
        return 0
    marks = ",".join("?" * len(item_ids))
    cur = conn.execute(
        f"UPDATE items SET status = '{Status.PUBLISHED}', published_date = ? "
        f"WHERE status = '{Status.CANDIDATE}' AND id IN ({marks})",
        [date, *item_ids],
    )
    conn.commit()
    return cur.rowcount or 0


# --- write's issue-assembly reads --------------------------------------------
# write.py used to run these SELECTs inline (advisory: "No data access layer").
_FEATURED_COLUMNS = (
    "id, source, url, canonical_url, title, author, published_at, raw_text, "
    "score, tags, why, section"
)
_APPENDIX_COLUMNS = "id, source, url, canonical_url, title, section"


def load_featured_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Rows in ``featured`` status, ordered by section, score desc, id."""
    return conn.execute(
        f"SELECT {_FEATURED_COLUMNS} FROM items "
        f"WHERE status = '{Status.FEATURED}' "
        "ORDER BY section, score DESC, id"
    ).fetchall()


def demote_featured_to_appendix(conn: sqlite3.Connection, item_ids: list[str]) -> int:
    """Move `item_ids` still in ``featured`` status to ``appendix``.

    For featured items the writer produced no summary for (#587). Commits;
    returns the rows demoted.
    """
    if not item_ids:
        return 0
    marks = ",".join("?" * len(item_ids))
    cur = conn.execute(
        f"UPDATE items SET status = '{Status.APPENDIX}' "
        f"WHERE status = '{Status.FEATURED}' AND id IN ({marks})",
        list(item_ids),
    )
    conn.commit()
    return cur.rowcount or 0


def load_appendix_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Rows in ``appendix`` status, ordered by section, id."""
    return conn.execute(
        f"SELECT {_APPENDIX_COLUMNS} FROM items "
        f"WHERE status = '{Status.APPENDIX}' "
        "ORDER BY section, id"
    ).fetchall()


# --- publish's run finalization ----------------------------------------------
# publish.py used to run these statements inline (advisory: "No data access
# layer").


def load_run_featured_count(conn: sqlite3.Connection, date: str) -> int | None:
    """``runs.items_featured`` for ``date``; None when no runs row exists.

    A row with a NULL count reads as 0, so callers can tell "already
    published" (not None) from "never published" (None).
    """
    row = conn.execute(
        "SELECT items_featured FROM runs WHERE date = ?", (date,)
    ).fetchone()
    if row is None:
        return None
    return int(row[0] or 0)


def count_featured_by_section(conn: sqlite3.Connection) -> dict[str, int]:
    """Number of ``featured`` items per section; sections with none are absent."""
    rows = conn.execute(
        f"SELECT section, COUNT(*) FROM items WHERE status = '{Status.FEATURED}' "
        "GROUP BY section ORDER BY section"
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def publish_selected_items(conn: sqlite3.Connection, date: str) -> int:
    """Move every featured/appendix item to ``published`` on ``date``.

    Commits. Returns rows updated.
    """
    cur = conn.execute(
        f"UPDATE items SET status = '{Status.PUBLISHED}', published_date = ? "
        f"WHERE status IN ('{Status.FEATURED}', '{Status.APPENDIX}')",
        (date,),
    )
    conn.commit()
    return cur.rowcount or 0


def upsert_run(
    conn: sqlite3.Connection,
    date: str,
    *,
    items_fetched: int,
    items_candidate: int,
    items_featured: int,
    items_papers: int,
    items_news: int,
    items_blogs: int,
    notes: str,
    duration_seconds: int | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    papers_expired_unjudged: int | None = None,
    papers_prerank_cut: int | None = None,
) -> None:
    """Insert or refresh the ``runs`` row for ``date``. Commits.

    Counts and notes are overwritten on re-publish; duration, tokens and the
    papers-pool counters keep their earlier value when the new one is None.
    """
    conn.execute(
        """
        INSERT INTO runs (date, items_fetched, items_candidate, items_featured,
                          items_papers, items_news, items_blogs,
                          duration_seconds, tokens_in, tokens_out, notes,
                          papers_expired_unjudged, papers_prerank_cut)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            items_fetched = excluded.items_fetched,
            items_candidate = excluded.items_candidate,
            items_featured = excluded.items_featured,
            items_papers = excluded.items_papers,
            items_news = excluded.items_news,
            items_blogs = excluded.items_blogs,
            duration_seconds = COALESCE(excluded.duration_seconds, runs.duration_seconds),
            tokens_in = COALESCE(excluded.tokens_in, runs.tokens_in),
            tokens_out = COALESCE(excluded.tokens_out, runs.tokens_out),
            notes = excluded.notes,
            papers_expired_unjudged = COALESCE(
                excluded.papers_expired_unjudged, runs.papers_expired_unjudged),
            papers_prerank_cut = COALESCE(
                excluded.papers_prerank_cut, runs.papers_prerank_cut)
        """,
        (
            date, items_fetched, items_candidate, items_featured,
            items_papers, items_news, items_blogs,
            duration_seconds, tokens_in, tokens_out, notes,
            papers_expired_unjudged, papers_prerank_cut,
        ),
    )
    conn.commit()


# --- reset_day's forced re-run resets ----------------------------------------
# reset_day.py used to run these statements inline (advisory: "No data access
# layer").


def delete_meta(conn: sqlite3.Connection, key: str) -> None:
    """Remove a ``meta`` row (no-op when absent); counterpart of set_meta."""
    conn.execute("DELETE FROM meta WHERE key = ?", (key,))
    conn.commit()


def delete_items_first_seen_on(conn: sqlite3.Connection, date: str) -> int:
    """Delete every item first fetched on ``date``; returns rows deleted."""
    cur = conn.execute("DELETE FROM items WHERE first_seen_date = ?", (date,))
    conn.commit()
    return cur.rowcount or 0


def reset_processed_items_for(conn: sqlite3.Connection, date: str) -> int:
    """Send ``date``'s featured/appendix/published items back to ``candidate``.

    Clears score/tags/why/published_date so rank and write re-run from
    scratch. ``new`` and ``dropped`` rows are left alone. Matches on
    first_seen_date (not last_seen_date, which fetch bumps for re-surfaced
    older items, #321) or published_date (multi-day pool papers first seen
    earlier that won a slot on ``date``, #326). Returns rows reset.
    """
    cur = conn.execute(
        f"UPDATE items SET status = '{Status.CANDIDATE}', score = NULL, tags = NULL, "
        "why = NULL, published_date = NULL "
        f"WHERE status IN ('{Status.FEATURED}', '{Status.APPENDIX}', '{Status.PUBLISHED}') "
        "AND (first_seen_date = ? OR published_date = ?)",
        (date, date),
    )
    conn.commit()
    return cur.rowcount or 0


def delete_run(conn: sqlite3.Connection, date: str) -> int:
    """Delete the ``runs`` row for ``date``; returns rows deleted."""
    cur = conn.execute("DELETE FROM runs WHERE date = ?", (date,))
    conn.commit()
    return cur.rowcount or 0


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


# --- run_attempts bookkeeping and check_edition's dry-run read ---------------
# run_attempt.py and check_edition.py used to run these statements inline
# (advisory: "No data access layer").
def next_attempt_number(conn: sqlite3.Connection, date: str) -> int:
    """1 + the highest ``run_attempts.attempt`` recorded for `date` (1 if none)."""
    return conn.execute(
        "SELECT COALESCE(MAX(attempt), 0) + 1 FROM run_attempts WHERE date = ?", (date,)
    ).fetchone()[0]


def insert_run_attempt(
    conn: sqlite3.Connection, date: str, attempt: int, started_at: str,
    code_commit: str | None, code_dirty: int | None, host: str,
) -> None:
    """Record attempt `attempt` of `date` with outcome ``started``. Commits."""
    conn.execute(
        "INSERT INTO run_attempts (date, attempt, started_at, outcome, code_commit, code_dirty, host)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (date, attempt, started_at, AttemptOutcome.STARTED, code_commit, code_dirty, host),
    )
    conn.commit()


def finish_run_attempt(
    conn: sqlite3.Connection, date: str, attempt: int, *, finished_at: str,
    outcome: str, failed_stage: str | None, exit_code: int | None, usage: dict,
) -> int:
    """Close out attempt `attempt` of `date`. Commits; returns the rows updated."""
    cur = conn.execute(
        "UPDATE run_attempts SET finished_at = ?, outcome = ?, failed_stage = ?, exit_code = ?,"
        " llm_calls = ?, tokens_in = ?, tokens_out = ?, models = ?, tokens_by_model = ?"
        " WHERE date = ? AND attempt = ?",
        (finished_at, outcome, failed_stage, exit_code,
         usage.get("calls"), usage.get("tokens_in"), usage.get("tokens_out"),
         usage.get("models"), usage.get("tokens_by_model"), date, attempt),
    )
    conn.commit()
    return cur.rowcount


def latest_attempt_outcome(conn: sqlite3.Connection, date: str) -> str | None:
    """Outcome of the highest-numbered attempt for `date`, or None if none."""
    row = conn.execute(
        "SELECT outcome FROM run_attempts WHERE date = ? ORDER BY attempt DESC LIMIT 1", (date,)
    ).fetchone()
    return row[0] if row else None


_GATE_COLUMNS = (
    "id, source, url, title, published_at, fetched_at, raw_text, "
    "section_override, keyword_gate_bypass, recency_days_override"
)
_IN_CHUNK = 400  # stay well under SQLite's bound-parameter limit


def load_gate_rows(conn: sqlite3.Connection, item_ids: list[str]) -> list[dict]:
    """The columns the prefilter gates read, for each stored id in `item_ids`."""
    rows: list[dict] = []
    for i in range(0, len(item_ids), _IN_CHUNK):
        chunk = item_ids[i:i + _IN_CHUNK]
        marks = ",".join("?" * len(chunk))
        rows.extend(
            dict(r) for r in conn.execute(
                f"SELECT {_GATE_COLUMNS} FROM items WHERE id IN ({marks})", chunk
            )
        )
    return rows


# --- fetch's item upsert -------------------------------------------------------
# fetch.upsert_items used to run this INSERT inline (advisory: "No data access
# layer"). URL vetting and id/canonical derivation stay in fetch; this is only
# the statement.
_UPSERT_ITEM_SQL = f"""
    INSERT INTO items (id, source, url, canonical_url, title, author,
                       published_at, fetched_at, raw_text, status,
                       section_override, keyword_gate_bypass,
                       recency_days_override,
                       first_seen_date, last_seen_date, appearances)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{Status.NEW}', ?, ?, ?, ?, ?, 1)
    ON CONFLICT(id) DO UPDATE SET
        last_seen_date = excluded.last_seen_date,
        appearances = items.appearances + 1,
        section_override = COALESCE(excluded.section_override,
                                    items.section_override),
        keyword_gate_bypass = MAX(excluded.keyword_gate_bypass,
                                  items.keyword_gate_bypass),
        recency_days_override = COALESCE(excluded.recency_days_override,
                                         items.recency_days_override)
"""


def upsert_item(
    conn: sqlite3.Connection, *, item_id: str, source: str, url: str,
    canonical_url: str, title: str, author: str | None, published_at: str | None,
    fetched_at: str, raw_text: str | None, section_override: str | None,
    keyword_gate_bypass: bool, recency_days_override: int | None, seen_date: str,
) -> bool:
    """Insert a new ``items`` row (status ``new``) or refresh a re-seen one.

    A new row gets ``first_seen_date = last_seen_date = seen_date`` and
    ``appearances = 1``. A re-seen id bumps ``appearances``, moves
    ``last_seen_date`` and merges the per-source overrides (a set override wins
    over NULL; ``keyword_gate_bypass`` is sticky once on). Everything else on
    the stored row is left alone.

    Does not commit (the caller commits once per batch). Returns whether SQLite
    reported a written row (rowcount and lastrowid both set).
    """
    cur = conn.execute(
        _UPSERT_ITEM_SQL,
        (
            item_id, source, url, canonical_url, title, author,
            published_at, fetched_at, raw_text,
            section_override, 1 if keyword_gate_bypass else 0,
            recency_days_override,
            seen_date, seen_date,
        ),
    )
    return bool(cur.rowcount and cur.lastrowid)


if __name__ == "__main__":
    init_db()
    print(f"Initialized {db_path()}")
