"""``write.main(today=...)`` contract: the run date is injectable.

write is the date-keyed stage (issue filename, featured/appendix selection,
previous-issue lookup). Taking ``today`` as a parameter lets tests and
backfills pin the date without exporting ``RUN_DATE``, the same way
fetch/prefilter/rank take ``now``. The AST guard keeps the signature in place.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from conftest import base_template

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PINNED = "2026-07-09"


def test_write_main_accepts_optional_today():
    tree = ast.parse((SCRIPTS / "write.py").read_text())
    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    assert len(mains) == 1
    args = mains[0].args
    assert [a.arg for a in args.args] == ["today"]
    assert len(args.defaults) == 1
    assert isinstance(args.defaults[0], ast.Constant) and args.defaults[0].value is None


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    import db as db_mod
    import write as write_mod

    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    issues = tmp_path / "issues"
    issues.mkdir()
    prompt = tmp_path / "write.md"
    prompt.write_text(base_template("write"))
    monkeypatch.setattr(write_mod, "ISSUES_DIR", issues)
    monkeypatch.setattr(write_mod, "PROMPT_PATH", prompt)
    monkeypatch.setattr(write_mod, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(write_mod, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.setenv("RUN_DATE", "2000-01-01")
    return write_mod, issues


def test_injected_today_overrides_run_date(wired, monkeypatch):
    write_mod, issues = wired
    seen = []

    def fake_load(conn, today):
        seen.append(today)
        return [], {}, {
            "items_featured_papers": 0, "items_featured_news": 0, "items_featured_blogs": 0,
        }

    monkeypatch.setattr(write_mod, "load_today_items", fake_load)
    assert write_mod.main(today=PINNED) == 0
    assert seen == [PINNED]


def test_injected_today_keys_existing_issue_skip(wired):
    write_mod, issues = wired
    (issues / f"{PINNED}.md").write_text("existing")
    assert write_mod.main(today=PINNED) == 0
    assert (issues / f"{PINNED}.md").read_text() == "existing"
    assert not (issues / "2000-01-01.md").exists()


def test_default_today_falls_back_to_run_date(wired, monkeypatch):
    write_mod, issues = wired
    seen = []

    def fake_load(conn, today):
        seen.append(today)
        return [], {}, {
            "items_featured_papers": 0, "items_featured_news": 0, "items_featured_blogs": 0,
        }

    monkeypatch.setattr(write_mod, "load_today_items", fake_load)
    assert write_mod.main() == 0
    assert seen == ["2000-01-01"]
