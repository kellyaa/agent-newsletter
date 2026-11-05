"""sources.yaml per-entry override rules live in models.py, not inline.

fetch (runtime), check_edition (lint) and new_edition (scaffold) each used to
hand-write the "positive int, but not bool" predicate and the reddit default
limit. They drifted: check_edition never linted reddit ``limit``, so
``limit: 0`` passed ``check_edition`` and fetch then silently fell back to 10.
These tests pin the shared rules and fail if an inline copy comes back.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

import check_edition
import fetch
import new_edition
from test_check_edition import _clone_agents, editions_dir  # noqa: F401  (fixture)
from models import (
    REDDIT_DEFAULT_LIMIT,
    SOURCE_BOOL_FLAGS,
    SOURCE_POSITIVE_INT_KEYS,
    is_positive_int,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CONSUMERS = ("fetch.py", "check_edition.py", "new_edition.py")


@pytest.mark.parametrize("value, ok", [
    (1, True), (30, True), (0, False), (-1, False), (True, False), (False, False),
    ("5", False), (5.0, False), (None, False),
])
def test_is_positive_int(value, ok):
    assert is_positive_int(value) is ok


def _bool_isinstance_checks(path: Path) -> list[int]:
    """Lines calling isinstance(<name>, bool): the tell-tale of an inline positive-int rule.

    check_edition's bool-flag lint tests a subscript (``entry[flag]``), not a bare
    name, so it is not flagged.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "isinstance"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Name)
        and isinstance(node.args[1], ast.Name) and node.args[1].id == "bool"
    ]


def test_detector_catches_inline_rule(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("ok = isinstance(v, int) and not isinstance(v, bool) and v > 0\n"
                 "flag = isinstance(entry['k'], bool)\n")
    assert _bool_isinstance_checks(p) == [1]


@pytest.mark.parametrize("name", CONSUMERS)
def test_no_inline_positive_int_rule(name):
    lines = _bool_isinstance_checks(SCRIPTS / name)
    assert lines == [], f"{name}:{lines} re-implements models.is_positive_int"


@pytest.mark.parametrize("name", ("fetch.py", "new_edition.py"))
def test_no_local_reddit_default(name):
    tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
    assigned = {
        t.id
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for t in node.targets if isinstance(t, ast.Name)
    }
    assert "REDDIT_DEFAULT_LIMIT" not in assigned
    assert fetch.REDDIT_DEFAULT_LIMIT is REDDIT_DEFAULT_LIMIT


def test_check_edition_has_no_inline_override_key_list():
    tree = ast.parse((SCRIPTS / "check_edition.py").read_text(encoding="utf-8"))
    literals = {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    inline = (set(SOURCE_BOOL_FLAGS) | set(SOURCE_POSITIVE_INT_KEYS)) & literals
    assert inline == set(), f"use models.SOURCE_BOOL_FLAGS/SOURCE_POSITIVE_INT_KEYS: {inline}"


@pytest.mark.parametrize("value", [0, -3, True, "5", 2.5])
def test_lint_and_fetch_agree_on_bad_reddit_limit(editions_dir, value):
    """A limit fetch would discard is a lint error, not a silent fallback."""
    dest = _clone_agents(editions_dir)
    (dest / "sources.yaml").write_text(yaml.safe_dump(
        {"reddit": [{"id": "r", "url": "https://www.reddit.com/r/x/top.json", "limit": value}]}))
    assert any("limit must be a positive integer" in e
               for e in check_edition.lint("demo").errors)
    assert fetch._validate_reddit_limit(value, "r") == REDDIT_DEFAULT_LIMIT


@pytest.mark.parametrize("value", [None, 0, True, "5"])
def test_new_edition_scaffolds_models_reddit_default(monkeypatch, value):
    monkeypatch.setattr(new_edition, "check_subreddit", lambda client, url: None)
    out, dropped = new_edition.validate_sources(
        {"reddit": [{"subreddit": "python", "limit": value}]}, [], client=None,
        sleep=lambda _s: None)
    assert dropped == []
    assert out["reddit"][0]["limit"] == REDDIT_DEFAULT_LIMIT
