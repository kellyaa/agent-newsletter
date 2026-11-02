"""fetch.py takes its section and status names from models, not string literals.

fetch.VALID_SECTIONS used to be a hand-written {"papers", "news", "blogs"}
copy of models.Section. Adding a section to models.Section (and therefore to
edition.yaml validation and check_edition) would then have fetch silently
discard every sources.yaml `section:` override naming it. This guard fails if
a bare section/status literal comes back. Docstrings are ignored.
"""
from __future__ import annotations

import ast
from pathlib import Path

import fetch
from models import Section, Status

FETCH_PY = Path(__file__).resolve().parent.parent / "scripts" / "fetch.py"
DOMAIN_NAMES = {s.value for s in Section} | {s.value for s in Status}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_fetch_has_no_bare_section_or_status_literals():
    tree = ast.parse(FETCH_PY.read_text())
    skip = _docstring_nodes(tree)
    offenders = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and node.value in DOMAIN_NAMES
    ]
    assert offenders == [], f"use models.Section/Status instead of literals: {offenders}"


def test_valid_sections_is_immutable_set_of_plain_str():
    assert isinstance(fetch.VALID_SECTIONS, frozenset)
    assert all(type(v) is str for v in fetch.VALID_SECTIONS)


def test_validate_section_accepts_every_models_section():
    for s in Section:
        assert fetch._validate_section(s.value, "src") == s.value


def test_validate_section_rejects_unknown_and_logs_sorted_allowed(caplog):
    with caplog.at_level("WARNING"):
        assert fetch._validate_section("tools", "src") is None
    assert str(sorted(s.value for s in Section)) in caplog.text
