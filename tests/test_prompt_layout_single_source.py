"""The prompt layout has one home: ``scripts/prompts.py``.

Base template paths, the fragment folder name, fragment file names and which
slots need a fragment used to be spelled in six modules. ``prefilter`` hashed
its own ``REPO_ROOT / "prompts" / "rank.md"`` to decide whether cached paper
scores are stale, while ``rank`` rendered a separate literal. If one of them
moved, ``_rubric_hash()`` returned ``""`` and invalidation turned off with no
error. ``new_edition.FRAGMENTS`` was a hand-kept list, so a slot added to a
base template was never asked of the LLM and the scaffold wrote an edition
``check_edition`` then rejected. These tests pin:

* the rank template prefilter hashes is the one rank renders;
* ``new_edition.FRAGMENTS`` is exactly the templates' fragment slots;
* no script but ``prompts.py`` spells a template file name or joins
  ``/ "prompts"`` itself.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import edition
import new_edition
import prefilter
import prompts
import rank
import replay_writer
import write
from models import EDITIONS_DIRNAME, LEGACY_EDITION, PROMPTS_DIRNAME

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TEMPLATE_NAMES = {f"{s}.md" for s in prompts.STAGES} | {prompts.DRAFT_EDITION_TEMPLATE.name}


def test_rubric_hash_reads_the_template_rank_renders():
    assert prefilter.RUBRIC_PATH == rank.PROMPT_PATH == prompts.template_path("rank")
    assert prefilter.RUBRIC_PATH.is_file()


def test_writer_template_is_shared_with_replay_writer():
    assert write.PROMPT_PATH == replay_writer.PROMPT_PATH == prompts.template_path("write")
    assert write.PROMPT_PATH.is_file()


def test_template_paths_live_under_the_prompts_dir():
    for stage in prompts.STAGES:
        assert prompts.template_path(stage) == prompts.PROMPTS_DIR / f"{stage}{prompts.TEMPLATE_SUFFIX}"
    assert prompts.PROMPTS_DIR == edition.REPO_ROOT / PROMPTS_DIRNAME
    assert prompts.DRAFT_EDITION_TEMPLATE.is_file()


def test_unknown_stage_has_no_template():
    with pytest.raises(edition.EditionError, match="unknown prompt stage"):
        prompts.template_path("publish")


def test_fragment_dir_is_the_shared_name():
    ed = edition.load(LEGACY_EDITION)
    assert ed.paths().prompt_fragments == ed.paths().root / PROMPTS_DIRNAME


def test_scalar_slots_match_what_edition_yaml_fills():
    ed = edition.load(LEGACY_EDITION)
    assert set(prompts.SCALAR_SLOTS) == set(prompts._scalar_slots(ed))


@pytest.mark.parametrize("stage", prompts.STAGES)
def test_every_fragment_slot_has_a_fragment_in_every_real_edition(stage):
    real = [ed for _slug, ed, _err in edition.discover() if ed is not None]
    assert real, "no loadable edition found"
    for ed in real:
        frag_dir = ed.paths().prompt_fragments
        slug = ed.slug
        for slot in prompts.fragment_slots(stage):
            assert (frag_dir / prompts.fragment_filename(stage, slot)).is_file(), (slug, stage, slot)


def test_new_edition_asks_for_exactly_the_fragment_slots():
    expected = {prompts.fragment_filename(st, sl) for st in prompts.STAGES for sl in prompts.fragment_slots(st)}
    assert set(new_edition.FRAGMENTS.values()) == expected
    assert len(new_edition.FRAGMENTS) == len(expected)


def test_new_edition_fragments_follow_a_new_template_slot(monkeypatch, tmp_path):
    """Adding a slot to a base template is all it takes to scaffold it."""
    for stage in prompts.STAGES:
        text = prompts.template_path(stage).read_text()
        (tmp_path / f"{stage}.md").write_text(text + ("\n{{extra-bit}}\n" if stage == "rank" else ""))
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    frags = new_edition._fragment_map()
    assert frags["rank_extra_bit"] == "rank.extra-bit.md"
    assert set(frags) - {"rank_extra_bit"} == set(new_edition.FRAGMENTS)


def test_fragment_slots_skip_scalar_slots():
    assert prompts.fragment_slots("rank", "{{name}} {{intro}} {{tags}} {{intro}} {{x}}") == ["intro", "x"]


def test_new_edition_reference_edition_is_the_legacy_one():
    assert new_edition.REFERENCE_EDITION == LEGACY_EDITION
    out = new_edition.build_prompt("s", "N", "D", [], edition.REPO_ROOT)
    assert f"### {EDITIONS_DIRNAME}/{LEGACY_EDITION}/{PROMPTS_DIRNAME}/rank.intro.md" in out


def _strings(path: Path) -> list[tuple[int, str]]:
    """(line, value) of every string constant, docstrings excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


OTHER_SCRIPTS = sorted(set(SCRIPTS.glob("*.py")) - {SCRIPTS / "prompts.py"})


@pytest.mark.parametrize("path", OTHER_SCRIPTS, ids=lambda p: p.name)
def test_no_template_filename_outside_prompts(path):
    hits = [(ln, v) for ln, v in _strings(path) if any(v.endswith(n) for n in TEMPLATE_NAMES)]
    assert hits == [], f"{path.name}: use prompts.template_path / DRAFT_EDITION_TEMPLATE: {hits}"


@pytest.mark.parametrize("path", OTHER_SCRIPTS, ids=lambda p: p.name)
def test_no_prompts_dir_path_join_outside_prompts(path):
    """``x / "prompts"`` is the fragment or template folder; use the shared name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    joins = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
             and isinstance(n.right, ast.Constant) and n.right.value == PROMPTS_DIRNAME]
    assert joins == [], f"{path.name}:{joins} use models.PROMPTS_DIRNAME / Edition.paths().prompt_fragments"


@pytest.mark.parametrize("path", OTHER_SCRIPTS, ids=lambda p: p.name)
def test_no_fragment_filename_fstring_outside_prompts(path):
    """``f"{stage}.{slot}.md"`` and ``"rank.<slot>.md"`` literals belong to prompts.fragment_filename."""
    hits = [(ln, v) for ln, v in _strings(path)
            if any(v.startswith(f"{s}.") and v.endswith(prompts.TEMPLATE_SUFFIX) for s in prompts.STAGES)]
    assert hits == [], f"{path.name}: use prompts.fragment_filename: {hits}"
