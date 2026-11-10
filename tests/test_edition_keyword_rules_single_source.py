"""edition.yaml keyword, tag and slug rules live once, in edition.py.

new_edition.py writes edition.yaml files that edition.load must accept. It used
to carry its own slug regex and a weaker keyword check, so an LLM keyword such
as ``x?`` (matches the empty string) was written out and then refused by load.
"""
from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest
import yaml

import edition
import new_edition

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

BAD_KEYWORDS = ["x?", ".*", "(foo)?", "(", "(?i)agent", "(?P<n>c)", "", "  "]
GOOD_KEYWORDS = ["agent", r"\bllm\b", "rag", "(?P<n>b)"]


def test_new_edition_defines_no_rules_of_its_own():
    tree = ast.parse((SCRIPTS / "new_edition.py").read_text())
    names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}
    assert not {"_SLUG_RE", "SLUG_RE", "_TAG_RE", "TAG_RE"} & names
    compiles = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "compile"
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "re"]
    # Only the {{slot}} placeholder pattern may be compiled here.
    assert len(compiles) == 1


def test_keyword_problem():
    assert edition.keyword_problem("agent") is None
    assert "does not compile" in edition.keyword_problem("(")
    assert "empty string" in edition.keyword_problem("x?")


def test_combined_keywords_problem():
    assert edition.combined_keywords_problem(["a", "b"]) is None
    assert "combined" in edition.combined_keywords_problem(["a", "(?i)b"])


@pytest.mark.parametrize("bad", BAD_KEYWORDS)
def test_clean_keywords_drops_what_load_rejects(bad):
    kept, dropped = new_edition.clean_keywords([*GOOD_KEYWORDS, bad])
    assert bad not in kept
    assert [d.what for d in dropped] == [bad]


def test_scaffold_output_round_trips_through_load(tmp_path, monkeypatch):
    d = tmp_path / "editions"
    shutil.copytree(edition.EDITIONS_DIR, d)
    monkeypatch.setattr(edition, "EDITIONS_DIR", d)
    kept, _ = new_edition.clean_keywords(BAD_KEYWORDS + GOOD_KEYWORDS)
    assert kept
    v = new_edition.Validated("t", "r", [], kept, ["tag-a"], {}, {}, [])
    sections = yaml.safe_load((d / "_template" / "edition.yaml").read_text())["sections"]
    dest = d / "round-trip"
    shutil.copytree(d / "_template", dest)
    (dest / "edition.yaml").write_text(
        new_edition.render_edition_yaml("round-trip", "Round Trip", 999, v, sections))
    assert edition.load("round-trip", d).keywords == tuple(kept)
