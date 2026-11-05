"""edition.py: accessor behaviour and validation boundaries not pinned elsewhere.

Covers Edition.keyword_re / keyword_regexes / valid_tags, reader_profile
normalisation, the appendix_min == featured_min and zero-value boundaries, and
discover()'s tie-breaking order.
"""
import pytest
import yaml

import edition
from edition import EditionError

AGENTS_YAML = edition.EDITIONS_DIR / "agents" / "edition.yaml"


@pytest.fixture()
def base():
    return yaml.safe_load(AGENTS_YAML.read_text())


def _write(tmp_path, data, folder="agents"):
    d = tmp_path / folder
    d.mkdir(exist_ok=True)
    (d / "edition.yaml").write_text(yaml.safe_dump(data))
    return tmp_path


def _load(tmp_path, data, slug="agents"):
    return edition.load(slug, _write(tmp_path, data, slug))


def test_keyword_re_is_case_insensitive_alternation(tmp_path, base):
    base["keywords"] = ["agent", r"tool[- ]use"]
    ed = _load(tmp_path, base)
    pat = ed.keyword_re()
    assert pat.pattern == r"agent|tool[- ]use"
    assert pat.search("Building AGENTS at scale")
    assert pat.search("Tool-Use patterns")
    assert not pat.search("a recipe for sourdough")


def test_keyword_regexes_returns_a_fresh_list(tmp_path, base):
    base["keywords"] = ["agent", "mcp"]
    ed = _load(tmp_path, base)
    got = ed.keyword_regexes()
    assert got == ["agent", "mcp"]
    got.append("mutated")
    assert ed.keyword_regexes() == ["agent", "mcp"]
    assert ed.keywords == ("agent", "mcp")


def test_valid_tags_is_a_set_of_the_configured_tags(tmp_path, base):
    base["tags"] = ["alpha", "beta-2"]
    ed = _load(tmp_path, base)
    assert ed.valid_tags() == {"alpha", "beta-2"}
    ed.valid_tags().add("x")
    assert ed.valid_tags() == {"alpha", "beta-2"}


def test_reader_profile_is_stripped(tmp_path, base):
    base["reader_profile"] = "\n  Senior engineers.\n\n"
    assert _load(tmp_path, base).reader_profile == "Senior engineers."


def test_appendix_min_equal_to_featured_min_is_allowed(tmp_path, base):
    base["sections"]["news"]["appendix_min"] = base["sections"]["news"]["featured_min"]
    ed = _load(tmp_path, base)
    rules = ed.section_rules()["news"]
    assert rules["appendix_min"] == rules["featured_min"]


def test_zero_valued_section_rules_are_allowed(tmp_path, base):
    base["sections"]["news"].update(featured_min=0, appendix_min=0, cap=0)
    assert _load(tmp_path, base).section_rules()["news"] == {
        "featured_min": 0, "appendix_min": 0, "cap": 0,
    }


@pytest.mark.parametrize("missing", ["burst_trigger_score", "burst_trigger_count"])
def test_single_missing_burst_key_is_rejected(tmp_path, base, missing):
    del base["sections"]["papers"][missing]
    with pytest.raises(EditionError, match="burst_.* must be given together"):
        _load(tmp_path, base)


def test_burst_keys_may_be_omitted_together(tmp_path, base):
    for k in ("burst_cap", "burst_trigger_score", "burst_trigger_count"):
        del base["sections"]["papers"][k]
    assert set(_load(tmp_path, base).section_rules()["papers"]) == {
        "featured_min", "appendix_min", "cap",
    }


def test_burst_keys_are_validated_as_nonnegative_ints(tmp_path, base):
    base["sections"]["papers"]["burst_cap"] = -3
    with pytest.raises(EditionError, match=r"sections\.papers\.burst_cap must be a non-negative"):
        _load(tmp_path, base)


def test_error_message_names_the_offending_file(tmp_path, base):
    base["order"] = "x"
    with pytest.raises(EditionError) as exc:
        _load(tmp_path, base)
    assert str(tmp_path / "agents" / "edition.yaml") in str(exc.value)


def test_discover_ties_on_order_break_by_slug_with_agents_first(tmp_path, base):
    for slug in ("zz", "aa", "agents"):
        _write(tmp_path, {**base, "slug": slug, "order": 7}, slug)
    assert [r[0] for r in edition.discover(tmp_path)] == ["agents", "aa", "zz"]


def test_discover_reports_error_text_for_invalid_edition(tmp_path, base):
    _write(tmp_path, {**base, "slug": "agents"}, "agents")
    _write(tmp_path, {**base, "slug": "other-name"}, "mismatch")
    rows = edition.discover(tmp_path)
    assert [r[0] for r in rows] == ["agents", "mismatch"]
    slug, ed, err = rows[1]
    assert ed is None and "does not match folder name 'mismatch'" in err
