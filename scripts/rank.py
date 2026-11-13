"""Invoke an OpenAI-compatible LLM to rank candidate items, then apply
per-section thresholds and caps to assign each item a final status.

Reads:  state.db (candidate pool, via candidates.load_ranking_pool)
Writes: ranked.json (raw ranker output, for debugging)
        DB:  items.score, items.tags, items.why, items.status

Architecture: one LLM call per section. Each call gets the section name and
that section's candidates inlined into the prompt — no tool calls needed.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from candidates import PAPER_PRERANK_CAP, PAPERS_PRESCORED, bound_prerank_scorer, load_ranking_pool
import edition
import logsetup
import prompts
from db import (
    REPO_ROOT,
    apply_rank_decisions,
    bump_times_competed,
    connect,
    content_root,
    init_db,
    reset_unpublished_featured,
)
from llm import ContentFilterError, call_llm, ranker_config
from llm_ids import alias_items, resolve_ids
from models import ExitCode, RankDecision, ScoredItem, Section, Status

log = logging.getLogger("rank")

# Base template; main() renders it with the edition's fragments. prefilter
# hashes the same file (its RUBRIC_PATH) to invalidate cached paper scores.
PROMPT_PATH = prompts.template_path("rank")


def ranked_path() -> Path:
    """The selected edition's ranked.json debug artifact (raw LLM scores).

    Resolved from ``EDITION`` / ``CONTENT_ROOT`` on every call (like
    :func:`db.db_path`), so ``import rank`` needs no edition and never pins a
    stale one (#446).
    """
    return edition.runtime_paths(content_root()).ranked


# Per-section thresholds and caps come from editions/<slug>/edition.yaml
# (`sections:`), read via edition.current().section_rules() at call time.
# See SPEC.md "Ranking rubric".
# Papers also has burst_cap/burst_trigger_score/burst_trigger_count for the
# adaptive cap policy: when today's count of papers at score >= burst_trigger_score
# reaches burst_trigger_count, the cap rises from `cap` to `burst_cap`.

# Deprecated module attributes, resolved lazily so importing this module does
# not require an edition (#446). New code calls edition.current() / ranked_path().
_LAZY_ALIASES = {
    "EDITION": lambda: edition.current(),
    "RANKED_PATH": lambda: ranked_path(),
    "SECTION_RULES": lambda: edition.current().section_rules(),
    "VALID_TAGS": lambda: edition.current().valid_tags(),
    "KEYWORD_RE": lambda: edition.current().keyword_re(),
    # Deprecated: invoke_ranker reads llm.ranker_config() per call.
    "RANKER": lambda: ranker_config(),
}


def __getattr__(name: str):
    if name in _LAZY_ALIASES:
        return _LAZY_ALIASES[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def effective_cap(section: str, items: list[dict]) -> int:
    """Pick today's cap for a section.

    Papers uses an adaptive policy: count papers at score >= burst_trigger_score;
    if that count reaches burst_trigger_count, return burst_cap, else return cap.
    Sections without burst_* keys always return their static cap.
    """
    rules = edition.current().section_rules()[section]
    burst_score = rules.get("burst_trigger_score")
    if burst_score is None:
        return rules["cap"]
    trigger_count = sum(1 for e in items if e["score"] >= burst_score)
    if trigger_count >= rules["burst_trigger_count"]:
        log.info(
            "%s: burst cap engaged (%d items at score>=%d, threshold=%d) → cap=%d",
            section, trigger_count, burst_score,
            rules["burst_trigger_count"], rules["burst_cap"],
        )
        return rules["burst_cap"]
    return rules["cap"]

RANKER_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["rankings"],
    "additionalProperties": False,
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "score", "tags", "why"],
                "additionalProperties": False,
                "properties": {
                    "id":    {"type": "string", "minLength": 1},
                    "score": {"type": "integer", "minimum": 0, "maximum": 10},
                    "tags":  {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "why":   {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
        },
    },
}


def build_prompt(section: str, items: list[dict], rubric: str) -> str:
    """Return the full prompt: section header + items JSON + rubric."""
    items_json = json.dumps(items, ensure_ascii=False, indent=2)
    return (
        f"{prompts.UNTRUSTED_INPUT_NOTICE}\n\n"
        f"# Section to rank: {section}\n\n"
        f"Number of items: {len(items)}\n\n"
        f"Candidates (JSON array):\n\n"
        f"```json\n{items_json}\n```\n\n"
        f"---\n\n"
        f"{rubric}"
    )


def invoke_ranker(prompt: str, label: str) -> list[dict]:
    # Model, timeout and token cap: env-driven, read per call from
    # llm.ranker_config() so an env change after import is honoured.
    cfg = ranker_config()
    out = call_llm(
        prompt,
        RANKER_OUTPUT_SCHEMA,
        schema_name="ranker_output",
        model=cfg.model,
        timeout_s=cfg.timeout_s,
        label=label,
        max_tokens=cfg.max_tokens,
    )
    rankings = out.get("rankings")
    if not isinstance(rankings, list):
        debug_path = edition.current().paths().logs / f"ranker-output-{label}.json"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(json.dumps(out, indent=2))
        raise RuntimeError(f"ranker returned no rankings for {label}; output at {debug_path}")
    return rankings


def sanitize_rankings(rankings: list, sent_ids: set[str], label: str) -> list[dict]:
    """Keep only well-formed ranker entries for ids we actually sent.

    The output schema is only enforced when the provider honours
    response_format=json_schema (LLM_RESPONSE_FORMAT=json_object|none, e.g.
    LiteLLM-fronted Bedrock Claude, does not), so entries can arrive with a
    missing/null/non-integer score, a null `tags`, an id we never sent, or the
    same id twice. Unchecked, a bad score crashes the whole rank stage in
    assign_statuses' sort, and a made-up id takes a featured slot and counts
    against the cap while its UPDATE matches no row. Dropping the entry sends
    the real item (if any) through main()'s unscored → appendix fallback.
    Duplicates keep the first entry.
    """
    clean: list[dict] = []
    seen: set[str] = set()
    dropped = 0
    for e in rankings:
        if not isinstance(e, dict):
            dropped += 1
            continue
        item_id, score = e.get("id"), e.get("score")
        if isinstance(score, float) and score.is_integer():
            score = int(score)
        if (
            not isinstance(item_id, str)
            or item_id not in sent_ids
            or item_id in seen
            or isinstance(score, bool)
            or not isinstance(score, int)
            or not 0 <= score <= 10
        ):
            dropped += 1
            continue
        seen.add(item_id)
        tags = e.get("tags")
        why = e.get("why")
        clean.append({
            **e,
            "score": score,
            "tags": [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
            "why": why if isinstance(why, str) else "",
        })
    if dropped:
        log.warning(
            "%s: ignored %d malformed/unknown/duplicate ranker entries (of %d)",
            label, dropped, len(rankings),
        )
    return clean


def rank_section(section: str, items: list[dict], rubric: str) -> list[dict]:
    """Rank one section, isolating items the endpoint's content filter blocks
    and never letting one section's LLM failure abort the whole run.

    A single item can trip the filter (e.g. a malware writeup), and because the
    whole section goes in one call, that one item otherwise costs us every other
    item in the section — this failed the rank stage two days running.

    On a block we bisect: rank each half independently, so only the offending
    item goes unscored. Recursion bottoms out at a single item, which we skip.

    A skipped item returns no ranking, so main()'s defensive fallback sweeps it
    into the appendix with score 0 — it still appears in the issue, by title and
    URL only, with no LLM-written summary. That's the pre-existing behaviour for
    any unscored candidate; keeping it means we never silently lose an item.

    Separately, call_llm() can exhaust its retries and raise RuntimeError —
    truncation, a repetition loop, or content that even the tolerant parser in
    llm.py can't salvage (issue #208). Previously that propagated out of
    rank_section(), out of main(), and killed the entire rank stage: one bad
    blogs response meant zero newsletter that day, even though papers and news
    had already scored cleanly. We now catch it here, log it, and leave the
    whole batch unscored instead — the same appendix fallback that handles a
    content-filter block or an LLM omission. The run finishes and publishes
    whatever sections *did* score; the failed section's items reappear next
    run (unscored papers re-compete; news/blogs land in the appendix with no
    summary rather than vanishing).
    """
    try:
        # The model sees short per-call aliases (i1…iN), not 64-hex ids, so a
        # mis-copied id cannot demote a featured-grade item to the appendix.
        aliased, alias_to_id = alias_items(items)
        rankings = invoke_ranker(build_prompt(section, aliased, rubric), label=section)
        resolved, unknown = resolve_ids(rankings, alias_to_id)
        if unknown:
            log.warning("%s: ranker returned %d ids it was not sent: %s",
                        section, len(unknown), unknown[:3])
        return sanitize_rankings(resolved, set(alias_to_id.values()), section)
    except ContentFilterError:
        if len(items) == 1:
            log.warning(
                "%s: content-filtered item left unscored (appendix fallback): %r",
                section, items[0].get("title"),
            )
            return []
        mid = len(items) // 2
        log.warning(
            "%s: content filter blocked a batch of %d; bisecting into %d + %d",
            section, len(items), mid, len(items) - mid,
        )
        return (
            rank_section(section, items[:mid], rubric)
            + rank_section(section, items[mid:], rubric)
        )
    except RuntimeError as e:
        log.error(
            "%s: ranker call failed for a batch of %d items (%s); leaving "
            "unscored (appendix fallback) instead of aborting the run",
            section, len(items), e,
        )
        return []


def assign_statuses(scored_by_section: dict[str, list[ScoredItem]]) -> dict[str, RankDecision]:
    """Apply thresholds + per-section caps. Returns id -> {status, ...}.

    Papers special case (issue #16): on heavy supply days the section can land
    20+ papers above featured_min, but only 5 win the cap. Today those 15+
    high-quality items get sealed to appendix and never reappear. The pool
    fix: papers that score >= featured_min but lose the cap stay 'candidate'
    to re-compete tomorrow against a (likely smaller) field, until they win a
    featured slot or age out via the multi-day pool gate in prefilter.py.

    Mid-band papers (appendix_min <= score < featured_min) still go to appendix
    as today — they're not strong enough to ever win featured, so leaving them
    in the pool would just bloat it. Below-appendix-min still drops.

    News/blogs unchanged.
    """
    final: dict[str, dict] = {}
    section_rules = edition.current().section_rules()
    for section, items in scored_by_section.items():
        rules = section_rules[section]
        items.sort(key=lambda e: (-e["score"], e["id"]))
        cap = effective_cap(section, items)
        featured_count = 0
        for entry in items:
            score = entry["score"]
            if score >= rules["featured_min"] and featured_count < cap:
                status = Status.FEATURED
                featured_count += 1
            elif score >= rules["featured_min"] and section == Section.PAPERS:
                # Cleared the bar but lost the cap → keep in the pool for
                # tomorrow. The reader sees this paper at most once, in its
                # featured slot on whichever day it eventually wins one.
                status = Status.CANDIDATE
            elif score >= rules["appendix_min"]:
                # Also covers news/blogs that cleared featured_min but lost
                # the cap (appendix_min < featured_min, so they land here).
                status = Status.APPENDIX
            else:
                status = Status.DROPPED
            final[entry["id"]] = {
                "status": status,
                "score": score,
                "tags": entry.get("tags", []),
                "why": entry.get("why", ""),
                "section": section,
            }
    return final


def score_candidates(
    candidates: dict[str, list[dict]],
    rubric: str,
    *,
    ranked_path=None,
) -> dict[str, RankDecision]:
    """Score a grouped candidate pool and return id -> RankDecision.

    The single scoring core shared by :func:`main` (daily run) and
    ``backfill.rank_with_optional_llm`` (sandbox DB). Only the unscored
    buckets go to the LLM, each through :func:`rank_section` (content-filter
    bisection, per-section failure isolation, output sanitising);
    ``papers_prescored`` items keep their cached score and compete with the
    fresh ones in :func:`assign_statuses`. Any candidate the ranker did not
    score falls back to the appendix with score 0, so an item is never left
    in limbo. ``ranked_path``, when given, receives the sanitised rankings
    for debugging (the daily run only; backfill leaves live logs alone).
    Persisting and ``times_competed`` bookkeeping stay with the caller.
    tests/test_rank_scoring_single_source.py keeps backfill on this path.
    """
    scored_by_section: dict[str, list[dict]] = {s.value: [] for s in Section}

    # Papers: only invoke the LLM on the unscored bucket. Prescored items
    # already have a score from a prior day's run and bypass the LLM entirely.
    unscored_papers = candidates.get(Section.PAPERS, [])
    prescored_papers = candidates.get(PAPERS_PRESCORED, [])
    if unscored_papers:
        scored = rank_section(Section.PAPERS.value, unscored_papers, rubric)
        log.info("papers: ranker returned %d entries (sent %d)", len(scored), len(unscored_papers))
        scored_by_section[Section.PAPERS].extend(scored)
    else:
        log.info("papers: no unscored items, skipping LLM call")
    # Merge in prescored items (they pass through assign_statuses with their
    # cached score and compete head-to-head with the freshly-scored ones).
    for it in prescored_papers:
        scored_by_section[Section.PAPERS].append({
            "id": it["id"],
            "score": it["score"],
            "tags": it.get("tags", []),
            "why": it.get("why", ""),
        })
    if prescored_papers:
        log.info("papers: merged %d prescored items from the multi-day pool", len(prescored_papers))

    for section in (Section.NEWS.value, Section.BLOGS.value):
        items = candidates.get(section, [])
        if not items:
            continue
        scored = rank_section(section, items, rubric)
        log.info("%s: ranker returned %d entries (sent %d)", section, len(scored), len(items))
        scored_by_section[section] = scored

    if ranked_path is not None:
        # Just the rankings (not the full envelopes — too noisy) for debugging.
        ranked_path.parent.mkdir(parents=True, exist_ok=True)
        ranked_path.write_text(json.dumps(scored_by_section, indent=2, ensure_ascii=False))

    # Build id → section map from input candidates for fallback handling.
    # Papers (both unscored + prescored) all collapse to section='papers'.
    by_id_section: dict[str, str] = {}
    section_rules = edition.current().section_rules()
    for bucket, items in candidates.items():
        section = Section.PAPERS.value if bucket in (Section.PAPERS, PAPERS_PRESCORED) else bucket
        if section not in section_rules:
            continue  # pragma: no cover — load_candidates_from_db only returns valid bucket keys
        for it in items:
            by_id_section[it["id"]] = section

    decisions = assign_statuses(scored_by_section)

    # Defensive fallback: any candidate not scored → appendix with score 0.
    # (Only triggers when the LLM omits an id it was sent. Prescored items
    # are merged into scored_by_section directly so they can never end up here.)
    missing = [iid for iid in by_id_section if iid not in decisions]
    if missing:
        log.warning("%d candidates not scored → fallback to appendix", len(missing))
        for iid in missing:
            decisions[iid] = {
                "status": Status.APPENDIX,
                "score": 0,
                "tags": [],
                "why": "fallback: ranker did not return a score",
                "section": by_id_section[iid],
            }
    return decisions


def persist(conn, decisions: dict[str, RankDecision]) -> dict[str, int]:
    counts: dict[str, int] = {}
    rows = []
    valid_tags = edition.current().valid_tags()
    for item_id, d in decisions.items():
        clean_tags = [t for t in d["tags"] if t in valid_tags]
        rows.append((item_id, d["score"], json.dumps(clean_tags), d["why"], d["status"]))
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    apply_rank_decisions(conn, rows)
    return counts


def _prerank_scorer(item: dict, now) -> float:
    """candidates.prerank_score bound to this edition's keyword gate."""
    return bound_prerank_scorer(edition.current().keyword_re())(item, now)


def main(now: datetime | None = None) -> int:
    """Run the rank stage.

    ``now`` is the wall-clock instant the papers pre-rank cap scores recency
    against. It defaults to the current UTC time; tests and backfills pass a
    fixed instant instead of patching ``datetime`` (same contract as
    ``prefilter.main``).
    """
    init_db()
    if not PROMPT_PATH.exists():
        log.error("missing %s", PROMPT_PATH)
        return ExitCode.MISSING_INPUT
    if now is None:
        now = datetime.now(timezone.utc)

    rubric = prompts.render_and_log("rank", edition.current(), template_path=PROMPT_PATH)

    # Start from a clean slate: publish promotes featured items, so any still
    # 'featured' now were picked by an earlier run that failed after rank. Left
    # alone they pile up across retries and the writer gets a bigger set each
    # time (#587). Rank persists all its verdicts in one commit, so there is no
    # half-ranked state to resume from.
    reset_conn = connect()
    try:
        stale = reset_unpublished_featured(reset_conn)
    finally:
        reset_conn.close()
    if stale:
        log.warning("reset %d featured items left by an unpublished earlier run", stale)

    # Load candidates directly from the DB. This is the same function
    # prefilter.py uses to write its debug artifact, ensuring consistency.
    # The query only returns items with status='candidate'; items already
    # dropped, appendix'd or published are not re-ranked.
    #
    # The prerank cap must be applied here, not just in prefilter's debug
    # artifact: it's what bounds the papers prompt (and so the LLM call's
    # duration) on burst days. Uncapped, a backlog after a failed run sends
    # 130+ papers in one call, which runs past the endpoint's 300s ceiling and
    # fails every attempt. Items beyond the cap stay 'candidate' and re-compete
    # on the next run.
    candidates = load_ranking_pool(now=now, keyword_re=edition.current().keyword_re())

    # `papers_prescored` is the multi-day pool's cached-score bucket (issue #16).
    # Items here have score+tags+why already; we skip the LLM and merge them
    # with whatever the LLM returns for the unscored `papers` bucket.
    BUCKETS = (*(s.value for s in Section), PAPERS_PRESCORED)

    # Total count for sanity logging.
    total = sum(len(candidates.get(b, [])) for b in BUCKETS)
    log.info(
        "candidates: papers=%d (prescored=%d) news=%d blogs=%d total=%d",
        len(candidates.get(Section.PAPERS, [])),
        len(candidates.get(PAPERS_PRESCORED, [])),
        len(candidates.get(Section.NEWS, [])),
        len(candidates.get(Section.BLOGS, [])),
        total,
    )
    if total == 0:
        log.warning("no candidates to rank; exiting cleanly")
        return ExitCode.OK

    decisions = score_candidates(candidates, rubric, ranked_path=ranked_path())

    conn = connect()
    counts = persist(conn, decisions)
    # Bump times_competed for every paper that competed and stayed in the
    # candidate pool (i.e. didn't get sealed to featured or appendix). Gating
    # on status='candidate' makes the increment a no-op for sealed items, so
    # featured/appendix winners can't have their counter inflated.
    papers_competitors = [
        iid for iid, d in decisions.items() if d["section"] == Section.PAPERS
    ]
    if papers_competitors:
        bumped = bump_times_competed(conn, papers_competitors)
        log.info(
            "papers: bumped times_competed on %d pool items (of %d competitors)",
            bumped, len(papers_competitors),
        )
    conn.close()

    log.info(
        "DONE: featured=%d appendix=%d dropped=%d candidate=%d",
        counts.get(Status.FEATURED, 0),
        counts.get(Status.APPENDIX, 0),
        counts.get(Status.DROPPED, 0),
        counts.get(Status.CANDIDATE, 0),
    )
    return ExitCode.OK


if __name__ == "__main__":
    logsetup.configure()
    sys.exit(main())
