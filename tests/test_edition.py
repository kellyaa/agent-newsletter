"""edition.py: loading, strict validation, current()."""
import copy
from pathlib import Path

import pytest
import yaml

import edition
from edition import EditionError

AGENTS_YAML = edition.EDITIONS_DIR / "agents" / "edition.yaml"


@pytest.fixture()
def base():
    return yaml.safe_load(AGENTS_YAML.read_text())


@pytest.fixture()
def write_edition(tmp_path):
    def _write(data, folder="agents"):
        d = tmp_path / folder
        d.mkdir(exist_ok=True)
        (d / "edition.yaml").write_text(yaml.safe_dump(data) if not isinstance(data, str) else data)
        return tmp_path
    return _write


def _load(editions_dir, slug="agents"):
    return edition.load(slug, editions_dir)


def test_agents_edition_loads_and_is_frozen():
    ed = edition.load("agents")
    assert ed.slug == "agents"
    with pytest.raises(Exception):
        ed.slug = "other"  # frozen dataclass


def test_section_rules_returns_copies():
    ed = edition.load("agents")
    ed.section_rules()["papers"]["cap"] = 999
    assert ed.section_rules()["papers"]["cap"] == 5


def test_paths_point_into_the_edition_folder():
    p = edition.load("agents").paths()
    assert p.root == edition.EDITIONS_DIR / "agents"
    assert p.sources.is_file()
    assert p.prompt_fragments.is_dir()


def test_runtime_paths_are_legacy_layout_for_now(tmp_path):
    rp = edition.runtime_paths(tmp_path, "agents")
    assert rp.state_db == tmp_path / "state.db"
    assert rp.issues_dir == tmp_path / "site" / "src" / "content" / "issues"
    assert rp.candidates.name == "candidates.json"


def test_runtime_paths_reject_bad_slug(tmp_path):
    with pytest.raises(EditionError, match="invalid edition slug"):
        edition.runtime_paths(tmp_path, "../etc")


def test_unknown_edition(tmp_path):
    with pytest.raises(EditionError, match="unknown edition 'nope'"):
        _load(tmp_path, "nope")


def test_invalid_yaml(write_edition):
    d = write_edition("slug: [unclosed\n")
    with pytest.raises(EditionError, match="invalid YAML"):
        _load(d)


def test_top_level_must_be_mapping(write_edition):
    with pytest.raises(EditionError, match="top level must be a mapping"):
        _load(write_edition("- a\n- b\n"))


def test_unknown_key_rejected(write_edition, base):
    base["surprise"] = 1
    with pytest.raises(EditionError, match=r"unknown keys \['surprise'\]"):
        _load(write_edition(base))


def test_missing_key_rejected(write_edition, base):
    del base["tagline"]
    with pytest.raises(EditionError, match=r"missing keys \['tagline'\]"):
        _load(write_edition(base))


@pytest.mark.parametrize("key", ["slug", "name", "tagline", "reader_profile"])
def test_string_fields_must_be_nonempty(write_edition, base, key):
    base[key] = "  "
    with pytest.raises(EditionError, match=f"'{key}' must be a non-empty string"):
        _load(write_edition(base))


@pytest.mark.parametrize("slug", ["Agents", "a_b", "-a", "a--b", "a b"])
def test_slug_format(write_edition, base, slug):
    base["slug"] = slug
    with pytest.raises(EditionError, match=r"must match \[a-z0-9-\]\+"):
        _load(write_edition(base))


def test_slug_must_match_folder(write_edition, base):
    base["slug"] = "other"
    with pytest.raises(EditionError, match="does not match folder name 'agents'"):
        _load(write_edition(base))


def test_status_must_be_known(write_edition, base):
    base["status"] = "retired"
    with pytest.raises(EditionError, match="status 'retired'"):
        _load(write_edition(base))


@pytest.mark.parametrize("status", ["draft", "live", "paused"])
def test_all_statuses_accepted(write_edition, base, status):
    base["status"] = status
    assert _load(write_edition(base)).status == status


@pytest.mark.parametrize("order", ["10", 1.5, True])
def test_order_must_be_int(write_edition, base, order):
    base["order"] = order
    with pytest.raises(EditionError, match="'order' must be an integer"):
        _load(write_edition(base))


def test_keyword_must_compile(write_edition, base):
    base["keywords"] = [r"\bagent\b", "(unclosed"]
    with pytest.raises(EditionError, match=r"keyword '\(unclosed' does not compile"):
        _load(write_edition(base))


def test_keywords_must_be_nonempty_list_of_strings(write_edition, base):
    base["keywords"] = []
    with pytest.raises(EditionError, match="'keywords' must not be empty"):
        _load(write_edition(base))
    base["keywords"] = "agent"
    with pytest.raises(EditionError, match="list of non-empty strings"):
        _load(write_edition(base))


@pytest.mark.parametrize("tag", ["Tool_Use", "tool use", "-x", "x-", "a--b"])
def test_tags_must_be_kebab_case(write_edition, base, tag):
    base["tags"] = ["ok", tag]
    with pytest.raises(EditionError, match="must be kebab-case"):
        _load(write_edition(base))


def test_tags_must_be_unique_and_nonempty(write_edition, base):
    base["tags"] = ["a", "a"]
    with pytest.raises(EditionError, match="duplicates"):
        _load(write_edition(base))
    base["tags"] = []
    with pytest.raises(EditionError, match="'tags' must not be empty"):
        _load(write_edition(base))


def test_sections_must_be_a_mapping(write_edition, base):
    base["sections"] = [1]
    with pytest.raises(EditionError, match="'sections' must be a mapping"):
        _load(write_edition(base))


@pytest.mark.parametrize("drop,add", [("blogs", None), (None, "extras"), ("news", "extras")])
def test_sections_must_be_exactly_papers_news_blogs(write_edition, base, drop, add):
    if drop:
        del base["sections"][drop]
    if add:
        base["sections"][add] = {"featured_min": 6, "appendix_min": 4, "cap": 6}
    with pytest.raises(EditionError, match="exactly"):
        _load(write_edition(base))


def test_section_must_be_a_mapping(write_edition, base):
    base["sections"]["news"] = 6
    with pytest.raises(EditionError, match=r"sections\.news must be a mapping"):
        _load(write_edition(base))


def test_appendix_min_may_not_exceed_featured_min(write_edition, base):
    base["sections"]["news"]["appendix_min"] = 7
    with pytest.raises(EditionError, match=r"appendix_min \(7\) must be <= featured_min \(6\)"):
        _load(write_edition(base))


def test_appendix_min_equal_to_featured_min_is_allowed(write_edition, base):
    base["sections"]["news"]["appendix_min"] = 6
    assert _load(write_edition(base)).sections["news"]["appendix_min"] == 6


def test_unknown_section_key_rejected(write_edition, base):
    base["sections"]["news"]["floor"] = 1
    with pytest.raises(EditionError, match=r"sections\.news: unknown keys \['floor'\]"):
        _load(write_edition(base))


def test_missing_section_key_rejected(write_edition, base):
    del base["sections"]["news"]["cap"]
    with pytest.raises(EditionError, match=r"sections\.news: missing keys \['cap'\]"):
        _load(write_edition(base))


def test_burst_keys_must_come_together(write_edition, base):
    del base["sections"]["papers"]["burst_cap"]
    with pytest.raises(EditionError, match="burst_.* must be given together"):
        _load(write_edition(base))


@pytest.mark.parametrize("value", [-1, "5", 2.5, True])
def test_section_values_must_be_nonnegative_ints(write_edition, base, value):
    base["sections"]["news"]["cap"] = value
    with pytest.raises(EditionError, match=r"sections\.news\.cap must be a non-negative integer"):
        _load(write_edition(base))


def test_downweighted_is_optional_but_validated(write_edition, base):
    base.pop("downweighted", None)
    assert _load(write_edition(base)).downweighted == ()
    base["downweighted"] = [1]
    with pytest.raises(EditionError, match="'downweighted' must be a list"):
        _load(write_edition(base))


# --- current() ---------------------------------------------------------------

def test_current_raises_when_edition_unset(monkeypatch):
    monkeypatch.delenv("EDITION", raising=False)
    with pytest.raises(EditionError, match="EDITION is not set"):
        edition.current()


def test_current_raises_when_edition_blank(monkeypatch):
    monkeypatch.setenv("EDITION", "  ")
    with pytest.raises(EditionError, match="EDITION is not set"):
        edition.current()


def test_current_has_no_default(monkeypatch):
    monkeypatch.delenv("EDITION", raising=False)
    with pytest.raises(EditionError):
        edition.selected_slug()


def test_current_loads_the_selected_edition(monkeypatch):
    monkeypatch.setenv("EDITION", "agents")
    assert edition.current().slug == "agents"


def test_current_unknown_edition_raises(monkeypatch):
    monkeypatch.setenv("EDITION", "does-not-exist")
    with pytest.raises(EditionError, match="unknown edition"):
        edition.current()


def test_stage_modules_refuse_to_import_without_edition(monkeypatch):
    """A stage must fail before doing any work when no edition is selected."""
    import subprocess, sys
    env = {k: v for k, v in __import__("os").environ.items() if k != "EDITION"}
    for mod in ("rank", "write", "prefilter", "fetch"):
        r = subprocess.run(
            [sys.executable, "-c", f"import sys; sys.path.insert(0, 'scripts'); import {mod}"],
            cwd=edition.REPO_ROOT, env=env, capture_output=True, text=True,
        )
        assert r.returncode != 0, mod
        assert "EDITION is not set" in r.stderr, mod
