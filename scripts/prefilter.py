"""Promote new items → candidate, applying cheap deterministic filters.

Reads `new` items, applies recency/keyword/dedup gates, writes survivors back
as status='candidate' and emits candidates.json for the ranker.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone

from candidates import PAPER_PRERANK_CAP, PAPERS_PRESCORED, load_candidates_from_db, prerank_score
import edition
import prompts
from gates import (  # noqa: F401 — re-exported for tests and tooling
    RECENCY_DAYS, SECTION_BY_FAMILY, SOURCE_PRIORITY, assign_section,
    collapse_near_dups, keyword_gate, passes_recency as _passes_recency,
)
from db import (
    CONTENT_ROOT, REPO_ROOT, age_out_papers_pool, clear_candidate_scores, connect,
    count_items_in_status, count_never_scored_expired, drop_items, get_meta, init_db, load_prefilter_eligible,
    promote_to_candidates, record_stage_stats, run_date, set_meta,
)
from models import ExitCode, PrefilterItem, Section, Status

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

# Recency windows, source priority and section-by-family live in gates.py
# alongside the pure gate functions; re-exported here for existing callers.

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

# The age-out UPDATE itself lives in db.age_out_papers_pool(), shared with
# backfill.py so the two can't drift.

# Cached rubric hash. When prompts/rank.md changes, all cached papers scores
# become stale (they were assigned under a different rubric), so we wipe them
# and let rank.py re-score from scratch on the next run.
RUBRIC_PATH = REPO_ROOT / "prompts" / "rank.md"
RUBRIC_HASH_KEY = "rubric_hash"  # key in the edition's state.db `meta` table


def _passes_keyword_gate(item: PrefilterItem) -> bool:
    return keyword_gate(item, KEYWORD_RE)


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
    invalidated = clear_candidate_scores(conn, Section.PAPERS)
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


def age_out_papers(conn, now: datetime) -> tuple[int, int]:
    """Age out papers candidates that hit the competition cap or recency ceiling.

    ``now`` is passed explicitly (rather than SQLite's ``julianday('now')``)
    so tests and backfills can pin the clock. Commits, and returns
    ``(aged_out, expired_unjudged)``: the total rows moved out of the pool and
    how many of those had never been scored.
    """
    unjudged_before = count_items_in_status(conn, Status.EXPIRED_UNJUDGED)
    aged_out = age_out_papers_pool(
        conn,
        max_competes=PAPER_POOL_MAX_COMPETES,
        now=now.strftime("%Y-%m-%d %H:%M:%S"),
        max_age_days=PAPER_POOL_MAX_AGE_DAYS,
        commit=False,
    )
    # Only this UPDATE moves rows into 'expired_unjudged', so the delta is this
    # run's never-judged expiries; the rest of the rowcount went to 'dropped'.
    expired_unjudged = (
        count_items_in_status(conn, Status.EXPIRED_UNJUDGED) - unjudged_before
    )
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

    unjudged = count_never_scored_expired(conn, Section.PAPERS)
    log.info("papers expired without ever being scored (cumulative): %d", unjudged)

    # Fetch all `new` items, plus any existing `appendix` items eligible for retry.
    items = load_prefilter_eligible(conn, APPENDIX_MAX_APPEARANCES)
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
        promote_to_candidates(conn, [(it["id"], it["section"]) for it in after_dedup])

    # Demote new-but-rejected to 'dropped' so they don't re-enter the pipeline.
    rejected_ids = [
        it["id"] for it in items
        if it["status"] == Status.NEW and it["id"] not in set(candidate_ids)
    ]
    if rejected_ids:
        drop_items(conn, rejected_ids)
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
        len(grouped[Section.PAPERS]),
        len(grouped[PAPERS_PRESCORED]),
        len(grouped[Section.NEWS]),
        len(grouped[Section.BLOGS]),
    )

    conn.close()
    return ExitCode.OK


if __name__ == "__main__":
    sys.exit(main())
