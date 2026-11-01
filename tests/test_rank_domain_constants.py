"""rank.py takes its section and status names from models, not string literals.

rank.py used to spell out "papers"/"news"/"blogs" and "featured"/"appendix"/...
inline in main(). Renaming or adding a section/status in models.py would
then leave rank.py quietly bucketing items under a stale name. This guard
fails if a bare literal comes back. Docstrings are ignored; "papers_prescored"
is a candidate bucket, not a section, so it is allowed.
"""
from __future__ import annotations

import ast
from pathlib import Path

from models import Section, Status

RANK_PY = Path(__file__).resolve().parent.parent / "scripts" / "rank.py"
DOMAIN_NAMES = {s.value for s in Section} | {s.value for s in Status}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_rank_has_no_bare_section_or_status_literals():
    tree = ast.parse(RANK_PY.read_text())
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
