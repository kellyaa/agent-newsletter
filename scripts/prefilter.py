"""Promote new items → candidate, applying cheap deterministic filters.

Reads `new` items, applies recency/keyword/dedup gates, writes survivors back
as status='candidate' and emits candidates.json for the ranker.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from candidates import PAPER_PRERANK_CAP, load_candidates_from_db, prerank_score
import edition
import prompts
from db import (
    CONTENT_ROOT, REPO_ROOT, connect, get_meta, init_db, record_stage_stats, run_date,
    set_meta,
)
from models import CandidateItem, PrefilterItem, Section, Status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("prefilter")

_RUNTIME = edition.runtime_paths(CONTENT_ROOT)
CANDIDATES_OUT = _RUNTIME.candidates

# Items must mention at least one keyword in title or raw_text. The list is
# per-edition (editions/<slug>/edition.yaml); importing this module requires
# EDITION to be set so a missing selection fails before any work is done.
EDITION = edition.current()
KEYWORDS = EDITION.keyword_regexes()
KEYWORD_RE = EDITION.keyword_re()

# Recency windows by source family. RSS is wide because practitioner blogs
# publish weekly-or-monthly; the cross-day dedup layer (status >= 'ranked' is
# sealed) prevents stale republishing once an item has been seen.
RECENCY_DAYS = {
    "arxiv": 7,
    "rss": 30,
    "hn": 3,
    "reddit": 3,
    "gh": 14,
    "html": 30,
    "default": 3,
}

# Source priority for cross-source collapse (higher beats lower).
SOURCE_PRIORITY = {
    "arxiv": 5,
    "rss": 4,
    "html": 4,
    "gh": 3,
    "hn": 2,
    "reddit": 1,
}

# Source family → output section. See SPEC.md "Section assignment".
SECTION_BY_FAMILY = {
    "arxiv": "papers",
    "gh": "news",
    "hn": "news",
    "reddit": "news",
    "html": "news",
    "rss": "blogs",
}


def assign_section(source: str, override: str | None = None) -> str:
    """Per-source override (from sources.yaml) wins over the family default."""
    if override in {"papers", "news", "blogs"}:
        return override
    family = _source_family(source)
    return SECTION_BY_FAMILY.get(family, "blogs")

# Featured items are sealed; appendix items get up to 2 chances total.
APPENDIX_MAX_APPEARANCES = 2

# Multi-day candidate pool for the papers section (see issue #16).
# Papers that pass prefilter sit in `status = candidate` for up to
# PAPER_POOL_MAX_AGE_DAYS days from their published_at, competing each run
# for one of the 5 featured slots. PAPER_POOL_MAX_COMPETES caps how many
# competitions a single paper can lose before being aged out — independent of
# wall-clock age, so a paper that lands during a quiet week still gets a fair
# number of attempts. PAPER_PRERANK_CAP (defined in candidates.py, shared with
# rank.py) bounds the LLM input size as a guard against a degenerate burst.
PAPER_POOL_MAX_COMPETES = 7
PAPER_POOL_MAX_AGE_DAYS = 7

# Age-out for the papers candidate pool. Shared with backfill.py so the two
# can't drift. Papers leave the pool when they hit the competition cap or when
# they pass the recency ceiling. A paper that was never sent to the ranker
# (times_competed = 0, score IS NULL) because PAPER_PRERANK_CAP kept truncating
# it is NOT marked 'dropped': an unscored expiry is indistinguishable from an
# editorial rejection, so it gets the distinct terminal status
# 'expired_unjudged'. Expiring it at all keeps the pool (and the per-run work in
# candidates._load) bounded when daily inflow exceeds the prerank cap (issue #289).
# Parameters: (max_competes, now, max_age_days).
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

# Cached rubric hash. When prompts/rank.md changes, all cached papers scores
# become stale (they were assigned under a different rubric), so we wipe them
# and let rank.py re-score from scratch on the next run.
RUBRIC_PATH = REPO_ROOT / "prompts" / "rank.md"
RUBRIC_HASH_KEY = "rubric_hash"  # key in the edition's state.db `meta` table


def _source_family(source: str) -> str:
    return source.split(":", 1)[0]


def _passes_recency(item: PrefilterItem, now: datetime) -> bool:
    family = _source_family(item["source"])
    override = item.get("recency_days_override")
    if isinstance(override, int) and override > 0:
        window = override
    else:
        window = RECENCY_DAYS.get(family, RECENCY_DAYS["default"])
    pub = item.get("published_at")
    if not pub:
        # No publication timestamp — fall back to fetched_at (always present).
        pub = item["fetched_at"]
    try:
        dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True  # don't drop things we can't parse; let the LLM judge
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt) <= timedelta(days=window)


def keyword_gate(item: PrefilterItem, keyword_re: re.Pattern[str]) -> bool:
    """The keyword gate for an arbitrary compiled pattern (scripts/check_edition.py
    uses it to evaluate an edition without importing it as the current one)."""
    if item.get("keyword_gate_bypass"):
        return True
    haystack = (item.get("title") or "") + "\n" + (item.get("raw_text") or "")
    return bool(keyword_re.search(haystack))


def _passes_keyword_gate(item: PrefilterItem) -> bool:
    return keyword_gate(item, KEYWORD_RE)


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", title.lower()).strip()


def _title_tokens(title: str) -> set[str]:
    return set(_normalize_title(title).split())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _prerank_score(item: PrefilterItem, now: datetime) -> float:
    """candidates.prerank_score bound to this edition's keyword gate."""
    return prerank_score(item, now, KEYWORD_RE)


def _rubric_hash() -> str:
    """sha256 of the *rendered* rank prompt, so a change to the base template or
    to any of the edition's fragments invalidates cached paper scores."""
    if not RUBRIC_PATH.exists():
        return ""
    rendered = prompts.render("rank", EDITION, template_path=RUBRIC_PATH)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _maybe_invalidate_papers_scores(conn) -> int:
    """If prompts/rank.md changed since the last run, wipe cached papers
    scores so rank.py re-scores them under the new rubric. Returns the count
    invalidated (0 on first run or when the hash hasn't changed)."""
    current = _rubric_hash()
    if not current:
        return 0
    last = get_meta(conn, RUBRIC_HASH_KEY) or ""
    if last == current:
        return 0
    cur = conn.execute(
        "UPDATE items SET score = NULL "
        f"WHERE status = '{Status.CANDIDATE}' AND section = '{Section.PAPERS}' "
        "AND score IS NOT NULL"
    )
    invalidated = cur.rowcount or 0
    set_meta(conn, RUBRIC_HASH_KEY, current)  # commits, along with the UPDATE above
    if invalidated:
        log.info(
            "rubric changed (hash %s → %s) — invalidated %d cached papers scores",
            last[:8] or "<none>", current[:8], invalidated,
        )
    else:
        # First run, or rubric changed but no papers were prescored yet.
        log.info("rubric hash recorded: %s", current[:8])
    return invalidated


def collapse_near_dups(items: list[PrefilterItem], threshold: float = 0.85) -> list[PrefilterItem]:
    """Within this run, drop items whose title is ~the same as a higher-priority one."""
    sorted_items = sorted(
        items,
        key=lambda it: SOURCE_PRIORITY.get(_source_family(it["source"]), 0),
        reverse=True,
    )
    kept: list[dict] = []
    kept_tokens: list[set[str]] = []
    for it in sorted_items:
        toks = _title_tokens(it["title"])
        if any(_jaccard(toks, prev) >= threshold for prev in kept_tokens):
            log.debug("dedup: collapsing %r", it["title"])
            continue
        kept.append(it)
        kept_tokens.append(toks)
    return kept


def age_out_papers(conn, now: datetime) -> tuple[int, int]:
    """Age out papers candidates that hit the competition cap or recency ceiling.

    ``now`` is passed explicitly (rather than SQLite's ``julianday('now')``)
    so tests and backfills can pin the clock. Commits, and returns
    ``(aged_out, expired_unjudged)``: the total rows moved out of the pool and
    how many of those had never been scored.
    """
    unjudged_before = conn.execute(
        f"SELECT COUNT(*) FROM items WHERE status = '{Status.EXPIRED_UNJUDGED}'"
    ).fetchone()[0]
    aged = conn.execute(
        PAPER_AGE_OUT_SQL,
        (
            PAPER_POOL_MAX_COMPETES,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            PAPER_POOL_MAX_AGE_DAYS,
        ),
    )
    aged_out = aged.rowcount or 0
    # Only this UPDATE moves rows into 'expired_unjudged', so the delta is this
    # run's never-judged expiries; the rest of the rowcount went to 'dropped'.
    expired_unjudged = conn.execute(
        f"SELECT COUNT(*) FROM items WHERE status = '{Status.EXPIRED_UNJUDGED}'"
    ).fetchone()[0] - unjudged_before
    conn.commit()
    return aged_out, expired_unjudged


def main(now: datetime | None = None) -> int:
    """Run the prefilter stage.

    ``now`` is the wall-clock instant used for every recency decision in this
    run (paper age-out, the per-source recency gate, the pre-rank scorer).
    It defaults to the current UTC time; tests and backfills pass a fixed
    instant instead of patching ``datetime``.
    """
    init_db()
    conn = connect()
    if now is None:
        now = datetime.now(timezone.utc)

    # Wipe cached papers scores if the rubric changed since the last run.
    _maybe_invalidate_papers_scores(conn)

    # Done up front so aged-out papers don't appear in candidates.json this run.
    aged_out, expired_unjudged_now = age_out_papers(conn, now)
    if aged_out:
        log.info("aged out %d papers candidates (cap or recency)", aged_out)
    log.info(
        "papers age-out this run: expired_unjudged=%d dropped=%d",
        expired_unjudged_now, aged_out - expired_unjudged_now,
    )

    unjudged = conn.execute(
        "SELECT COUNT(*) FROM items "
        f"WHERE section = '{Section.PAPERS}' AND score IS NULL "
        f"AND status IN ('{Status.DROPPED}', '{Status.EXPIRED_UNJUDGED}')"
    ).fetchone()[0]
    log.info("papers expired without ever being scored (cumulative): %d", unjudged)

    # Fetch all `new` items, plus any existing `appendix` items eligible for retry.
    rows = conn.execute(
        f"""
        SELECT id, source, url, canonical_url, title, author, published_at,
               fetched_at, raw_text, status, appearances, section_override,
               keyword_gate_bypass, recency_days_override
        FROM items
        WHERE status = '{Status.NEW}'
           OR (status = '{Status.APPENDIX}' AND appearances < ?)
        """,
        (APPENDIX_MAX_APPEARANCES,),
    ).fetchall()
    items = [dict(r) for r in rows]
    log.info("loaded %d items eligible for prefilter", len(items))

    if items:
        # Apply cheap gates.
        after_recency = [it for it in items if _passes_recency(it, now)]
        log.info("after recency: %d", len(after_recency))

        after_keyword = [it for it in after_recency if _passes_keyword_gate(it)]
        log.info("after keyword gate: %d", len(after_keyword))

        after_dedup = collapse_near_dups(after_keyword)
        log.info("after near-dup collapse: %d", len(after_dedup))
    else:
        # Idempotent re-run: prefilter already promoted everything. Skip the
        # gate work; we'll re-emit candidates.json from whatever is currently
        # in `status = candidate` below.
        after_dedup = []
        log.info("prefilter: no new items to gate (idempotent re-run)")

    # Assign section + promote survivors to 'candidate'.
    candidate_ids = [it["id"] for it in after_dedup]
    if candidate_ids:
        for it in after_dedup:
            it["section"] = assign_section(it["source"], it.get("section_override"))
        conn.executemany(
            f"UPDATE items SET status = '{Status.CANDIDATE}', section = ? WHERE id = ?",
            [(it["section"], it["id"]) for it in after_dedup],
        )
        conn.commit()

    # Demote new-but-rejected to 'dropped' so they don't re-enter the pipeline.
    rejected_ids = [
        it["id"] for it in items
        if it["status"] == Status.NEW and it["id"] not in set(candidate_ids)
    ]
    if rejected_ids:
        placeholders = ",".join("?" * len(rejected_ids))
        conn.execute(
            f"UPDATE items SET status = '{Status.DROPPED}' WHERE id IN ({placeholders})",
            rejected_ids,
        )
        conn.commit()
        log.info("dropped %d items that failed prefilter", len(rejected_ids))

    # Load the candidate pool from DB using the shared query function.
    # This is the canonical way to get grouped candidates — rank.py uses
    # the same function, eliminating the file-based coupling.
    cap_stats: dict[str, int] = {}
    grouped = load_candidates_from_db(
        conn,
        prerank_cap=PAPER_PRERANK_CAP,
        prerank_scorer=_prerank_score,
        now=now,
        stats=cap_stats,
    )
    prerank_cut = cap_stats.get("prerank_cut", 0)
    log.info("papers pre-rank cap cut this run: %d", prerank_cut)
    record_stage_stats(
        conn,
        run_date(),
        expired_unjudged=expired_unjudged_now,
        prerank_cut=prerank_cut,
    )

    # Write candidates.json as a debug/inspection artifact. rank.py does NOT
    # depend on this file — it queries the DB directly via load_candidates_from_db().
    CANDIDATES_OUT.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_OUT.write_text(json.dumps(grouped, indent=2, ensure_ascii=False))
    log.info(
        "wrote %s (debug artifact) — papers=%d (prescored=%d) news=%d blogs=%d",
        CANDIDATES_OUT,
        len(grouped["papers"]),
        len(grouped["papers_prescored"]),
        len(grouped["news"]),
        len(grouped["blogs"]),
    )

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
