"""One writer core: ``write.write_issue`` is shared by the daily run and backfill.

backfill.run_writer_for_date used to re-implement write.main's body step by
step (load -> prev -> payload -> rubric -> LLM -> assemble -> write), so any
writer change had to be made twice. Both now call write.write_issue; these
tests pin that and the behaviour of the shared core.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from conftest import base_template

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
WRITER_STEPS = {
    "load_today_items", "find_previous_newsletter", "build_writer_input",
    "build_writer_prompt", "invoke_writer", "assemble_issue",
}


def _attr_calls(fn: ast.FunctionDef) -> set[str]:
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                out.add(f.attr)
            elif isinstance(f, ast.Name):
                out.add(f.id)
    return out


def _func(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text())
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_backfill_delegates_to_write_issue():
    calls = _attr_calls(_func(SCRIPTS / "backfill.py", "run_writer_for_date"))
    assert "write_issue" in calls
    assert not calls & WRITER_STEPS, f"backfill re-implements writer steps: {calls & WRITER_STEPS}"


def test_write_main_delegates_to_write_issue():
    calls = _attr_calls(_func(SCRIPTS / "write.py", "main"))
    assert "write_issue" in calls
    assert not calls & WRITER_STEPS


@pytest.fixture()
def core(tmp_path, monkeypatch):
    import db as db_mod
    import write as write_mod

    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, published_at, fetched_at, "
        "status, section, score, tags, why, first_seen_date, last_seen_date) "
        "VALUES ('f1','arxiv:cs','http://a/1','http://a/1','T','2026-07-09','2026-07-09',"
        "'featured','papers',9,'[]','why','2026-07-09','2026-07-09')"
    )
    conn.commit()
    issues = tmp_path / "issues"
    prompt = tmp_path / "write.md"
    prompt.write_text(base_template("write"))
    monkeypatch.setattr(write_mod, "issues_dir", lambda: issues)
    monkeypatch.setattr(write_mod, "PROMPT_PATH", prompt)
    monkeypatch.setattr(
        write_mod, "invoke_writer",
        lambda p: {"theme": "Pinned theme", "items": [{"id": "f1", "summary": "Pinned prose."}]},
    )
    logged = []
    monkeypatch.setattr(write_mod.prompts, "render_and_log",
                        lambda *a, **k: logged.append(a) or "rubric")
    yield write_mod, conn, issues, logged
    conn.close()


def test_write_issue_writes_and_logs_prompt(core):
    write_mod, conn, issues, logged = core
    out = write_mod.write_issue(conn, "2026-07-09")
    assert out == issues / "2026-07-09.md"
    text = out.read_text()
    assert "Pinned theme" in text and "Pinned prose." in text
    assert len(logged) == 1


def test_write_issue_skips_existing_unless_forced(core):
    write_mod, conn, issues, _ = core
    issues.mkdir()
    (issues / "2026-07-09.md").write_text("existing")
    assert write_mod.write_issue(conn, "2026-07-09") is None
    assert (issues / "2026-07-09.md").read_text() == "existing"
    out = write_mod.write_issue(conn, "2026-07-09", force=True)
    assert "Pinned prose." in out.read_text()


def test_write_issue_log_prompt_false_leaves_logs_alone(core):
    write_mod, conn, issues, logged = core
    assert write_mod.write_issue(conn, "2026-07-09", log_prompt=False) is not None
    assert logged == []


def test_write_issue_nothing_to_publish(core):
    write_mod, conn, issues, logged = core
    conn.execute("DELETE FROM items")
    conn.commit()
    assert write_mod.write_issue(conn, "2026-07-09") is None
    assert not (issues / "2026-07-09.md").exists()
    assert logged == []
