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

# Per-section thresholds and caps. See SPEC.md "Ranking rubric".
SECTION_RULES = {
    "papers": {"featured_min": 7, "appendix_min": 5, "cap": 5},
    "news":   {"featured_min": 6, "appendix_min": 4, "cap": 6},
    "blogs":  {"featured_min": 6, "appendix_min": 4, "cap": 6},
}

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
    )
    rankings = out.get("rankings")
    if not isinstance(rankings, list):
        debug_path = REPO_ROOT / f"logs/ranker-output-{label}.json"
        debug_path.parent.mkdir(exist_ok=True)
        debug_path.write_text(json.dumps(out, indent=2))
        raise RuntimeError(f"ranker returned no rankings for {label}; output at {debug_path}")
    return rankings


def assign_statuses(scored_by_section: dict[str, list[dict]]) -> dict[str, dict]:
    """Apply thresholds + per-section caps. Returns id -> {status, ...}."""
    final: dict[str, dict] = {}
    for section, items in scored_by_section.items():
        rules = SECTION_RULES[section]
        items.sort(key=lambda e: (-e["score"], e["id"]))
        featured_count = 0
        for entry in items:
            score = entry["score"]
            if score >= rules["featured_min"] and featured_count < rules["cap"]:
                status = "featured"
                featured_count += 1
            elif score >= rules["appendix_min"]:
                status = "appendix"
            elif score >= rules["featured_min"]:
                # Cleared the bar but lost the cap → demote to appendix.
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


def persist(conn, decisions: dict[str, dict]) -> dict[str, int]:
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
    for section in ("papers", "news", "blogs"):
        before = len(candidates.get(section, []))
        candidates[section] = [
            it for it in candidates.get(section, []) if it["id"] in still_candidate
        ]
        skipped_already_ranked += before - len(candidates[section])
    if skipped_already_ranked > 0:
        log.info(
            "rank: resume — skipping %d items already ranked in a prior run",
            skipped_already_ranked,
        )

    # Total count for sanity logging.
    total = sum(len(candidates.get(s, [])) for s in ("papers", "news", "blogs"))
    log.info(
        "candidates: papers=%d news=%d blogs=%d total=%d",
        len(candidates.get("papers", [])),
        len(candidates.get("news", [])),
        len(candidates.get("blogs", [])),
        total,
    )
    if total == 0:
        log.warning("no candidates to rank; exiting cleanly")
        return 0

    scored_by_section: dict[str, list[dict]] = {}

    for section in ("papers", "news", "blogs"):
        items = candidates.get(section, [])
        if not items:
            scored_by_section[section] = []
            continue
        prompt = build_prompt(section, items, rubric)
        scored = invoke_ranker(prompt, label=section)
        log.info("%s: ranker returned %d entries (sent %d)", section, len(scored), len(items))
        scored_by_section[section] = scored

    # Save just the rankings (not the full envelopes — too noisy) for debugging.
    RANKED_PATH.write_text(json.dumps(scored_by_section, indent=2, ensure_ascii=False))

    # Build id → section map from input candidates for fallback handling.
    by_id_section: dict[str, str] = {}
    for section, items in candidates.items():
        for it in items:
            by_id_section[it["id"]] = section

    decisions = assign_statuses(scored_by_section)

    # Defensive fallback: any candidate not scored → appendix with score 0.
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
    conn.close()

    log.info(
        "DONE: featured=%d appendix=%d dropped=%d",
        counts.get("featured", 0),
        counts.get("appendix", 0),
        counts.get("dropped", 0),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
