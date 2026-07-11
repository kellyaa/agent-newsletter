"""Invoke an OpenAI-compatible LLM to rank candidate items, then apply
per-section thresholds and caps to assign each item a final status.

Reads:  candidates.json (grouped by section, written by prefilter.py)
Writes: ranked.json (raw ranker output, for debugging)
        DB:  items.score, items.tags, items.why, items.status

Architecture: one LLM call per section. Each call gets the section name and
that section's candidates inlined into the prompt — no tool calls needed.
"""
from __future__ import annotations

import json
import logging
import os
import sys

from db import REPO_ROOT, connect, init_db
from llm import call_llm
from models import RankDecision, ScoredItem

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("rank")

PROMPT_PATH = REPO_ROOT / "prompts" / "rank.md"
CANDIDATES_PATH = REPO_ROOT / "candidates.json"
RANKED_PATH = REPO_ROOT / "ranked.json"

RANKER_MODEL = os.environ.get("RANKER_MODEL", "gpt-4o-mini")
# Per-section call timeout. Per-call output is bounded by section size, so this
# is generous — papers (up to 100+) is the long pole.
RANKER_TIMEOUT_S = int(os.environ.get("RANKER_TIMEOUT_S", "1800"))
# Cap per-call output. Healthy heaviest section (papers) lands around ~24k
# completion tokens; this caps it at ~32k to fail fast on degenerate
# repetition loops.
RANKER_MAX_TOKENS = int(os.environ.get("RANKER_MAX_TOKENS", "32000"))

# Per-section thresholds and caps. See SPEC.md "Ranking rubric".
# Papers also has burst_cap/burst_trigger_score/burst_trigger_count for the
# adaptive cap policy: when today's count of papers at score >= burst_trigger_score
# reaches burst_trigger_count, the cap rises from `cap` to `burst_cap`.
SECTION_RULES = {
    "papers": {
        "featured_min": 7,
        "appendix_min": 5,
        "cap": 5,
        "burst_cap": 10,
        "burst_trigger_score": 10,
        "burst_trigger_count": 10,
    },
    "news":   {"featured_min": 6, "appendix_min": 4, "cap": 6},
    "blogs":  {"featured_min": 6, "appendix_min": 4, "cap": 6},
}


def effective_cap(section: str, items: list[dict]) -> int:
    """Pick today's cap for a section.

    Papers uses an adaptive policy: count papers at score >= burst_trigger_score;
    if that count reaches burst_trigger_count, return burst_cap, else return cap.
    Sections without burst_* keys always return their static cap.
    """
    rules = SECTION_RULES[section]
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

VALID_TAGS = {
    "frameworks", "tool-use", "memory", "planning", "evals",
    "code-agents", "devops-agents", "observability", "safety",
    "research", "infra", "multi-agent", "cost-latency",
}

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
        f"# Section to rank: {section}\n\n"
        f"Number of items: {len(items)}\n\n"
        f"Candidates (JSON array):\n\n"
        f"```json\n{items_json}\n```\n\n"
        f"---\n\n"
        f"{rubric}"
    )


def invoke_ranker(prompt: str, label: str) -> list[dict]:
    out = call_llm(
        prompt,
        RANKER_OUTPUT_SCHEMA,
        schema_name="ranker_output",
        model=RANKER_MODEL,
        timeout_s=RANKER_TIMEOUT_S,
        label=label,
        max_tokens=RANKER_MAX_TOKENS,
    )
    rankings = out.get("rankings")
    if not isinstance(rankings, list):
        debug_path = REPO_ROOT / f"logs/ranker-output-{label}.json"
        debug_path.parent.mkdir(exist_ok=True)
        debug_path.write_text(json.dumps(out, indent=2))
        raise RuntimeError(f"ranker returned no rankings for {label}; output at {debug_path}")
    return rankings


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
    for section, items in scored_by_section.items():
        rules = SECTION_RULES[section]
        items.sort(key=lambda e: (-e["score"], e["id"]))
        cap = effective_cap(section, items)
        featured_count = 0
        for entry in items:
            score = entry["score"]
            if score >= rules["featured_min"] and featured_count < cap:
                status = "featured"
                featured_count += 1
            elif score >= rules["featured_min"] and section == "papers":
                # Cleared the bar but lost the cap → keep in the pool for
                # tomorrow. The reader sees this paper at most once, in its
                # featured slot on whichever day it eventually wins one.
                status = "candidate"
            elif score >= rules["appendix_min"]:
                # Also covers news/blogs that cleared featured_min but lost
                # the cap (appendix_min < featured_min, so they land here).
                status = "appendix"
            else:
                status = "dropped"
            final[entry["id"]] = {
                "status": status,
                "score": score,
                "tags": entry.get("tags", []),
                "why": entry.get("why", ""),
                "section": section,
            }
    return final


def persist(conn, decisions: dict[str, RankDecision]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item_id, d in decisions.items():
        clean_tags = [t for t in d["tags"] if t in VALID_TAGS]
        conn.execute(
            "UPDATE items SET score = ?, tags = ?, why = ?, status = ? WHERE id = ?",
            (d["score"], json.dumps(clean_tags), d["why"], d["status"], item_id),
        )
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    conn.commit()
    return counts


def main() -> int:
    init_db()
    if not CANDIDATES_PATH.exists():
        log.error("missing %s — run prefilter first", CANDIDATES_PATH)
        return 2
    if not PROMPT_PATH.exists():
        log.error("missing %s", PROMPT_PATH)
        return 2

    candidates = json.loads(CANDIDATES_PATH.read_text())
    rubric = PROMPT_PATH.read_text()

    # `papers_prescored` is the multi-day pool's cached-score bucket (issue #16).
    # Items here have score+tags+why already; we skip the LLM and merge them
    # with whatever the LLM returns for the unscored `papers` bucket.
    BUCKETS = ("papers", "papers_prescored", "news", "blogs")

    # Idempotent resume: filter out items already past 'candidate' status.
    # If rank.py crashed after papers but before blogs, papers items are now
    # 'featured/appendix/dropped' and we should not re-send them to the LLM.
    conn_check = connect()
    try:
        candidate_rows = conn_check.execute(
            "SELECT id FROM items WHERE status = 'candidate'"
        ).fetchall()
    finally:
        conn_check.close()
    still_candidate = {r["id"] for r in candidate_rows}
    skipped_already_ranked = 0
    for bucket in BUCKETS:
        before = len(candidates.get(bucket, []))
        candidates[bucket] = [
            it for it in candidates.get(bucket, []) if it["id"] in still_candidate
        ]
        skipped_already_ranked += before - len(candidates[bucket])
    if skipped_already_ranked > 0:
        log.info(
            "rank: resume — skipping %d items already ranked in a prior run",
            skipped_already_ranked,
        )

    # Total count for sanity logging.
    total = sum(len(candidates.get(b, [])) for b in BUCKETS)
    log.info(
        "candidates: papers=%d (prescored=%d) news=%d blogs=%d total=%d",
        len(candidates.get("papers", [])),
        len(candidates.get("papers_prescored", [])),
        len(candidates.get("news", [])),
        len(candidates.get("blogs", [])),
        total,
    )
    if total == 0:
        log.warning("no candidates to rank; exiting cleanly")
        return 0

    scored_by_section: dict[str, list[dict]] = {"papers": [], "news": [], "blogs": []}

    # Papers: only invoke the LLM on the unscored bucket. Prescored items
    # already have a score from a prior day's run and bypass the LLM entirely.
    unscored_papers = candidates.get("papers", [])
    prescored_papers = candidates.get("papers_prescored", [])
    if unscored_papers:
        prompt = build_prompt("papers", unscored_papers, rubric)
        scored = invoke_ranker(prompt, label="papers")
        log.info("papers: ranker returned %d entries (sent %d)", len(scored), len(unscored_papers))
        scored_by_section["papers"].extend(scored)
    else:
        log.info("papers: no unscored items, skipping LLM call")
    # Merge in prescored items (they pass through assign_statuses with their
    # cached score and compete head-to-head with the freshly-scored ones).
    for it in prescored_papers:
        scored_by_section["papers"].append({
            "id": it["id"],
            "score": it["score"],
            "tags": it.get("tags", []),
            "why": it.get("why", ""),
        })
    if prescored_papers:
        log.info("papers: merged %d prescored items from the multi-day pool", len(prescored_papers))

    # News + blogs unchanged.
    for section in ("news", "blogs"):
        items = candidates.get(section, [])
        if not items:
            continue
        prompt = build_prompt(section, items, rubric)
        scored = invoke_ranker(prompt, label=section)
        log.info("%s: ranker returned %d entries (sent %d)", section, len(scored), len(items))
        scored_by_section[section] = scored

    # Save just the rankings (not the full envelopes — too noisy) for debugging.
    RANKED_PATH.write_text(json.dumps(scored_by_section, indent=2, ensure_ascii=False))

    # Build id → section map from input candidates for fallback handling.
    # Papers (both unscored + prescored) all collapse to section='papers'.
    by_id_section: dict[str, str] = {}
    for bucket, items in candidates.items():
        section = "papers" if bucket in ("papers", "papers_prescored") else bucket
        if section not in SECTION_RULES:
            continue  # pragma: no cover — candidates.json only has valid bucket keys
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
                "status": "appendix",
                "score": 0,
                "tags": [],
                "why": "fallback: ranker did not return a score",
                "section": by_id_section[iid],
            }

    conn = connect()
    counts = persist(conn, decisions)
    # Bump times_competed for every paper that competed and stayed in the
    # candidate pool (i.e. didn't get sealed to featured or appendix). Gating
    # on status='candidate' makes the increment a no-op for sealed items, so
    # featured/appendix winners can't have their counter inflated.
    papers_competitors = [
        iid for iid, d in decisions.items() if d["section"] == "papers"
    ]
    if papers_competitors:
        placeholders = ",".join("?" * len(papers_competitors))
        bumped = conn.execute(
            f"UPDATE items SET times_competed = times_competed + 1 "
            f"WHERE status = 'candidate' AND id IN ({placeholders})",
            papers_competitors,
        )
        conn.commit()
        log.info(
            "papers: bumped times_competed on %d pool items (of %d competitors)",
            bumped.rowcount or 0, len(papers_competitors),
        )
    conn.close()

    log.info(
        "DONE: featured=%d appendix=%d dropped=%d candidate=%d",
        counts.get("featured", 0),
        counts.get("appendix", 0),
        counts.get("dropped", 0),
        counts.get("candidate", 0),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
