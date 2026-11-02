"""The fetch completion marker key is defined once, in models.MetaKey.

fetch.py sets the ``meta`` row and reset_day.py --refetch deletes it. Each used
to spell the key itself behind a "keep in sync" comment; if one copy changed,
--refetch would quietly clear a key nothing reads, fetch would skip, and the
write stage would overwrite the live issue (#321). These tests fail if the key
gets spelled out as a literal again.
"""
from __future__ import annotations

import ast
from pathlib import Path

import fetch
import reset_day
from models import MetaKey

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_fetch_and_reset_day_share_the_key():
    assert fetch.FETCH_DONE_KEY == reset_day.FETCH_DONE_KEY == MetaKey.FETCH_DONE


def test_key_literal_only_in_models():
    offenders = []
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name == "models.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in {k.value for k in MetaKey}:
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"use models.MetaKey instead of literals: {offenders}"
