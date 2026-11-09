"""The ``items.source`` prefix contract lives in models.ITEM_SOURCE_PREFIX.

fetch writes ``<prefix>:<id>`` into ``items.source`` and gates.py reads the
prefix back to choose the recency window, near-dup priority and default
section. The prefixes were spelled out separately in six fetch f-strings and
three gates tables. A new family, or a renamed prefix, missing from one gates
table falls back silently: 3-day window, lowest priority, ``blogs`` section.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import gates
from models import (
    ITEM_SOURCE_PREFIX, Section, SourceFamily, item_source, item_source_prefix,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_prefix_values_unchanged():
    # Existing state.db rows carry these prefixes; renaming one orphans them.
    assert {f.value: p for f, p in ITEM_SOURCE_PREFIX.items()} == {
        "rss": "rss", "arxiv": "arxiv", "hn": "hn", "reddit": "reddit",
        "html": "html", "github_releases": "gh",
    }


def test_every_family_has_a_prefix():
    assert set(ITEM_SOURCE_PREFIX) == set(SourceFamily)
    assert len(set(ITEM_SOURCE_PREFIX.values())) == len(SourceFamily)


def test_item_source_format():
    assert item_source(SourceFamily.RSS, {"id": "simonw"}) == "rss:simonw"
    assert item_source(SourceFamily.GITHUB_RELEASES, {"owner": "o", "repo": "r"}) == "gh:o/r"


def test_missing_key_raises_instead_of_writing_none():
    with pytest.raises(KeyError):
        item_source(SourceFamily.RSS, {})
    with pytest.raises(KeyError):
        item_source(SourceFamily.GITHUB_RELEASES, {"owner": "o"})


@pytest.mark.parametrize("family", list(SourceFamily))
def test_prefix_round_trips(family):
    entry = {"id": "x:y", "owner": "o", "repo": "r"}  # ids may contain ':' 
    assert item_source_prefix(item_source(family, entry)) == ITEM_SOURCE_PREFIX[family]
    assert gates.source_family(item_source(family, entry)) == ITEM_SOURCE_PREFIX[family]


@pytest.mark.parametrize("table", ["RECENCY_DAYS", "SOURCE_PRIORITY", "SECTION_BY_FAMILY"])
def test_gates_tables_cover_exactly_the_fetched_prefixes(table):
    keys = set(getattr(gates, table)) - {"default"}
    assert keys == set(ITEM_SOURCE_PREFIX.values()), table


def test_family_sections_are_valid():
    assert set(gates.SECTION_BY_FAMILY.values()) <= {s.value for s in Section}


def _prefix_fstrings(path: Path) -> list[str]:
    """f-strings starting ``<prefix>:``: inline items.source builders."""
    prefixes = tuple(f"{p}:" for p in ITEM_SOURCE_PREFIX.values())
    hits = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if isinstance(first, ast.Constant) and str(first.value).startswith(prefixes):
                hits.append(f"{path.name}:{node.lineno}")
    return hits


def test_fetch_builds_item_source_only_via_models():
    assert _prefix_fstrings(SCRIPTS / "fetch.py") == []
    tree = ast.parse((SCRIPTS / "fetch.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "item_source"]
    # One per adapter: rss, arxiv, hn, html, reddit, github releases.
    assert len(calls) == len(SourceFamily)


def test_gates_parses_prefix_only_via_models():
    tree = ast.parse((SCRIPTS / "gates.py").read_text(encoding="utf-8"))
    colon_splits = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"split", "partition", "rsplit", "rpartition"}
        and any(isinstance(a, ast.Constant) and a.value == ":" for a in n.args)
    ]
    assert colon_splits == []

