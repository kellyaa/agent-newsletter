"""gates.py is the single home of the cheap prefilter gates.

It must stay importable without $EDITION (check_edition.py relies on that to
dry-run any edition), and prefilter.py must use the same objects rather than
carrying its own copies.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import gates

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_gates_import_without_edition_selected():
    env = {k: v for k, v in os.environ.items() if k != "EDITION"}
    env["PYTHONPATH"] = str(SCRIPTS)
    proc = subprocess.run(
        [sys.executable, "-c", "import gates, sys; sys.exit('edition' in sys.modules)"],
        env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_prefilter_reuses_gate_objects():
    import prefilter

    assert prefilter.assign_section is gates.assign_section
    assert prefilter.collapse_near_dups is gates.collapse_near_dups
    assert prefilter.keyword_gate is gates.keyword_gate
    assert prefilter._passes_recency is gates.passes_recency
    assert prefilter.RECENCY_DAYS is gates.RECENCY_DAYS
    assert prefilter.SECTION_BY_FAMILY is gates.SECTION_BY_FAMILY


def test_check_edition_does_not_import_prefilter_stage():
    tree = ast.parse((SCRIPTS / "check_edition.py").read_text(encoding="utf-8"))
    imported = {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    } | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "prefilter" not in imported
    assert "gates" in imported


def test_gates_compose_without_an_edition():
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    kw = re.compile(r"\bagent", re.I)
    items = [
        {"id": "a", "source": "arxiv:cs.AI", "title": "Agent planning at scale",
         "raw_text": "", "published_at": "2026-06-30T00:00:00Z", "fetched_at": "x"},
        {"id": "b", "source": "hn:front", "title": "Agent planning at scale!",
         "raw_text": "", "published_at": "2026-06-30T00:00:00Z", "fetched_at": "x"},
        {"id": "c", "source": "hn:front", "title": "Unrelated", "raw_text": "",
         "published_at": "2026-06-30T00:00:00Z", "fetched_at": "x"},
        {"id": "d", "source": "reddit:x", "title": "Old agent thread", "raw_text": "",
         "published_at": "2026-05-01T00:00:00Z", "fetched_at": "x"},
    ]
    kept = [it for it in items if gates.passes_recency(it, now) and gates.keyword_gate(it, kw)]
    kept = gates.collapse_near_dups(kept)
    assert [it["id"] for it in kept] == ["a"]
    assert gates.assign_section(kept[0]["source"]) == "papers"
    assert gates.assign_section("unknown:x") == "blogs"
    assert gates.assign_section("arxiv:x", "news") == "news"
    assert gates.assign_section("arxiv:x", "bogus") == "papers"
