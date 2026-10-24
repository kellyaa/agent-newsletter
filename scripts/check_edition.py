"""Lint an edition and dry-run its fetch + prefilter gates. No LLM calls.

    uv run scripts/check_edition.py <slug>

1. **Lint** (offline): ``editions/<slug>/edition.yaml`` validates, both base
   prompts render with the edition's fragments, ``sources.yaml`` is well-formed,
   and no ``TODO`` placeholder from ``editions/_template/`` is left (an error
   for a ``live`` edition, a warning otherwise).
2. **Dry run** (network): every configured source is fetched into a throwaway
   SQLite DB under ``/tmp`` and the cheap prefilter gates are applied, printing
   per-source counts: fetched -> recency -> keyword gate -> section. Tuning an
   edition's keyword gate goes much faster with these numbers. Nothing is
   written to the real state DB and the LLM is never called.

Exit status is 1 when lint fails, 0 otherwise (a source that fails to fetch is
reported in the table but does not fail the check; networks are flaky).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

import edition as edition_mod
import prompts as prompts_mod
from edition import Edition, EditionError

FAMILIES = ("rss", "arxiv", "hn", "reddit", "html", "github_releases")
REQUIRED_KEYS = {
    "rss": {"id", "url"},
    "arxiv": {"id", "query"},
    "hn": {"id", "query"},
    "reddit": {"id", "url"},
    "html": {"id", "url"},
    "github_releases": {"owner", "repo"},
}
SECTIONS = ("papers", "news", "blogs")
_TODO = re.compile(r"\bTODO\b")
_UNFILLED_SLOT = re.compile(r"\{\{[a-z][a-z0-9_-]*\}\}")


@dataclass
class LintResult:
    edition: Edition | None = None
    sources: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class SourceCount:
    label: str
    fetched: int = 0
    after_recency: int = 0
    after_keyword: int = 0
    sections: Counter = field(default_factory=Counter)
    error: str | None = None


# ---------------------------------------------------------------- lint

def _load_sources(path: Path, result: LintResult) -> dict:
    if not path.is_file():
        result.errors.append(f"{path}: missing")
        return {}
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        result.errors.append(f"{path}: invalid YAML: {e}")
        return {}
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        result.errors.append(f"{path}: top level must be a mapping of source families")
        return {}
    return raw


def _check_sources(sources: dict, result: LintResult) -> None:
    unknown = sorted(set(map(str, sources)) - set(FAMILIES))
    if unknown:
        result.errors.append(f"sources.yaml: unknown source families {unknown} (allowed: {list(FAMILIES)})")
    active = 0
    for family in FAMILIES:
        entries = sources.get(family)
        if entries is None:
            continue
        if not isinstance(entries, list):
            result.errors.append(f"sources.yaml: {family!r} must be a list")
            continue
        seen_ids: set[str] = set()
        for i, entry in enumerate(entries):
            where = f"sources.yaml: {family}[{i}]"
            if not isinstance(entry, dict):
                result.errors.append(f"{where} must be a mapping")
                continue
            active += 1
            missing = sorted(REQUIRED_KEYS[family] - set(entry))
            if missing:
                result.errors.append(f"{where} is missing {missing}")
            sid = entry.get("id")
            if sid is not None:
                if not isinstance(sid, str) or not sid.strip():
                    result.errors.append(f"{where}: id must be a non-empty string")
                elif sid in seen_ids:
                    result.errors.append(f"{where}: duplicate id {sid!r} in {family}")
                else:
                    seen_ids.add(sid)
            if "section" in entry and entry["section"] not in SECTIONS:
                result.errors.append(f"{where}: section {entry['section']!r} must be one of {list(SECTIONS)}")
            if "keyword_gate_bypass" in entry and not isinstance(entry["keyword_gate_bypass"], bool):
                result.errors.append(f"{where}: keyword_gate_bypass must be true or false")
            if "recency_days" in entry:
                rd = entry["recency_days"]
                if isinstance(rd, bool) or not isinstance(rd, int) or rd <= 0:
                    result.errors.append(f"{where}: recency_days must be a positive integer")
    if active == 0:
        msg = "sources.yaml has no active sources, so the edition cannot produce an issue yet"
        (result.errors if _is_live(result) else result.warnings).append(msg)


def _is_live(result: LintResult) -> bool:
    return result.edition is not None and result.edition.status == "live"


def _scan_todos(root: Path) -> list[str]:
    """Files under *root* still holding a ``TODO`` placeholder from the template."""
    hits: list[str] = []
    files = [root / "edition.yaml", root / "sources.yaml", *sorted((root / "prompts").glob("*.md"))]
    for path in files:
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if path.suffix == ".yaml" and line.lstrip().startswith("#"):
                continue
            if _TODO.search(line):
                hits.append(f"{path.relative_to(root)}:{n}")
                break
    return hits


def lint(slug: str) -> LintResult:
    result = LintResult()
    try:
        ed = edition_mod.load(slug)
    except EditionError as e:
        result.errors.append(str(e))
        return result
    result.edition = ed

    for stage in prompts_mod.STAGES:
        try:
            text = prompts_mod.render(stage, ed)
        except EditionError as e:
            result.errors.append(str(e))
            continue
        left = _UNFILLED_SLOT.findall(text)
        if left:
            result.errors.append(f"{stage} prompt still contains unfilled placeholders {sorted(set(left))}")

    result.sources = _load_sources(ed.paths().sources, result)
    _check_sources(result.sources, result)

    todos = _scan_todos(ed.paths().root)
    if todos:
        msg = f"unfinished TODO placeholders from the template in: {', '.join(todos)}"
        (result.errors if ed.status == "live" else result.warnings).append(msg)
    return result


# ------------------------------------------------------------- dry run

def _rows_for(conn: sqlite3.Connection, ids: list[str]) -> list[dict]:
    rows: list[dict] = []
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        marks = ",".join("?" * len(chunk))
        rows.extend(
            dict(r) for r in conn.execute(
                f"""SELECT id, source, url, title, published_at, fetched_at, raw_text,
                           section_override, keyword_gate_bypass, recency_days_override
                    FROM items WHERE id IN ({marks})""",
                chunk,
            )
        )
    return rows


def dry_run(ed: Edition, sources: dict) -> list[SourceCount]:
    """Fetch every source into a throwaway DB and apply the recency + keyword gates."""
    # The stage modules read their edition at import time, so select this one
    # before importing them. Only the functions are used below, never their
    # module-level edition constants.
    previous = os.environ.get("EDITION")
    os.environ["EDITION"] = ed.slug
    try:
        import db
        import fetch
        import prefilter
    finally:
        if previous is None:
            del os.environ["EDITION"]
        else:
            os.environ["EDITION"] = previous

    keyword_re = re.compile("|".join(ed.keyword_regexes()), re.IGNORECASE)
    now = datetime.now(timezone.utc)
    tmp = Path(tempfile.mkdtemp(prefix=f"check-edition-{ed.slug}-"))
    counts: list[SourceCount] = []
    try:
        db_path = tmp / "state.db"
        db.init_db(db_path)
        conn = db.connect(db_path)
        try:
            with fetch._make_http_client() as client:
                walker = fetch.iter_collectors(sources, client)
                while True:
                    try:
                        label, gen = next(walker)
                    except StopIteration:
                        break
                    count = SourceCount(label)
                    counts.append(count)
                    try:
                        items = list(gen)
                    except Exception as e:  # per-source isolation, like fetch.main
                        count.error = f"{type(e).__name__}: {e}"[:120]
                        continue
                    fetch.upsert_items(conn, items)
                    ids = [db.url_id(it.url) for it in items if db.is_allowed_url(it.url)]
                    rows = _rows_for(conn, ids)
                    count.fetched = len(rows)
                    kept = [r for r in rows if prefilter._passes_recency(r, now)]
                    count.after_recency = len(kept)
                    kept = [r for r in kept if prefilter.keyword_gate(r, keyword_re)]
                    count.after_keyword = len(kept)
                    for r in kept:
                        count.sections[prefilter.assign_section(r["source"], r["section_override"])] += 1
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return counts


def format_table(counts: list[SourceCount]) -> str:
    width = max([len("source"), *(len(c.label) for c in counts)])
    head = f"{'source':<{width}}  {'fetched':>7}  {'recency':>7}  {'keyword':>7}  section"
    lines = [head, "-" * len(head)]
    for c in counts:
        if c.error:
            lines.append(f"{c.label:<{width}}  ERROR: {c.error}")
            continue
        sec = ", ".join(f"{s}:{n}" for s, n in sorted(c.sections.items())) or "-"
        lines.append(
            f"{c.label:<{width}}  {c.fetched:>7}  {c.after_recency:>7}  {c.after_keyword:>7}  {sec}"
        )
    ok = [c for c in counts if not c.error]
    total = Counter()
    for c in ok:
        total.update(c.sections)
    lines.append("-" * len(head))
    lines.append(
        f"{'total':<{width}}  {sum(c.fetched for c in ok):>7}  "
        f"{sum(c.after_recency for c in ok):>7}  {sum(c.after_keyword for c in ok):>7}  "
        + (", ".join(f"{s}:{n}" for s, n in sorted(total.items())) or "-")
    )
    failed = [c.label for c in counts if c.error]
    if failed:
        lines.append(f"{len(failed)} source(s) failed to fetch: {', '.join(failed)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def check(slug: str, out=None) -> int:
    """Lint + dry run for *slug*, printing a report to *out*. Returns the exit status."""
    out = out or sys.stdout
    result = lint(slug)
    print(f"== lint: editions/{slug}", file=out)
    for w in result.warnings:
        print(f"WARN  {w}", file=out)
    for e in result.errors:
        print(f"ERROR {e}", file=out)
    if not result.ok:
        print("lint FAILED; skipping the dry run", file=out)
        return 1
    print("lint ok" + (f" ({len(result.warnings)} warning(s))" if result.warnings else ""), file=out)

    print("\n== dry run: fetch -> recency -> keyword gate -> section (no LLM)", file=out)
    assert result.edition is not None
    counts = dry_run(result.edition, result.sources)
    if counts:
        print(format_table(counts), file=out)
    else:
        print("no sources configured; nothing to fetch", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug", help="edition folder name under editions/")
    args = ap.parse_args(argv)
    return check(args.slug)


if __name__ == "__main__":
    sys.exit(main())
