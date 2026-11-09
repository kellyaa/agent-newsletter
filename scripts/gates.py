"""Cheap deterministic prefilter gates: recency, section assignment, keyword, near-dup.

Pure functions over item dicts — no database, no filesystem, no edition loaded
at import time. ``prefilter.py`` composes them into the stage (and owns the
state.db reads/writes); ``check_edition.py`` applies the same gates to a
dry-run fetch without importing the prefilter stage. Keeping them here means
the two cannot drift and the gates are testable without ``$EDITION``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from models import PrefilterItem, Section, item_source_prefix

log = logging.getLogger("gates")

# The per-family tables below are keyed by models.ITEM_SOURCE_PREFIX values
# (the prefix fetch writes into items.source); every prefix must appear in
# each table (tests/test_item_source_single_source.py).
#
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
    "arxiv": Section.PAPERS.value,
    "gh": Section.NEWS.value,
    "hn": Section.NEWS.value,
    "reddit": Section.NEWS.value,
    "html": Section.NEWS.value,
    "rss": Section.BLOGS.value,
}

NEAR_DUP_THRESHOLD = 0.85


def source_family(source: str) -> str:
    """``"rss:simonwillison"`` → ``"rss"`` (see models.item_source_prefix)."""
    return item_source_prefix(source)


def assign_section(source: str, override: str | None = None) -> str:
    """Per-source override (from sources.yaml) wins over the family default."""
    if override in {s.value for s in Section}:
        return override
    return SECTION_BY_FAMILY.get(source_family(source), Section.BLOGS.value)


def passes_recency(item: PrefilterItem, now: datetime) -> bool:
    family = source_family(item["source"])
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
    """True when the title or raw_text matches ``keyword_re`` (or the source bypasses the gate)."""
    if item.get("keyword_gate_bypass"):
        return True
    haystack = (item.get("title") or "") + "\n" + (item.get("raw_text") or "")
    return bool(keyword_re.search(haystack))


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", title.lower()).strip()


def title_tokens(title: str) -> set[str]:
    return set(normalize_title(title).split())


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def collapse_near_dups(
    items: list[PrefilterItem], threshold: float = NEAR_DUP_THRESHOLD,
) -> list[PrefilterItem]:
    """Within one run, drop items whose title is ~the same as a higher-priority one."""
    sorted_items = sorted(
        items,
        key=lambda it: SOURCE_PRIORITY.get(source_family(it["source"]), 0),
        reverse=True,
    )
    kept: list[PrefilterItem] = []
    kept_tokens: list[set[str]] = []
    for it in sorted_items:
        toks = title_tokens(it["title"])
        if any(jaccard(toks, prev) >= threshold for prev in kept_tokens):
            log.debug("dedup: collapsing %r", it["title"])
            continue
        kept.append(it)
        kept_tokens.append(toks)
    return kept
