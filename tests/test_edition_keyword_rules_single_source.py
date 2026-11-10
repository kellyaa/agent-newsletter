"""edition.yaml keyword/slug/tag rules live only in scripts/edition.py.

``edition.load`` is the reader; ``new_edition.py`` is a writer that drafts
edition.yaml from LLM output. Before this guard, new_edition kept its own
copy of the slug regex and its own keyword check (``re.compile`` only). That
copy had drifted: it kept keywords such as ``x?`` (matches the empty string)
and ``(?i)llm`` / a reused ``(?P<g>..)`` group (break the OR-ed prefilter
pattern), so the scaffold wrote an edition.yaml that ``edition.load`` and the
scaffold's own closing ``check_edition`` run then rejected.

This test fails if new_edition.py defines its own slug/tag regex or compiles
keywords itself. It also checks the round trip: whatever ``clean_keywords``
keeps must load through ``render_edition_yaml`` -> ``edition.load``.
"""
from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest
import yaml

import edition
import new_edition

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
WRITER = SCRIPTS / "new_edition.py"
RULE_PATTERN = r"[a-z0-9]+(-[a-z0-9]+)*"

# Every keyword edition.load rejects, plus good ones around them.
CORPUS = [
    r"\bagent(s)?\b",
    "(",                 # does not compile
    "x?",                # matches the empty string
    ".*",                # matches the empty string
    "(foo)?",            # matches the empty string
    "",                  # empty pattern
    r"\bagent(s)?\b",    # duplicate
    "(?P<g>llm)",
    "(?P<g>rag)",        # reuses a named group: breaks the combined pattern
    "(?i)mcp",           # global flag not at the start once OR-ed
    r"\bcode review\b",
]


def _tree() -> ast.Module:
    return ast.parse(WRITER.read_text(encoding="utf-8"))


def test_new_edition_does_not_copy_the_slug_or_tag_regex():
    consts = [n.value for n in ast.walk(_tree()) if isinstance(n, ast.Constant)]
    assert RULE_PATTERN not in consts, "use edition.SLUG_RE / edition.TAG_RE"


def test_new_edition_does_not_compile_keywords_itself():
    calls = [
        n for n in ast.walk(_tree())
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "compile" and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "re" and not (n.args and isinstance(n.args[0], ast.Constant))
    ]
    assert not calls, "keyword checks belong in edition.keyword_problem / combined_keywords_problem"


def test_new_edition_uses_the_edition_rules():
    src = WRITER.read_text(encoding="utf-8")
    for name in ("edition_mod.keyword_problem", "edition_mod.combined_keywords_problem",
                 "edition_mod.SLUG_RE"):
        assert name in src


def test_the_guard_matches_the_rule_it_protects():
    assert edition.SLUG_RE.pattern == RULE_PATTERN
    assert edition.TAG_RE.pattern == RULE_PATTERN


@pytest.mark.parametrize("kw", ["(", "x?", ".*", "(foo)?"])
def test_every_individually_rejected_keyword_has_a_reason(kw):
    assert edition.keyword_problem(kw)


def test_good_keywords_have_no_problem():
    assert edition.keyword_problem(r"\bagent\b") is None
    assert edition.combined_keywords_problem([r"\bagent\b", "(?P<g>x)"]) is None
    assert edition.combined_keywords_problem(["(?P<g>x)", "(?P<g>y)"])


def test_whatever_clean_keywords_keeps_loads(tmp_path):
    kept, dropped = new_edition.clean_keywords(CORPUS)
    assert kept == [r"\bagent(s)?\b", "(?P<g>llm)", r"\bcode review\b"]
    assert len(dropped) == len(CORPUS) - len(kept)

    editions_dir = tmp_path / "editions"
    shutil.copytree(edition.EDITIONS_DIR / "_template", editions_dir / "demo")
    sections = yaml.safe_load((editions_dir / "demo" / "edition.yaml").read_text())["sections"]
    v = new_edition.Validated("tagline", "profile", [], kept, ["ai"], {}, {}, [])
    (editions_dir / "demo" / "edition.yaml").write_text(
        new_edition.render_edition_yaml("demo", "Demo", 10, v, sections)
    )
    assert edition.load("demo", editions_dir).keywords == tuple(kept)

