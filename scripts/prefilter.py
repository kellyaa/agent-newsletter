"""Promote new items → candidate, applying cheap deterministic filters.

Reads `new` items, applies recency/keyword/dedup gates, writes survivors back
as status='candidate' and emits candidates.json for the ranker.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import REPO_ROOT, connect, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("prefilter")

CANDIDATES_OUT = REPO_ROOT / "candidates.json"

# Items must mention at least one keyword in title or raw_text.
KEYWORDS = [
    r"\bagent(s|ic|s')?\b",
    r"\btool[- ]use\b",
    r"\btool[- ]calling\b",
    r"\bllm\b",
    r"\blanguage model\b",
    r"\bmcp\b",
    r"\bmodel context protocol\b",
    r"\brag\b",
    r"\bretrieval[- ]augmented\b",
    r"\beval(s|uation)?\b",
    r"\bmulti[- ]agent\b",
    r"\bcode generation\b",
    r"\bcopilot\b",
    r"\bchain[- ]of[- ]thought\b",
    r"\bplanning\b",
    r"\bworkflow\b",
    r"\bautonomous\b",
    r"\bclaude code\b",
    r"\bgpt[- ]?\d?\b",
    r"\bclaude\b",
    r"\bgemini\b",
    r"\bdspy\b",
    r"\blanggraph\b",
    r"\blangchain\b",
    r"\bautogen\b",
    r"\bcrewai\b",
]
KEYWORD_RE = re.compile("|".join(KEYWORDS), re.IGNORECASE)

# Recency windows by source family. RSS is wide because practitioner blogs
# publish weekly-or-monthly; the cross-day dedup layer (status >= 'ranked' is
# sealed) prevents stale republishing once an item has been seen.
RECENCY_DAYS = {
    "arxiv": 7,
    "rss": 30,
    "hn": 3,
    "reddit": 3,
    "gh": 14,
    "default": 3,
}

# Source priority for cross-source collapse (higher beats lower).
SOURCE_PRIORITY = {
    "arxiv": 5,
    "rss": 4,
    "gh": 3,
    "hn": 2,
    "reddit": 1,
}

# Source family → output section. See SPEC.md "Section assignment".
SECTION_BY_FAMILY = {
    "arxiv": "papers",
    "hf-daily": "papers",
    "gh": "news",
    "hn": "news",
    "reddit": "news",
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


def _source_family(source: str) -> str:
    return source.split(":", 1)[0]


def _passes_recency(item: dict, now: datetime) -> bool:
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


def _passes_keyword_gate(item: dict) -> bool:
    if item.get("keyword_gate_bypass"):
        return True
    haystack = (item.get("title") or "") + "\n" + (item.get("raw_text") or "")
    return bool(KEYWORD_RE.search(haystack))


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", title.lower()).strip()


def _title_tokens(title: str) -> set[str]:
    return set(_normalize_title(title).split())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def collapse_near_dups(items: list[dict], threshold: float = 0.85) -> list[dict]:
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


def main() -> int:
    init_db()
    conn = connect()
    now = datetime.now(timezone.utc)

    # Fetch all `new` items, plus any existing `appendix` items eligible for retry.
    rows = conn.execute(
        """
        SELECT id, source, url, canonical_url, title, author, published_at,
               fetched_at, raw_text, status, appearances, section_override,
               keyword_gate_bypass, recency_days_override
        FROM items
        WHERE status = 'new'
           OR (status = 'appendix' AND appearances < ?)
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
            "UPDATE items SET status = 'candidate', section = ? WHERE id = ?",
            [(it["section"], it["id"]) for it in after_dedup],
        )
        conn.commit()

    # Demote new-but-rejected to 'dropped' so they don't re-enter the pipeline.
    rejected_ids = [
        it["id"] for it in items
        if it["status"] == "new" and it["id"] not in set(candidate_ids)
    ]
    if rejected_ids:
        placeholders = ",".join("?" * len(rejected_ids))
        conn.execute(
            f"UPDATE items SET status = 'dropped' WHERE id IN ({placeholders})",
            rejected_ids,
        )
        conn.commit()
        log.info("dropped %d items that failed prefilter", len(rejected_ids))

    # Emit candidates.json grouped by section, sourced from the DB's *current*
    # `status = candidate` rows. Re-running prefilter regenerates the same
    # snapshot regardless of whether new items were added this invocation.
    grouped: dict[str, list[dict]] = {"papers": [], "news": [], "blogs": []}
    rows = conn.execute(
        """
        SELECT id, source, url, title, author, published_at, raw_text,
               section, section_override
        FROM items
        WHERE status = 'candidate'
        """
    ).fetchall()
    for r in rows:
        d = dict(r)
        section = d.get("section") or assign_section(
            d["source"], d.get("section_override")
        )
        grouped[section].append({
            "id": d["id"],
            "source": d["source"],
            "url": d["url"],
            "title": d["title"],
            "author": d["author"],
            "published_at": d["published_at"],
            "raw_text": d["raw_text"],
        })
    CANDIDATES_OUT.write_text(json.dumps(grouped, indent=2, ensure_ascii=False))
    log.info(
        "wrote %s — papers=%d news=%d blogs=%d",
        CANDIDATES_OUT,
        len(grouped["papers"]),
        len(grouped["news"]),
        len(grouped["blogs"]),
    )

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
