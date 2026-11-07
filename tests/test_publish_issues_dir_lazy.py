"""publish resolves the edition's issue folder at call time, not at import (#446).

publish.py used to bind ``ISSUES_DIR = edition.runtime_paths(CONTENT_ROOT).issues_dir``
when it was imported, from ``db.CONTENT_ROOT`` (itself frozen at db's import).
A test or caller that changed ``CONTENT_ROOT``/``EDITION`` after import kept the
old folder unless it reloaded ``db`` and then ``publish`` in that order; with the
wrong order publish looked for the issue file in the wrong edition and
reported ``MISSING_INPUT`` instead of failing loudly.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import edition
import publish

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_publish_imports_without_an_edition_selected():
    env = {k: v for k, v in os.environ.items() if k != "EDITION"}
    proc = subprocess.run(
        [sys.executable, "-c", "import publish"],
        cwd=SCRIPTS, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_publish_binds_no_edition_or_content_root_at_module_level():
    tree = ast.parse((SCRIPTS / "publish.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "db":
            names = {a.name for a in node.names}
            assert "CONTENT_ROOT" not in names, (
                f"publish.py:{node.lineno} imports db.CONTENT_ROOT (frozen at import); "
                "use db.content_root()"
            )
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                        and sub.value.id == "edition":
                    raise AssertionError(
                        f"publish.py:{node.lineno} reads edition.{sub.attr} at import time; "
                        "resolve it inside a function (see publish.issues_dir)"
                    )


def test_issues_dir_follows_env_without_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    expected = edition.runtime_paths(tmp_path, "agents").issues_dir
    assert publish.issues_dir() == expected
    assert publish.ISSUES_DIR == expected  # deprecated alias stays in step
    monkeypatch.delenv("EDITION")
    with pytest.raises(edition.EditionError):  # no default edition
        publish.issues_dir()


def test_unknown_module_attribute_still_raises():
    with pytest.raises(AttributeError):
        publish.NO_SUCH_NAME  # noqa: B018


def test_main_reads_issue_from_current_content_root(monkeypatch, tmp_path):
    """main() looks under the CONTENT_ROOT in effect when it runs, with no reload."""
    import db as db_mod

    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(publish, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(publish, "init_db", lambda: db_mod.init_db(db_path))
    issues = edition.runtime_paths(tmp_path, "agents").issues_dir
    issues.mkdir(parents=True)
    (issues / "2026-07-09.md").write_text("x")  # present but too small
    assert publish.main(today="2026-07-09") == publish.ExitCode.ISSUE_TOO_SMALL
