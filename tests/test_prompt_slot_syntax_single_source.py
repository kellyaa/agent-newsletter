"""The prompt ``{{slot}}`` syntax lives only in scripts/prompts.py.

``new_edition`` once kept its own ``[a-z_]+`` copy that missed hyphenated and
numbered slots (``{{seed-sources}}``, ``{{example2}}``), leaving them raw in the
meta-prompt without any error. ``prompts.PLACEHOLDER_RE`` / ``fill_slots`` are
now the only definition; this test fails if another script re-adds one.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import prompts
from edition import EditionError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _regex_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and r"\{\{" in n.value
    ]


@pytest.mark.parametrize("path", sorted(SCRIPTS.glob("*.py")), ids=lambda p: p.name)
def test_no_other_script_defines_a_slot_regex(path):
    if path.name == "prompts.py":
        pytest.skip("the canonical definition")
    assert _regex_literals(path) == []


def test_canonical_definition_exists():
    assert len(_regex_literals(SCRIPTS / "prompts.py")) == 1


def test_fill_slots_round_trips_hyphen_and_digit_slots():
    template = "A {{seed-sources}} B {{example2}} C {{slug}} D {{slug}}"
    out = prompts.fill_slots(template, {"seed-sources": "S", "example2": "E", "slug": "x"})
    assert out == "A S B E C x D x"
    assert not prompts.PLACEHOLDER_RE.search(out)


def test_fill_slots_is_single_pass():
    assert prompts.fill_slots("{{a}} {{b}}", {"a": "{{b}}", "b": "B"}) == "{{b}} B"


def test_fill_slots_raises_on_missing_value():
    with pytest.raises(EditionError, match=r"\{\{example2\}\}"):
        prompts.fill_slots("{{slug}} {{example2}}", {"slug": "x"})


def test_new_edition_reports_a_missing_meta_prompt_value(tmp_path, monkeypatch):
    import new_edition

    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "draft_edition.md").write_text("{{slug}} {{brand-new}}")
    ex = tmp_path / "editions" / "agents"
    (ex / "prompts").mkdir(parents=True)
    (ex / "edition.yaml").write_text("x")
    (ex / "sources.yaml").write_text("y")
    with pytest.raises(new_edition.NewEditionError, match=r"brand-new"):
        new_edition.build_prompt("s", "N", "D", [], tmp_path)
