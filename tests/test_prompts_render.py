"""prompts.render(): base template + edition slots.

The regression guard for issue #300: rendering the `agents` edition must
reproduce the pre-extraction prompts/rank.md and prompts/write.md byte for byte
(tests/golden/*.md are verbatim copies of those files).
"""
from pathlib import Path

import pytest

import edition
import prompts

GOLDEN = Path(__file__).parent / "golden"


@pytest.mark.parametrize("stage", ["rank", "write"])
def test_agents_render_is_byte_identical_to_pre_extraction_prompt(stage):
    golden = (GOLDEN / f"{stage}.md").read_bytes()
    rendered = prompts.render(stage, edition.load("agents")).encode("utf-8")
    assert rendered == golden


@pytest.mark.parametrize("stage", ["rank", "write"])
def test_base_template_has_no_agent_specific_wording_left_in_slots(stage):
    template = (prompts.PROMPTS_DIR / f"{stage}.md").read_text()
    assert "{{" in template
    # every slot in the template is satisfied by the agents edition
    assert prompts.template_slots(template)


def test_render_and_log_writes_rendered_prompt_under_logs(tmp_path, monkeypatch):
    ed = edition.load("agents")
    text = prompts.render_and_log("rank", ed)
    out = ed.paths().logs / "rank.rendered.md"
    assert out.read_text() == text
    assert out.is_relative_to(tmp_path)  # conftest redirects LOGS_ROOT


def test_unknown_stage_rejected():
    with pytest.raises(edition.EditionError, match="unknown prompt stage"):
        prompts.render("publish", edition.load("agents"))


def _make_edition(tmp_path, monkeypatch, fragments: dict[str, str], template: str):
    """A throwaway edition on disk + a template file; returns (Edition, template_path)."""
    root = tmp_path / "editions" / "demo"
    (root / "prompts").mkdir(parents=True)
    (root / "edition.yaml").write_text(
        (edition.EDITIONS_DIR / "agents" / "edition.yaml").read_text().replace("slug: agents", "slug: demo")
    )
    for name, body in fragments.items():
        (root / "prompts" / name).write_text(body)
    monkeypatch.setattr(edition, "EDITIONS_DIR", tmp_path / "editions")
    tpl = tmp_path / "rank.md"
    tpl.write_text(template)
    return edition.load("demo", tmp_path / "editions"), tpl


def test_missing_slot_value_fails(tmp_path, monkeypatch):
    ed, tpl = _make_edition(tmp_path, monkeypatch, {}, "Hello {{nope}}")
    with pytest.raises(edition.EditionError, match=r"no value for slot \{\{nope\}\}"):
        prompts.render("rank", ed, tpl)


def test_unused_fragment_file_fails(tmp_path, monkeypatch):
    ed, tpl = _make_edition(
        tmp_path, monkeypatch, {"rank.typo.md": "x\n"}, "Hello {{name}}"
    )
    with pytest.raises(edition.EditionError, match=r"rank\.typo\.md.*not used"):
        prompts.render("rank", ed, tpl)


def test_fragment_and_scalar_for_same_slot_fails(tmp_path, monkeypatch):
    ed, tpl = _make_edition(
        tmp_path, monkeypatch, {"rank.name.md": "x\n"}, "Hello {{name}}"
    )
    with pytest.raises(edition.EditionError, match="defined both"):
        prompts.render("rank", ed, tpl)


def test_scalar_and_fragment_slots_fill_in_and_trailing_newline_is_stripped(tmp_path, monkeypatch):
    ed, tpl = _make_edition(
        tmp_path, monkeypatch, {"rank.extra.md": "line one\nline two\n"},
        "# {{name}}\n{{extra}}\nTags: {{tags}}\nTwice: {{name}}\n",
    )
    out = prompts.render("rank", ed, tpl)
    assert out.startswith("# AI Agents Daily\nline one\nline two\nTags: `frameworks`, `tool-use`")
    assert out.endswith("Twice: AI Agents Daily\n")


def test_values_are_not_re_expanded(tmp_path, monkeypatch):
    ed, tpl = _make_edition(
        tmp_path, monkeypatch, {"rank.extra.md": "see {{name}}\n"}, "{{extra}} / {{name}}"
    )
    assert prompts.render("rank", ed, tpl) == "see {{name}} / AI Agents Daily"


def test_edition_without_prompts_dir_renders_scalar_only_template(tmp_path, monkeypatch):
    ed, tpl = _make_edition(tmp_path, monkeypatch, {}, "{{name}}")
    import shutil
    shutil.rmtree(ed.paths().prompt_fragments)
    assert prompts.render("rank", ed, tpl) == "AI Agents Daily"


def test_render_defaults_to_the_current_edition_and_repo_template():
    assert prompts.render("write") == (GOLDEN / "write.md").read_text()
