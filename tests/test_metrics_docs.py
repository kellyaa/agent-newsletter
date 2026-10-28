"""docs/metrics/README.md may only name columns that exist in the schema."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import db

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = (REPO_ROOT / "docs" / "metrics" / "README.md").read_text()


def _columns(table: str) -> set[str]:
    conn = sqlite3.connect(":memory:")
    conn.executescript(db.SCHEMA)
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_documented_runs_columns_exist():
    section = DOC.split("## Pipeline metrics", 1)[1].split("##", 1)[0]
    documented = set(re.findall(r"`([a-z_]+)`", section)) - {"runs"}
    assert documented, "no columns documented"
    assert documented <= _columns("runs"), documented - _columns("runs")


def test_documented_run_attempts_columns_exist():
    section = DOC.split("## Reliability metrics", 1)[1].split("##", 1)[0]
    documented = set(re.findall(r"`([a-z_]+)`", section)) - {"run_attempts", "started", "ok", "failed"}
    assert documented <= _columns("run_attempts")
    assert {"outcome", "failed_stage", "exit_code"} <= documented
