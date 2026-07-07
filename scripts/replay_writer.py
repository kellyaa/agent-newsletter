"""Replay the writer stage against a past day's featured items.

Reads the featured set from the archived issue file's YAML frontmatter
(the authoritative record of what shipped), joins to state.db for the
full item data (raw_text, score, tags, why), invokes the writer LLM with
the current prompt, and writes the raw output to logs/theme-replay-<date>.json.

Does NOT mutate state.db or the archived issue file. Read-only.

Usage: python scripts/replay_writer.py <YYYY-MM-DD>

Design note: we cannot reconstruct the featured set from state.db alone.
publish.py promotes both featured and appendix items to status='published',
and last_seen_date advances every time an ingester re-sees the URL. The
issue file's frontmatter is the durable record of which specific items
were featured on which date, so that's the source of truth.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import REPO_ROOT, connect, init_db  # noqa: E402
from write import (  # noqa: E402
    ISSUES_DIR,
    PROMPT_PATH,
    RAW_TEXT_MAX,
    build_writer_input,
    find_previous_newsletter,
    invoke_writer,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("replay_writer")


def parse_issue_frontmatter(issue_path: Path) -> dict:
    """Extract the YAML frontmatter block from an Astro issue file."""
    text = issue_path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"{issue_path}: missing opening frontmatter fence")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"{issue_path}: missing closing frontmatter fence")
    block = text[4:end]
    return yaml.safe_load(block)


def load_from_issue_file(conn, date: str):
    """Reconstruct writer input from the archived issue file plus state.db.

    Featured IDs and appendix IDs come from the frontmatter (durable record
    of what shipped). Full item data (raw_text, score, tags, why) is looked
    up in state.db by id.
    """
    issue_path = ISSUES_DIR / f"{date}.md"
    if not issue_path.exists():
        raise FileNotFoundError(f"no archived issue at {issue_path}")

    fm = parse_issue_frontmatter(issue_path)
    featured_ids: list[str] = [entry["id"] for entry in (fm.get("featured") or [])]
    appendix_fm: dict = fm.get("appendix") or {}

    featured: list[dict] = []
    for item_id in featured_ids:
        row = conn.execute(
            """
            SELECT id, source, url, canonical_url, title, author, published_at,
                   raw_text, score, tags, why, section
            FROM items
            WHERE id = ?
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            log.warning("featured id %s from issue file not found in state.db", item_id)
            continue
        try:
            tags = json.loads(row["tags"]) if row["tags"] else []
        except json.JSONDecodeError:
            tags = []
        raw = row["raw_text"] or ""
        if len(raw) > RAW_TEXT_MAX:
            raw = raw[:RAW_TEXT_MAX] + "\n...[truncated]"
        featured.append({
            "id": row["id"],
            "section": row["section"],
            "source": row["source"],
            "url": row["url"],
            "title": row["title"],
            "author": row["author"],
            "published_at": row["published_at"],
            "raw_text": raw,
            "score": row["score"],
            "tags": tags,
            "why": row["why"],
        })

    # Preserve production ordering: within a section, by score desc, then id.
    # Matches scripts/write.py:load_today_items ORDER BY clause.
    section_rank = {"papers": 0, "news": 1, "blogs": 2}
    featured.sort(key=lambda it: (
        section_rank.get(it["section"], 99),
        -(it["score"] or 0),
        it["id"],
    ))

    appendix_by_section: dict[str, list[dict]] = {"papers": [], "news": [], "blogs": []}
    for section in ("papers", "news", "blogs"):
        for entry in (appendix_fm.get(section) or []):
            appendix_by_section[section].append({
                "id": entry["id"],
                "section": section,
                "source": entry["source"],
                "url": entry["url"],
                "title": entry["title"],
            })

    counts = {"papers": 0, "news": 0, "blogs": 0}
    for it in featured:
        counts[it["section"]] = counts.get(it["section"], 0) + 1
    appendix_total = sum(len(v) for v in appendix_by_section.values())
    metadata = {
        "items_considered": None,
        "items_featured_total": len(featured),
        "items_featured_papers": counts["papers"],
        "items_featured_news": counts["news"],
        "items_featured_blogs": counts["blogs"],
        "items_appendix": appendix_total,
    }
    return featured, appendix_by_section, metadata


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/replay_writer.py <YYYY-MM-DD>", file=sys.stderr)
        return 2

    date = sys.argv[1]
    init_db()
    conn = connect()
    try:
        featured, appendix_by_section, metadata = load_from_issue_file(conn, date)
    finally:
        conn.close()

    if not featured:
        log.error("no featured items reconstructed for %s", date)
        return 1

    log.info(
        "replay date=%s featured=%d (papers=%d news=%d blogs=%d) appendix=%d",
        date, len(featured),
        metadata["items_featured_papers"],
        metadata["items_featured_news"],
        metadata["items_featured_blogs"],
        metadata["items_appendix"],
    )

    prev = find_previous_newsletter(date)
    payload = build_writer_input(date, featured, appendix_by_section, metadata, prev)

    rubric = PROMPT_PATH.read_text()
    prompt = (
        "You are writing today's AI Agents newsletter prose. The input JSON "
        "is below; the rubric and output schema follow.\n\n"
        f"## Input\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        f"---\n\n{rubric}"
    )

    writer_output = invoke_writer(prompt)

    out_path = REPO_ROOT / "logs" / f"theme-replay-{date}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps({
        "date": date,
        "theme": writer_output.get("theme"),
        "items": writer_output.get("items", []),
    }, indent=2))
    log.info("wrote %s", out_path)
    log.info("theme: %s", writer_output.get("theme"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
