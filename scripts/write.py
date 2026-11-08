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
import re
import sys
from pathlib import Path

import edition
import prompts
from db import (
    REPO_ROOT,
    connect,
    content_root,
    count_items_seen_on,
    demote_featured_to_appendix,
    init_db,
    load_appendix_items,
    load_featured_items,
    run_date,
)
from llm import ContentFilterError, call_llm, writer_config
from llm_ids import alias_items, resolve_ids
from models import AppendixItem, ExitCode, FeaturedItem, FrontmatterFeatured, Section

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("write")

# Base template; main() renders it with the edition's fragments.
PROMPT_PATH = REPO_ROOT / "prompts" / "write.md"


def issues_dir() -> Path:
    """The selected edition's issue folder, resolved from the env at call time.

    Reads ``EDITION`` / ``CONTENT_ROOT`` on every call (like :func:`db.db_path`),
    so ``import write`` needs no edition and never pins a stale one (#446).
    """
    return edition.runtime_paths(content_root()).issues_dir


# Deprecated module attributes, resolved lazily so importing this module does
# not require an edition. The edition's reader profile comes from
# editions/<slug>/edition.yaml.
_LAZY_ALIASES = {
    "EDITION": lambda: edition.current(),
    "ISSUES_DIR": lambda: issues_dir(),
    "READER_PROFILE": lambda: edition.current().reader_profile,
    # Deprecated: invoke_writer reads llm.writer_config() per call.
    "WRITER": lambda: writer_config(),
}


def __getattr__(name: str):
    if name in _LAZY_ALIASES:
        return _LAZY_ALIASES[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


RAW_TEXT_MAX = 1500
PREV_NEWSLETTER_MAX = 4000

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


def featured_item_from_row(r) -> FeaturedItem:
    """Project one ``items`` row onto the writer's FeaturedItem shape.

    Single home for the row -> writer-input mapping (tag JSON decode,
    raw_text truncation to RAW_TEXT_MAX). Shared by load_today_items and
    scripts/replay_writer.py so a replay sees exactly what production sent;
    tests/test_writer_input_single_source.py enforces that.
    """
    try:
        tags = json.loads(r["tags"]) if r["tags"] else []
    except json.JSONDecodeError:
        tags = []
    raw = r["raw_text"] or ""
    if len(raw) > RAW_TEXT_MAX:
        raw = raw[:RAW_TEXT_MAX] + "\n...[truncated]"
    return FeaturedItem(
        id=r["id"],
        section=r["section"],
        source=r["source"],
        url=r["url"],
        title=r["title"],
        author=r["author"],
        published_at=r["published_at"],
        raw_text=raw,
        score=r["score"],
        tags=tags,
        why=r["why"],
    )


def writer_metadata(
    featured: list[FeaturedItem],
    appendix_by_section: dict[str, list[AppendixItem]],
    items_considered: int | None,
) -> dict:
    """Run metadata block for the writer input / issue frontmatter.

    ``items_considered`` is None when it cannot be reconstructed (replay).
    """
    counts = {s.value: 0 for s in Section}
    for it in featured:
        counts[it["section"]] = counts.get(it["section"], 0) + 1
    return {
        "items_considered": items_considered,
        "items_featured_total": len(featured),
        "items_featured_papers": counts[Section.PAPERS],
        "items_featured_news": counts[Section.NEWS],
        "items_featured_blogs": counts[Section.BLOGS],
        "items_appendix": sum(len(v) for v in appendix_by_section.values()),
    }


def load_today_items(
    conn, today: str
) -> tuple[list[FeaturedItem], dict[str, list[AppendixItem]], dict[str, int]]:
    """Featured items, appendix items grouped by section, and run metadata.

    Item shapes are bound to models.FeaturedItem / models.AppendixItem
    (pinned by tests/test_writer_item_contract.py).
    """
    featured_rows = load_featured_items(conn)
    appendix_rows = load_appendix_items(conn)

    featured: list[FeaturedItem] = [featured_item_from_row(r) for r in featured_rows]

    appendix_by_section: dict[str, list[AppendixItem]] = {s.value: [] for s in Section}
    for r in appendix_rows:
        section = r["section"] or Section.BLOGS.value
        appendix_by_section.setdefault(section, []).append(AppendixItem(
            id=r["id"],
            section=section,
            source=r["source"],
            url=r["url"],
            title=r["title"],
        ))

    metadata = writer_metadata(
        featured, appendix_by_section, int(count_items_seen_on(conn, today))
    )
    return featured, appendix_by_section, metadata


def find_previous_newsletter(today: str) -> str | None:
    folder = issues_dir()
    if not folder.exists():
        return None
    prior = sorted(p for p in folder.glob("*.md") if p.stem < today)
    if not prior:
        return None
    return prior[-1].read_text()


def build_writer_input(date: str, featured, appendix_by_section, metadata, prev) -> dict:
    payload = {
        "date": date,
        "reader_profile": edition.current().reader_profile,
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
        f"You are writing today's {edition.current().name} newsletter prose. The input JSON "
        "is below; the rubric and output schema follow.\n\n"
        f"{prompts.UNTRUSTED_INPUT_NOTICE}\n\n"
        f"## Input\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        f"---\n\n{rubric}"
    )


class WriterOutputError(RuntimeError):
    """The writer's response cannot be spliced into a trustworthy issue."""


def save_writer_response(out) -> Path:
    """Keep the writer's parsed response at ``logs/<slug>/write.response.json``.

    It sits next to the rendered prompt so a bad splice can be diagnosed
    without spending another writer call. Only the daily run saves it:
    backfill and replay must leave the live logs alone.
    """
    path = edition.current().paths().logs / "write.response.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return path


def invoke_writer(prompt: str) -> dict:
    # Model, timeout and token cap: env-driven, read per call from
    # llm.writer_config() so an env change after import is honoured.
    cfg = writer_config()
    out = call_llm(
        prompt,
        WRITER_SCHEMA,
        schema_name="writer_output",
        model=cfg.model,
        timeout_s=cfg.timeout_s,
        label="writer",
        max_tokens=cfg.max_tokens,
    )
    if not isinstance(out, dict) or "items" not in out:
        debug_path = edition.current().paths().logs / "writer-output.json"
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
    become None (assemble_issue then treats the summary as missing); entries
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


def resolve_writer_output(out, alias_to_id: dict[str, str]):
    """Map the aliases in a writer response back to real featured ids.

    The id contract (aliases, ``_dup`` tolerance, unknown ids) lives in
    ``llm_ids``; this only applies it to the writer's ``items`` array. A
    response without a list ``items`` is returned unchanged for
    :func:`sanitize_writer_output` to deal with. Entries whose id matches no
    featured item are dropped with a warning, so :func:`assemble_issue` and
    :func:`_unwritten` see only real ids and the item they would have
    summarised counts as unwritten.
    """
    if not isinstance(out, dict) or not isinstance(out.get("items"), list):
        return out
    items, unknown = resolve_ids(out["items"], alias_to_id)
    if unknown:
        log.warning("writer returned %d ids not in the featured set: %s",
                    len(unknown), unknown[:3])
    return {**out, "items": items}


def call_writer(
    today: str,
    featured: list[FeaturedItem],
    appendix_by_section: dict[str, list[AppendixItem]],
    metadata: dict,
    prev: str | None,
    rubric: str,
    *,
    save: bool = False,
) -> dict:
    """One writer call over ``featured``, with ids aliased in and resolved out.

    ``save`` keeps the raw response at ``logs/<slug>/write.response.json``
    (the daily run only; follow-ups and replays leave it alone).
    """
    aliased, alias_to_id = alias_items(featured)
    payload = build_writer_input(today, aliased, appendix_by_section, metadata, prev)
    raw = invoke_writer(build_writer_prompt(payload, rubric))
    if save:
        save_writer_response(raw)
    return resolve_writer_output(raw, alias_to_id)


# A writer that leaves out a few featured items is a flaky response; one that
# leaves out many is a broken one. Demote up to this share of the featured set
# to the appendix; above it, fail the stage (#587).
MAX_DEMOTED_FRACTION = 0.2


def _unwritten(featured: list[FeaturedItem], writer_output: dict) -> list[FeaturedItem]:
    """Featured items with no usable summary in `writer_output`.

    `writer_output` carries real ids (see :func:`resolve_writer_output`). Same
    matching :func:`assemble_issue` applies, so the two never disagree.
    """
    out = sanitize_writer_output(writer_output)
    have = {e["id"] for e in out["items"] if (e["summary"] or "").strip()}
    return [it for it in featured if it["id"] not in have]


def fill_missing_summaries(
    today: str, featured: list[FeaturedItem], writer_output: dict, rubric: str
) -> dict:
    """Ask the writer once more for only the featured items it left out.

    Returns `writer_output` with any recovered entries appended. A failed
    follow-up call is logged and ignored: the caller still has the demote path.
    Only the ids asked for are accepted, so the follow-up can never put a
    summary under the wrong item.
    """
    missing = _unwritten(featured, writer_output)
    if not missing:
        return writer_output
    log.warning("writer left out %d of %d featured items; asking again for just those",
                len(missing), len(featured))
    try:
        extra = call_writer(
            today, missing, {s.value: [] for s in Section},
            writer_metadata(missing, {}, None), None, rubric,
        )
    except (RuntimeError, ContentFilterError) as e:
        log.warning("follow-up writer call failed: %s", e)
        return writer_output
    # Only the ids asked for: a stray answer must never land on another item.
    wanted = {it["id"] for it in missing}
    recovered = [e for e in sanitize_writer_output(extra)["items"]
                 if e["id"] in wanted and (e["summary"] or "").strip()]
    base = sanitize_writer_output(writer_output)
    return {"theme": base["theme"], "items": base["items"] + recovered}


def demote_unwritten(
    conn,
    featured: list[FeaturedItem],
    appendix_by_section: dict[str, list[AppendixItem]],
    metadata: dict,
    writer_output: dict,
) -> tuple[list[FeaturedItem], dict[str, list[AppendixItem]], dict]:
    """Move featured items the writer still has no summary for to the appendix.

    Each is logged as a WARNING and moved in the DB too, so publish does not
    count it as featured. Nothing is demoted when more than
    ``MAX_DEMOTED_FRACTION`` of the featured set is missing: that points to a
    broken response, and :func:`assemble_issue` then fails the stage. A demoted
    item appears as title + link only, never with a made-up summary (#540).
    """
    missing = _unwritten(featured, writer_output)
    if not missing or len(missing) > MAX_DEMOTED_FRACTION * len(featured):
        return featured, appendix_by_section, metadata
    gone = {it["id"] for it in missing}
    for it in missing:
        log.warning("demoting featured item to appendix, writer gave no summary: %s %r",
                    it["id"], it["title"])
    demote_featured_to_appendix(conn, sorted(gone))
    kept = [it for it in featured if it["id"] not in gone]
    appendix = {sec: list(items) for sec, items in appendix_by_section.items()}
    for it in missing:
        appendix.setdefault(it["section"], []).append(AppendixItem(
            id=it["id"], section=it["section"], source=it["source"],
            url=it["url"], title=it["title"],
        ))
    for items in appendix.values():
        items.sort(key=lambda a: a["id"])
    return kept, appendix, writer_metadata(kept, appendix, metadata["items_considered"])


def assemble_issue(
    today: str,
    featured: list[FeaturedItem],
    appendix_by_section: dict[str, list[AppendixItem]],
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

    featured_for_fm: list[FrontmatterFeatured] = []
    missing_summaries: list[str] = []
    for item in featured:
        w = writer_items.get(item["id"], {})
        summary = (w.get("summary") or "").strip()
        if not summary:
            missing_summaries.append(item["id"])
            continue
        featured_for_fm.append(FrontmatterFeatured(
            id=item["id"],
            section=item["section"],
            source=item["source"],
            url=item["url"],
            title=item["title"],
            author=item.get("author"),
            score=int(item["score"]) if item.get("score") is not None else 0,
            tags=list(item.get("tags") or []),
            summary=summary,
            takeaway=w.get("takeaway") or None,
            open_question=w.get("open_question") or None,
        ))

    featured_ids = {item["id"] for item in featured}
    unknown_ids = [e["id"] for e in writer_output["items"] if e["id"] not in featured_ids]
    if unknown_ids:
        log.warning("writer returned %d ids not in the featured set: %s",
                    len(unknown_ids), unknown_ids[:3])

    if missing_summaries:
        # Publishing the title as the summary is how a mis-keyed writer response
        # shipped green; fail the stage so the run is red and re-runnable.
        raise WriterOutputError(
            f"writer missing summaries for {len(missing_summaries)} of "
            f"{len(featured)} featured items: {missing_summaries[:3]}; "
            f"returned ids not in featured set: {unknown_ids[:3]}"
        )

    appendix_for_fm: dict[str, list[AppendixItem]] = {s.value: [] for s in Section}
    for sec, items in appendix_by_section.items():
        for it in items:
            appendix_for_fm[sec].append(AppendixItem(
                id=it["id"],
                section=sec,
                source=it["source"],
                url=it["url"],
                title=it["title"],
            ))

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


def write_issue(conn, today: str, *, force: bool = False, log_prompt: bool = True) -> Path | None:
    """Write the issue file for ``today`` from ``conn``; return its path.

    The single writer core shared by :func:`main` (daily run, live DB) and
    ``backfill.run_writer_for_date`` (sandbox DB). Returns ``None`` without
    writing when the issue already exists (unless ``force``) or when there is
    nothing featured or in the appendix. ``log_prompt`` controls whether the
    rendered rubric is also written under ``logs/<slug>/`` (backfills skip it
    so a sandbox run leaves the live logs untouched).
    """
    out_path = issues_dir() / f"{today}.md"
    if out_path.exists() and not force:
        log.info(
            "write: skip — %s already exists (%d bytes); delete to rerun",
            out_path, out_path.stat().st_size,
        )
        return None

    featured, appendix_by_section, metadata = load_today_items(conn, today)

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
        return None

    prev = find_previous_newsletter(today)

    render = prompts.render_and_log if log_prompt else prompts.render
    rubric = render("write", edition.current(), template_path=PROMPT_PATH)

    if featured:
        writer_output = call_writer(
            today, featured, appendix_by_section, metadata, prev, rubric, save=True)
        writer_output = fill_missing_summaries(today, featured, writer_output, rubric)
        featured, appendix_by_section, metadata = demote_unwritten(
            conn, featured, appendix_by_section, metadata, writer_output)
    else:
        # Edge case: zero featured items but appendix non-empty. Skip the LLM
        # entirely; the issue page can still render an appendix-only digest.
        writer_output = {"theme": None, "items": []}

    issue_md = assemble_issue(today, featured, appendix_by_section, metadata, writer_output)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(issue_md)
    log.info("wrote %s (%d bytes)", out_path, len(issue_md))
    return out_path


def main(today: str | None = None) -> int:
    """Write the issue for ``today`` (``YYYY-MM-DD``; defaults to :func:`db.run_date`).

    ``today`` is injectable so tests and backfills pin the run date instead of
    setting ``RUN_DATE`` in the environment, matching fetch/prefilter/rank's
    ``main(now=...)``. The work itself lives in :func:`write_issue`.
    """
    init_db()
    if not PROMPT_PATH.exists():
        log.error("missing %s", PROMPT_PATH)
        return ExitCode.MISSING_INPUT

    if today is None:
        today = run_date()

    conn = connect()
    try:
        write_issue(conn, today)
    finally:
        conn.close()
    return ExitCode.OK


if __name__ == "__main__":
    sys.exit(main())
