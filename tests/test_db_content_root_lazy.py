"""db.CONTENT_ROOT is resolved on access, and no script imports it (#446).

db.py used to bind ``CONTENT_ROOT = content_root()`` at import "for the stage
modules that still bind it at import time". After the #446 stage slices no
stage did, but reset_day.py still did ``from db import CONTENT_ROOT``, so a
caller that set ``$CONTENT_ROOT`` after ``import db`` reset the wrong
worktree's state.db and issue file unless it reloaded ``db``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import db

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_db_binds_no_content_root_at_module_level():
    tree = ast.parse((SCRIPTS / "db.py").read_text())
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else \
            [node.target] if isinstance(node, ast.AnnAssign) else []
        for t in targets:
            assert not (isinstance(t, ast.Name) and t.id in ("CONTENT_ROOT", "DB_PATH")), (
                f"db.py:{node.lineno} binds {t.id} at import; it is a lazy alias "
                "(see db.__getattr__); use db.content_root() / db.db_path()"
            )


def test_content_root_alias_follows_env_without_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    assert db.CONTENT_ROOT == tmp_path
    monkeypatch.delenv("CONTENT_ROOT")
    assert db.CONTENT_ROOT == db.REPO_ROOT


def test_scripts_do_not_import_frozen_content_root():
    for path in sorted(SCRIPTS.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "db":
                assert "CONTENT_ROOT" not in {a.name for a in node.names}, (
                    f"{path.name}:{node.lineno} imports db.CONTENT_ROOT; "
                    "call db.content_root()"
                )
            if isinstance(node, ast.Attribute) and node.attr == "CONTENT_ROOT" \
                    and isinstance(node.value, ast.Name) and node.value.id == "db":
                raise AssertionError(
                    f"{path.name}:{node.lineno} reads db.CONTENT_ROOT; call db.content_root()"
                )


def test_reset_day_uses_content_root_at_call_time(monkeypatch, tmp_path):
    import reset_day

    monkeypatch.setenv("EDITION", "agents")
    monkeypatch.setenv("RUN_DATE", "2026-01-02")
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    paths = __import__("edition").runtime_paths(tmp_path)
    paths.issues_dir.mkdir(parents=True)
    issue = paths.issues_dir / "2026-01-02.md"
    issue.write_text("x")
    db.init_db(paths.state_db)
    assert reset_day.main([]) == 0
    assert not issue.exists()
