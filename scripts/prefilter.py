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

# Multi-day candidate pool for the papers section (see issue #16).
# Papers that pass prefilter sit in `status = candidate` for up to
# PAPER_POOL_MAX_AGE_DAYS days from their published_at, competing each run
# for one of the 5 featured slots. PAPER_POOL_MAX_COMPETES caps how many
# competitions a single paper can lose before being aged out — independent of
# wall-clock age, so a paper that lands during a quiet week still gets a fair
# number of attempts. PAPER_PRERANK_CAP bounds the LLM input size on the
# rare burst day where >50 unscored papers land at once.
PAPER_POOL_MAX_COMPETES = 7
PAPER_POOL_MAX_AGE_DAYS = 7
PAPER_PRERANK_CAP = 50

# Cached rubric hash. When prompts/rank.md changes, all cached papers scores
# become stale (they were assigned under a different rubric), so we wipe them
# and let rank.py re-score from scratch on the next run.
RUBRIC_PATH = REPO_ROOT / "prompts" / "rank.md"
RUBRIC_HASH_PATH = REPO_ROOT / ".rubric_hash"


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


def _prerank_score(item: dict, now: datetime) -> float:
    """Cheap composite for capping the unscored-papers pool before the LLM.

    Source weight is omitted: all papers items today are arxiv:*, so a weight
    factor would be a constant. If hf-daily or another paper source is added
    later with a non-1.0 weight, plumb it through here.
    """
    pub = item.get("published_at") or item.get("fetched_at")
    try:
        dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    except (ValueError, AttributeError):
        age_days = 0.0
    recency = 1.0 / (1.0 + age_days)
    haystack = ((item.get("title") or "") + "\n" + (item.get("raw_text") or "")).lower()
    kw_hits = len(KEYWORD_RE.findall(haystack))
    kw = min(kw_hits, 5) / 5.0
    return recency * (0.5 + kw)


def _rubric_hash() -> str:
    if not RUBRIC_PATH.exists():
        return ""
    return hashlib.sha256(RUBRIC_PATH.read_bytes()).hexdigest()


def _maybe_invalidate_papers_scores(conn) -> int:
    """If prompts/rank.md changed since the last run, wipe cached papers
    scores so rank.py re-scores them under the new rubric. Returns the count
    invalidated (0 on first run or when the hash hasn't changed)."""
    current = _rubric_hash()
    if not current:
        return 0
    last = RUBRIC_HASH_PATH.read_text().strip() if RUBRIC_HASH_PATH.exists() else ""
    if last == current:
        return 0
    cur = conn.execute(
        "UPDATE items SET score = NULL "
        "WHERE status = 'candidate' AND section = 'papers' AND score IS NOT NULL"
    )
    invalidated = cur.rowcount or 0
    conn.commit()
    RUBRIC_HASH_PATH.write_text(current)
    if invalidated:
        log.info(
            "rubric changed (hash %s → %s) — invalidated %d cached papers scores",
            last[:8] or "<none>", current[:8], invalidated,
        )
    else:
        # First run, or rubric changed but no papers were prescored yet.
        log.info("rubric hash recorded: %s", current[:8])
    return invalidated


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

    # Wipe cached papers scores if the rubric changed since the last run.
    _maybe_invalidate_papers_scores(conn)

    # Age out papers candidates that have hit the per-paper competition cap or
    # exceeded the recency ceiling. Done up front so they don't appear in
    # candidates.json this run. Recency uses julianday() which treats
    # published_at/fetched_at as UTC dates; the cutoff matches the
    # PAPER_POOL_MAX_AGE_DAYS constant.
    aged = conn.execute(
        """
        UPDATE items
        SET status = 'dropped'
        WHERE status = 'candidate'
          AND section = 'papers'
          AND (times_competed >= ?
               OR julianday('now') - julianday(COALESCE(published_at, fetched_at)) >= ?)
        """,
        (PAPER_POOL_MAX_COMPETES, PAPER_POOL_MAX_AGE_DAYS),
    )
    aged_out = aged.rowcount or 0
    conn.commit()
    if aged_out:
        log.info("aged out %d papers candidates (cap or recency)", aged_out)

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
    #
    # Papers handling (issue #16): papers items live in the candidate pool
    # for up to PAPER_POOL_MAX_COMPETES days, scored once and re-selected each
    # day. Items with score IS NOT NULL go into `papers_prescored` so rank.py
    # can skip the LLM call for them. Items with score IS NULL are unscored
    # newcomers — these are the only ones subject to PAPER_PRERANK_CAP.
    grouped: dict[str, list[dict]] = {
        "papers": [],
        "papers_prescored": [],
        "news": [],
        "blogs": [],
    }
    rows = conn.execute(
        """
        SELECT id, source, url, title, author, published_at, raw_text,
               section, section_override, score, tags, why
        FROM items
        WHERE status = 'candidate'
        """
    ).fetchall()
    for r in rows:
        d = dict(r)
        section = d.get("section") or assign_section(
            d["source"], d.get("section_override")
        )
        emitted = {
            "id": d["id"],
            "source": d["source"],
            "url": d["url"],
            "title": d["title"],
            "author": d["author"],
            "published_at": d["published_at"],
            "raw_text": d["raw_text"],
        }
        if section == "papers" and d.get("score") is not None:
            # Carry the cached score + tags + why so rank.py can merge without
            # re-invoking the LLM. tags is a JSON-encoded array on disk.
            emitted["score"] = d["score"]
            try:
                emitted["tags"] = json.loads(d["tags"]) if d["tags"] else []
            except (TypeError, ValueError):
                emitted["tags"] = []
            emitted["why"] = d["why"] or ""
            grouped["papers_prescored"].append(emitted)
        else:
            grouped[section].append(emitted)

    # Pre-rank cap: only applies to unscored papers, since prescored ones are
    # free to keep around (no LLM cost). Sort by composite heuristic and keep
    # top-N; the rest are NOT dropped from the DB — they'll re-compete tomorrow
    # with whatever fresh arrivals show up, and can win a slot via the heuristic
    # then.
    if len(grouped["papers"]) > PAPER_PRERANK_CAP:
        before = len(grouped["papers"])
        grouped["papers"].sort(key=lambda it: _prerank_score(it, now), reverse=True)
        grouped["papers"] = grouped["papers"][:PAPER_PRERANK_CAP]
        log.info(
            "papers pre-rank cap: %d unscored → %d (top by recency × keyword density)",
            before, len(grouped["papers"]),
        )

    CANDIDATES_OUT.write_text(json.dumps(grouped, indent=2, ensure_ascii=False))
    log.info(
        "wrote %s — papers=%d (prescored=%d) news=%d blogs=%d",
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
