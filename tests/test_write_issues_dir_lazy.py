"""write resolves the edition, its issue folder and reader profile at call time (#446).

write.py used to bind ``EDITION = edition.current()``,
``ISSUES_DIR = edition.runtime_paths(CONTENT_ROOT).issues_dir`` and
``READER_PROFILE`` when it was imported, from ``db.CONTENT_ROOT`` (itself
frozen at db's import). ``import write`` raised without ``EDITION`` set, and a
caller that changed ``CONTENT_ROOT``/``EDITION`` after import kept the old
edition's folder and prompt unless it reloaded ``db`` and then ``write`` in
that order. replay_writer and backfill re-exported the stale values.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import edition
import write

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_LAZY = {"EDITION", "ISSUES_DIR", "READER_PROFILE"}


def test_write_and_replay_writer_import_without_an_edition_selected():
    env = {k: v for k, v in os.environ.items() if k != "EDITION"}
    for mod in ("write", "replay_writer"):
        proc = subprocess.run(
            [sys.executable, "-c", f"import {mod}"],
            cwd=SCRIPTS, env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, (mod, proc.stderr)


def _walk_skipping_lambdas(node):
    """ast.walk, but lambda bodies (deferred, run at call time) are not entered."""
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(c for c in ast.iter_child_nodes(cur) if not isinstance(c, ast.Lambda))


def test_write_binds_no_edition_or_content_root_at_module_level():
    tree = ast.parse((SCRIPTS / "write.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "db":
            names = {a.name for a in node.names}
            assert "CONTENT_ROOT" not in names, (
                f"write.py:{node.lineno} imports db.CONTENT_ROOT (frozen at import); "
                "use db.content_root()"
            )
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
            for sub in _walk_skipping_lambdas(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                        and isinstance(sub.func.value, ast.Name) and sub.func.value.id == "edition":
                    raise AssertionError(
                        f"write.py:{node.lineno} calls edition.{sub.func.attr} at import time; "
                        "resolve it inside a function (see write.issues_dir)"
                    )


def test_write_functions_do_not_read_the_deprecated_aliases():
    """Inside write.py, code calls issues_dir()/edition.current(), not the aliases."""
    tree = ast.parse((SCRIPTS / "write.py").read_text())
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name != "__getattr__":
            for sub in ast.walk(fn):
                assert not (isinstance(sub, ast.Name) and sub.id in _LAZY), (
                    f"write.py:{sub.lineno} reads {sub.id}; it is a lazy alias for callers"
                )


@pytest.mark.parametrize("mod", ["replay_writer.py", "backfill.py"])
def test_callers_do_not_import_or_read_write_aliases(mod):
    tree = ast.parse((SCRIPTS / mod).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "write":
            bad = _LAZY & {a.name for a in node.names}
            assert not bad, f"{mod}:{node.lineno} imports {bad} from write (frozen copy)"
        if isinstance(node, ast.Attribute) and node.attr in _LAZY \
                and isinstance(node.value, ast.Name) and node.value.id == "write_mod":
            raise AssertionError(f"{mod}:{node.lineno} reads write.{node.attr}; call write.issues_dir()")


def test_aliases_follow_env_without_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("EDITION", "engineering-with-ai")
    other = edition.load("engineering-with-ai")
    expected = edition.runtime_paths(tmp_path, "engineering-with-ai").issues_dir
    assert write.issues_dir() == expected
    assert write.ISSUES_DIR == expected
    assert write.EDITION.slug == "engineering-with-ai"
    assert write.READER_PROFILE == other.reader_profile
    assert other.name in write.build_writer_prompt({}, "RUBRIC")
    payload = write.build_writer_input("2026-07-09", [], {}, {}, None)
    assert payload["reader_profile"] == other.reader_profile
    monkeypatch.delenv("EDITION")
    with pytest.raises(edition.EditionError):  # no default edition
        write.issues_dir()


def test_unknown_module_attribute_still_raises():
    with pytest.raises(AttributeError):
        write.NO_SUCH_NAME  # noqa: B018


def test_find_previous_newsletter_reads_current_content_root(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    issues = edition.runtime_paths(tmp_path, "agents").issues_dir
    assert write.find_previous_newsletter("2026-07-09") is None
    issues.mkdir(parents=True)
    (issues / "2026-07-08.md").write_text("yesterday")
    assert write.find_previous_newsletter("2026-07-09") == "yesterday"
