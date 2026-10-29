"""Build the day's newsletter via an OpenAI-compatible LLM endpoint, emitting
an issue file the Astro site consumes.

The writer LLM produces only editorial prose (theme + per-item summaries +
optional takeaway/open_question). All structured data — URLs, titles, scores,
tags, source labels, the appendix — is assembled here from state.db and
written verbatim into the YAML frontmatter. This makes URL hallucination
mechanically impossible: the LLM never gets to set a URL.

Output: site/src/content/issues/YYYY-MM-DD.md
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys

import edition
import prompts
from db import CONTENT_ROOT, REPO_ROOT, connect, init_db, run_date
from llm import call_llm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("write")

EDITION = edition.current()
# Base template; main() renders it with the edition's fragments.
PROMPT_PATH = REPO_ROOT / "prompts" / "write.md"
ISSUES_DIR = edition.runtime_paths(CONTENT_ROOT).issues_dir

WRITER_MODEL = os.environ.get("WRITER_MODEL", "gpt-4o-mini")
WRITER_TIMEOUT_S = int(os.environ.get("WRITER_TIMEOUT_S", "1200"))
# Cap per-call output. Healthy writer runs land around 6-8k completion tokens;
# this leaves comfortable headroom while failing fast on degenerate repetition
# loops (which we've seen burn 65k tokens producing unparseable truncated JSON).
WRITER_MAX_TOKENS = int(os.environ.get("WRITER_MAX_TOKENS", "16000"))

RAW_TEXT_MAX = 1500
PREV_NEWSLETTER_MAX = 4000

# Reader profile comes from editions/<slug>/edition.yaml.
READER_PROFILE = EDITION.reader_profile

# JSON Schema enforced by Claude Code. The LLM must return exactly this shape.
WRITER_SCHEMA = {
    "type": "object",
    "required": ["theme", "items"],
    "additionalProperties": False,
    "properties": {
        "theme": {"type": ["string", "null"], "maxLength": 800},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "summary"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 1200},
                    "takeaway": {"type": ["string", "null"], "maxLength": 400},
                    "open_question": {"type": ["string", "null"], "maxLength": 400},
                },
            },
        },
    },
}


def load_today_items(conn, today: str):
    featured_rows = conn.execute(
        """
        SELECT id, source, url, canonical_url, title, author, published_at, raw_text,
               score, tags, why, section
        FROM items
        WHERE status = 'featured'
        ORDER BY section, score DESC, id
        """
    ).fetchall()

    appendix_rows = conn.execute(
        """
        SELECT id, source, url, canonical_url, title, section
        FROM items
        WHERE status = 'appendix'
        ORDER BY section, id
        """
    ).fetchall()

    featured: list[dict] = []
    for r in featured_rows:
        try:
            tags = json.loads(r["tags"]) if r["tags"] else []
        except json.JSONDecodeError:
            tags = []
        raw = r["raw_text"] or ""
        if len(raw) > RAW_TEXT_MAX:
            raw = raw[:RAW_TEXT_MAX] + "\n...[truncated]"
        featured.append({
            "id": r["id"],
            "section": r["section"],
            "source": r["source"],
            "url": r["url"],
            "title": r["title"],
            "author": r["author"],
            "published_at": r["published_at"],
            "raw_text": raw,
            "score": r["score"],
            "tags": tags,
            "why": r["why"],
        })

    appendix_by_section: dict[str, list[dict]] = {"papers": [], "news": [], "blogs": []}
    for r in appendix_rows:
        section = r["section"] or "blogs"
        appendix_by_section.setdefault(section, []).append({
            "id": r["id"],
            "section": section,
            "source": r["source"],
            "url": r["url"],
            "title": r["title"],
        })

    counts = {"papers": 0, "news": 0, "blogs": 0}
    for it in featured:
        counts[it["section"]] = counts.get(it["section"], 0) + 1
    items_considered = conn.execute(
        "SELECT COUNT(*) FROM items WHERE last_seen_date = ?", (today,)
    ).fetchone()[0]
    appendix_total = sum(len(v) for v in appendix_by_section.values())

    metadata = {
        "items_considered": int(items_considered),
        "items_featured_total": len(featured),
        "items_featured_papers": counts["papers"],
        "items_featured_news": counts["news"],
        "items_featured_blogs": counts["blogs"],
        "items_appendix": appendix_total,
    }
    return featured, appendix_by_section, metadata


def find_previous_newsletter(today: str) -> str | None:
    if not ISSUES_DIR.exists():
        return None
    prior = sorted(p for p in ISSUES_DIR.glob("*.md") if p.stem < today)
    if not prior:
        return None
    return prior[-1].read_text()


def build_writer_input(date: str, featured, appendix_by_section, metadata, prev) -> dict:
    payload = {
        "date": date,
        "reader_profile": READER_PROFILE,
        "featured": featured,
        "appendix": appendix_by_section,
        "metadata": metadata,
    }
    if prev:
        payload["previous_newsletter"] = prev[:PREV_NEWSLETTER_MAX]
    return payload


def build_writer_prompt(payload: dict, rubric: str) -> str:
    """Full writer prompt: untrusted-input notice, input JSON, then the rubric."""
    return (
        f"You are writing today's {EDITION.name} newsletter prose. The input JSON "
        "is below; the rubric and output schema follow.\n\n"
        f"{prompts.UNTRUSTED_INPUT_NOTICE}\n\n"
        f"## Input\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        f"---\n\n{rubric}"
    )


def invoke_writer(prompt: str) -> dict:
    out = call_llm(
        prompt,
        WRITER_SCHEMA,
        schema_name="writer_output",
        model=WRITER_MODEL,
        timeout_s=WRITER_TIMEOUT_S,
        label="writer",
        max_tokens=WRITER_MAX_TOKENS,
    )
    if not isinstance(out, dict) or "items" not in out:
        debug_path = EDITION.paths().logs / "writer-output.json"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(json.dumps(out, indent=2))
        raise RuntimeError(f"writer returned no items; output at {debug_path}")
    return out


# ---------- Frontmatter assembly ----------

_YAML_CTRL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u2028\u2029]")


# Minimal YAML escaping for our shape. We only ever emit strings, ints, nulls,
# and arrays of those. Strings always use double-quote with backslash escaping.
def _yaml_str(s: str | None) -> str:
    if s is None:
        return "null"
    # Preserve newlines in long strings using the folded-block scalar would be nice,
    # but for safety we just escape and use double quotes. Most prose summaries are
    # one paragraph with no embedded newlines.
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    # Remaining control chars (and NEL / LS / PS line breaks) are not valid
    # inside a YAML scalar; emit them as \\uXXXX escapes, which are valid in
    # double-quoted scalars.
    s = _YAML_CTRL_RE.sub(lambda m: f"\\u{ord(m.group()):04x}", s)
    return f'"{s}"'


def _yaml_value(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return _yaml_str(v)
    raise TypeError(f"unsupported scalar type: {type(v)}")


def _emit_dict(d: dict, indent: int) -> list[str]:
    lines = []
    pad = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{pad}{k}:")
            lines.extend(_emit_dict(v, indent + 1))
        elif isinstance(v, list):
            if not v:
                lines.append(f"{pad}{k}: []")
                continue
            lines.append(f"{pad}{k}:")
            for entry in v:
                if isinstance(entry, dict):
                    sub = _emit_dict(entry, indent + 1)
                    if sub:
                        # Replace the first line's leading "  " indent with "- "
                        first = sub[0]
                        first_pad = "  " * (indent + 1)
                        assert first.startswith(first_pad)
                        lines.append(f"{first_pad[:-2]}- {first[len(first_pad):]}")
                        lines.extend(sub[1:])
                else:
                    lines.append(f"{pad}- {_yaml_value(entry)}")
        else:
            lines.append(f"{pad}{k}: {_yaml_value(v)}")
    return lines


def emit_yaml_frontmatter(data: dict) -> str:
    lines = ["---"]
    lines.extend(_emit_dict(data, 0))
    lines.append("---")
    return "\n".join(lines) + "\n"


# ---------- Issue assembly ----------

def _opt_str(v) -> str | None:
    return v if isinstance(v, str) else None


def sanitize_writer_output(out) -> dict:
    """Return a well-formed writer output, dropping anything malformed.

    The writer schema is only enforced when the provider honours
    response_format=json_schema (LLM_RESPONSE_FORMAT=json_object|none does
    not), so `items` may be null/not a list, entries may be null or lack a
    string `id`, and prose fields may be non-strings. Non-string prose fields
    become None (summary falls back to the title in assemble_issue); entries
    that are not dicts or have a non-string id are skipped.
    """
    if not isinstance(out, dict):
        out = {}
    raw_items = out.get("items")
    items: list[dict] = []
    if isinstance(raw_items, list):
        for e in raw_items:
            if not isinstance(e, dict) or not isinstance(e.get("id"), str):
                continue
            items.append({
                "id": e["id"],
                "summary": _opt_str(e.get("summary")),
                "takeaway": _opt_str(e.get("takeaway")),
                "open_question": _opt_str(e.get("open_question")),
            })
    return {"theme": _opt_str(out.get("theme")), "items": items}


def assemble_issue(
    today: str,
    featured: list[dict],
    appendix_by_section: dict[str, list[dict]],
    metadata: dict,
    writer_output: dict,
) -> str:
    """Combine writer prose with structural data into a frontmatter+body MD file.

    The body contains the prose summaries, ordered by featured item; the
    template renders the structured fields around it. This split keeps URLs
    out of the LLM's hands entirely.
    """
    writer_output = sanitize_writer_output(writer_output)
    # Map writer-output items by id for splicing.
    writer_items: dict[str, dict] = {}
    for entry in writer_output.get("items", []):
        writer_items[entry["id"]] = entry

    featured_for_fm: list[dict] = []
    missing_summaries: list[str] = []
    for item in featured:
        w = writer_items.get(item["id"], {})
        summary = (w.get("summary") or "").strip()
        if not summary:
            missing_summaries.append(item["id"])
            summary = item.get("title", "")  # last-resort fallback
        featured_for_fm.append({
            "id": item["id"],
            "section": item["section"],
            "source": item["source"],
            "url": item["url"],
            "title": item["title"],
            "author": item.get("author"),
            "score": int(item["score"]) if item.get("score") is not None else 0,
            "tags": list(item.get("tags") or []),
            "summary": summary,
            "takeaway": (w.get("takeaway") or None) or None,
            "open_question": (w.get("open_question") or None) or None,
        })

    if missing_summaries:
        log.warning("writer missing summaries for %d items: %s",
                    len(missing_summaries), missing_summaries[:3])

    appendix_for_fm: dict[str, list[dict]] = {"papers": [], "news": [], "blogs": []}
    for sec, items in appendix_by_section.items():
        for it in items:
            appendix_for_fm[sec].append({
                "id": it["id"],
                "section": sec,
                "source": it["source"],
                "url": it["url"],
                "title": it["title"],
            })

    theme = (writer_output.get("theme") or "").strip() or None

    frontmatter_data = {
        "date": today,
        "theme": theme,
        "featured": featured_for_fm,
        "appendix": appendix_for_fm,
        "metadata": metadata,
    }
    fm = emit_yaml_frontmatter(frontmatter_data)

    # Body is intentionally minimal — Astro ingests the structured frontmatter
    # for layout. We include a single line so the file isn't body-empty (some
    # MD renderers trip on that), and so a human glancing at the raw file
    # sees something readable.
    body = (
        f"\nGenerated {today}. Editorial prose lives in `featured[*].summary`; "
        f"the page template handles layout.\n"
    )
    return fm + body


def main() -> int:
    init_db()
    if not PROMPT_PATH.exists():
        log.error("missing %s", PROMPT_PATH)
        return 2

    today = run_date()
    out_path = ISSUES_DIR / f"{today}.md"
    if out_path.exists():
        log.info(
            "write: skip — %s already exists (%d bytes); delete to rerun",
            out_path, out_path.stat().st_size,
        )
        return 0

    conn = connect()
    featured, appendix_by_section, metadata = load_today_items(conn, today)
    conn.close()

    appendix_total = sum(len(v) for v in appendix_by_section.values())
    log.info(
        "today=%s featured=%d (papers=%d news=%d blogs=%d) appendix=%d",
        today, len(featured),
        metadata["items_featured_papers"],
        metadata["items_featured_news"],
        metadata["items_featured_blogs"],
        appendix_total,
    )
    if not featured and not appendix_total:
        log.warning("nothing to publish for %s; skipping", today)
        return 0

    prev = find_previous_newsletter(today)
    payload = build_writer_input(today, featured, appendix_by_section, metadata, prev)

    rubric = prompts.render_and_log("write", EDITION, template_path=PROMPT_PATH)
    prompt = build_writer_prompt(payload, rubric)

    if featured:
        writer_output = invoke_writer(prompt)
    else:
        # Edge case: zero featured items but appendix non-empty. Skip the LLM
        # entirely; the issue page can still render an appendix-only digest.
        writer_output = {"theme": None, "items": []}

    issue_md = assemble_issue(today, featured, appendix_by_section, metadata, writer_output)

    ISSUES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ISSUES_DIR / f"{today}.md"
    out_path.write_text(issue_md)
    log.info("wrote %s (%d bytes)", out_path, len(issue_md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
