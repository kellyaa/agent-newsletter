"""SQL in scripts/ must take item status and section values from models.py.

``models.Status`` and ``models.Section`` exist so a renamed or misspelt state
fails loudly instead of matching zero rows. A literal such as
``WHERE status = 'candidat'`` is valid SQL that silently does nothing, so the
constants only help if SQL actually uses them. This test parses every script
and fails on any SQL string constant that quotes a status or section value
literally; interpolate ``Status.X`` / ``Section.X`` (or bind it as a ``?``
parameter) instead.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from models import Section, Status

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

_SQL = re.compile(r"\b(SELECT|UPDATE|INSERT|DELETE|WHERE)\b")
_VALUES = "|".join(sorted({*Status, *Section}))
_QUOTED_VALUE = re.compile(rf"'({_VALUES})'")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in skip or not _SQL.search(node.value):
            continue
        for m in _QUOTED_VALUE.finditer(node.value):
            out.append(f"{path.name}:{node.lineno}: literal {m.group(0)} in SQL")
    return out


def test_sql_uses_status_and_section_constants():
    found = [v for p in sorted(SCRIPTS.glob("*.py")) for v in _violations(p)]
    assert not found, "use models.Status / models.Section in SQL:\n" + "\n".join(found)


def test_checker_flags_a_literal(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("Q = \"UPDATE items SET status = 'dropped' WHERE id = ?\"\n")
    good = tmp_path / "good.py"
    good.write_text(
        "from models import Status\n"
        "Q = f\"UPDATE items SET status = '{Status.DROPPED}' WHERE id = ?\"\n"
        "def f():\n    \"\"\"Docs may say WHERE status = 'new'.\"\"\"\n"
    )
    assert _violations(bad) == ["bad.py:1: literal 'dropped' in SQL"]
    assert _violations(good) == []
