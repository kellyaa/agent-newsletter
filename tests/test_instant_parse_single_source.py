"""Stored ISO-8601 instants are parsed only by ``models.parse_instant``.

fetch normalises feed dates into ``items.published_at``; gates (recency window),
candidates (paper pre-rank recency) and publish (``$RUN_STARTED_AT`` run
duration) read them back. Each used to spell "swap trailing Z, fromisoformat,
naive => UTC" inline, and publish once lacked the naive => UTC step (an aware
minus naive TypeError). A one-sided change would skew recency windows by a zone
offset or crash a stage, with nothing failing loudly.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from models import parse_instant

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# Every module that reads a stored instant string.
CONSUMERS = ("fetch", "gates", "candidates", "publish")


@pytest.mark.parametrize("raw", [
    "2026-06-01T09:30:00Z",
    "2026-06-01T09:30:00+00:00",
    "2026-06-01T09:30:00",
])
def test_utc_spellings_agree(raw):
    dt = parse_instant(raw)
    assert dt == datetime(2026, 6, 1, 9, 30, tzinfo=timezone.utc)
    assert dt.utcoffset() == timedelta(0)


def test_offset_is_kept():
    dt = parse_instant("2026-06-01T09:30:00+02:00")
    assert dt.utcoffset() == timedelta(hours=2)
    assert dt == datetime(2026, 6, 1, 7, 30, tzinfo=timezone.utc)


def test_result_is_always_aware_and_subtractable():
    now = datetime(2026, 6, 2, tzinfo=timezone.utc)
    assert now - parse_instant("2026-06-01") == timedelta(days=1)


def test_errors_left_to_callers():
    # Callers each catch these and apply their own fallback.
    with pytest.raises(ValueError):
        parse_instant("July 13, 2026")
    with pytest.raises(AttributeError):
        parse_instant(None)  # type: ignore[arg-type]


def _tree(mod: str) -> ast.Module:
    return ast.parse((SCRIPTS / f"{mod}.py").read_text())


def _attr_calls(tree: ast.Module, attr: str) -> list[int]:
    return [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
    ]


@pytest.mark.parametrize("mod", CONSUMERS)
def test_no_inline_fromisoformat(mod):
    assert _attr_calls(_tree(mod), "fromisoformat") == [], (
        f"{mod}.py parses an instant inline; use models.parse_instant"
    )


@pytest.mark.parametrize("mod", CONSUMERS)
def test_consumers_use_parse_instant(mod):
    calls = [
        n for n in ast.walk(_tree(mod))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "parse_instant"
    ]
    assert calls, f"{mod}.py no longer calls models.parse_instant"
