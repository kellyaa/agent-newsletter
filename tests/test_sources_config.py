"""Validate the committed sources.yaml.

fetch.main() reads sources.yaml at runtime and indexes required keys directly
(src['id'], src['url'], src['query'], entry['owner'] ...).  A typo or missing
key therefore surfaces as a KeyError in the middle of the daily run, and invalid
override values (section / keyword_gate_bypass / recency_days) are only logged
and silently ignored.  These tests catch both at CI time against the real file.
"""
import logging

import pytest

import fetch
from fetch import VALID_SECTIONS, load_sources

FAMILIES = {"rss", "arxiv", "hn", "reddit", "html", "github_releases"}
REQUIRED_KEYS = {
    "rss": {"id", "url"},
    "arxiv": {"id", "query"},
    "hn": {"id", "query"},
    "reddit": {"id", "url"},
    "html": {"id", "url"},
    "github_releases": {"owner", "repo"},
}


@pytest.fixture(scope="module")
def sources():
    return load_sources()


def _entries(sources):
    for family in FAMILIES:
        for entry in sources.get(family) or []:
            yield family, entry


def test_sources_file_is_a_mapping_of_known_families(sources):
    assert isinstance(sources, dict)
    assert set(sources) <= FAMILIES, f"unknown families: {set(sources) - FAMILIES}"


def test_every_family_is_a_list_of_mappings(sources):
    for family in FAMILIES:
        value = sources.get(family) or []
        assert isinstance(value, list), f"{family} must be a list"
        for entry in value:
            assert isinstance(entry, dict), f"{family} entry {entry!r} must be a mapping"


def test_at_least_one_source_is_configured(sources):
    assert any(True for _ in _entries(sources))


def test_entries_have_required_keys(sources):
    for family, entry in _entries(sources):
        missing = REQUIRED_KEYS[family] - set(entry)
        assert not missing, f"{family} entry {entry!r} is missing {sorted(missing)}"


def test_source_ids_are_unique_per_family(sources):
    # The id becomes the item's `source` value (e.g. "rss:<id>"), so duplicates
    # within a family would be indistinguishable downstream.
    for family in FAMILIES - {"github_releases"}:
        ids = [e["id"] for e in sources.get(family) or []]
        assert len(ids) == len(set(ids)), f"duplicate ids in {family}: {ids}"


def test_urls_are_http_or_https(sources):
    for family, entry in _entries(sources):
        if "url" in entry:
            assert entry["url"].startswith(("http://", "https://")), (family, entry["id"])


def test_numeric_fields_are_positive_ints(sources):
    for family, entry in _entries(sources):
        for key in ("max_results", "hours_back", "min_points", "min_score", "max_items", "limit"):
            if key in entry:
                value = entry[key]
                assert isinstance(value, int) and not isinstance(value, bool) and value > 0, (
                    f"{family}/{entry.get('id')}: {key}={value!r}"
                )


def test_override_fields_are_accepted_by_the_runtime_validators(sources, caplog):
    """No override in the committed file is rejected (and silently ignored)."""
    with caplog.at_level(logging.WARNING, logger=fetch.log.name):
        for family, entry in _entries(sources):
            sid = str(entry.get("id") or f"{entry.get('owner')}/{entry.get('repo')}")
            if "section" in entry:
                assert entry["section"] in VALID_SECTIONS, (family, sid)
                assert fetch._validate_section(entry["section"], sid) == entry["section"]
            if "keyword_gate_bypass" in entry:
                assert isinstance(entry["keyword_gate_bypass"], bool), (family, sid)
            if "recency_days" in entry:
                assert fetch._validate_recency_days(entry["recency_days"], sid) == entry["recency_days"]
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
