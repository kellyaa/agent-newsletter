"""The issue filename contract (``<YYYY-MM-DD>.md``) lives in models.

write and backfill create ``editions/<slug>/issues/<date>.md``, publish and
replay_writer read it, reset_day deletes it, write.find_previous_newsletter
globs it, and the site's content collection globs ``<slug>/*.md``. Each used to
spell the name out inline. A one-sided change fails silently: publish reports
MISSING_INPUT, ``reset_day --force`` leaves the old issue in place, replay
cannot find the archive, or the writer loses its previous-issue context.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import edition
from models import ISSUE_GLOB, ISSUE_SUFFIX, ISSUES_DIRNAME, issue_filename

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# Every module that builds, reads, globs or deletes an issue file path.
CONSUMERS = ("write", "backfill", "publish", "replay_writer", "reset_day")


def test_values_unchanged():
    # Archived issues on the content branch and the deployed site use these.
    assert ISSUE_SUFFIX == ".md"
    assert ISSUE_GLOB == "*.md"
    assert issue_filename("2026-06-01") == "2026-06-01.md"


def test_glob_matches_filename():
    assert Path(issue_filename("2026-06-01")).match(ISSUE_GLOB)


def test_stem_is_the_date_and_sorts_by_day():
    # find_previous_newsletter compares stems to the run date.
    dates = ["2026-12-31", "2026-01-02", "2025-06-15"]
    names = sorted(issue_filename(d) for d in dates)
    assert [Path(n).stem for n in names] == sorted(dates)


def test_full_path_matches_layout(tmp_path):
    paths = edition.runtime_paths(tmp_path, "beta")
    assert paths.issues_dir / issue_filename("2026-06-01") == (
        tmp_path / "editions" / "beta" / ISSUES_DIRNAME / "2026-06-01.md"
    )


def _tree(mod: str) -> ast.Module:
    return ast.parse((SCRIPTS / f"{mod}.py").read_text())


def _inline_issue_names(tree: ast.Module) -> list[int]:
    """Lines with an f-string ending in ``.md`` or a ``"*.md"`` literal."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and node.values:
            last = node.values[-1]
            if isinstance(last, ast.Constant) and str(last.value).endswith(ISSUE_SUFFIX):
                hits.append(node.lineno)
        elif isinstance(node, ast.Constant) and node.value == ISSUE_GLOB:
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize("mod", CONSUMERS)
def test_no_inline_issue_filenames(mod):
    assert _inline_issue_names(_tree(mod)) == [], (
        f"{mod}.py spells the issue filename inline; use models.issue_filename / ISSUE_GLOB"
    )


@pytest.mark.parametrize("mod", CONSUMERS)
def test_consumers_use_issue_filename(mod):
    calls = [
        n for n in ast.walk(_tree(mod))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "issue_filename"
    ]
    assert calls, f"{mod}.py no longer builds the issue path via models.issue_filename"


def test_previous_issue_lookup_uses_issue_glob():
    src = (SCRIPTS / "write.py").read_text()
    assert "glob(ISSUE_GLOB)" in src


def test_detector_catches_regressions():
    # The guard must flag the patterns it exists to forbid.
    assert _inline_issue_names(ast.parse('p = d / f"{today}.md"')) == [1]
    assert _inline_issue_names(ast.parse('d.glob("*.md")')) == [1]
    assert _inline_issue_names(ast.parse('p = d / issue_filename(today)')) == []
    assert _inline_issue_names(ast.parse('PROMPT = "write.md"')) == []


def test_site_collection_globs_the_same_suffix():
    # The Astro loader picks up <slug>/<ISSUE_GLOB> from the staged issues tree.
    cfg = (ROOT / "site" / "src" / "content.config.ts").read_text()
    assert f"${{e.slug}}/{ISSUE_GLOB}" in cfg
