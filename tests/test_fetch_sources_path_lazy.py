"""fetch resolves the edition's sources.yaml at call time, not at import (#446).

fetch.py used to bind ``SOURCES_PATH = edition.current().paths().sources`` when
it was imported. Two problems followed: ``import fetch`` failed with no
``EDITION`` set, and check_edition.dry_run had to set ``os.environ["EDITION"]``
around the import. If fetch was already imported under another edition, that
import did nothing and fetch kept the other edition's sources path. Nothing
reported it.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import edition
import fetch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_fetch_imports_without_an_edition_selected():
    env = {k: v for k, v in os.environ.items() if k != "EDITION"}
    proc = subprocess.run(
        [sys.executable, "-c", "import fetch, db, gates"],
        cwd=SCRIPTS, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_fetch_binds_no_edition_state_at_module_level():
    tree = ast.parse((SCRIPTS / "fetch.py").read_text())
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                        and sub.value.id == "edition":
                    raise AssertionError(
                        f"fetch.py:{node.lineno} reads edition.{sub.attr} at import time; "
                        "resolve it inside a function (see fetch.sources_path)"
                    )


def test_sources_path_follows_edition_env_at_call_time(monkeypatch):
    monkeypatch.setenv("EDITION", "agents")
    assert fetch.sources_path() == edition.load("agents").paths().sources
    monkeypatch.delenv("EDITION")
    with pytest.raises(edition.EditionError):  # no default edition
        fetch.sources_path()


def test_load_sources_reads_an_explicit_path(tmp_path):
    p = tmp_path / "sources.yaml"
    p.write_text("rss:\n  - id: a\n    url: https://example.com/f\n")
    assert fetch.load_sources(p) == {"rss": [{"id": "a", "url": "https://example.com/f"}]}


def test_check_edition_does_not_touch_the_environment():
    src = (SCRIPTS / "check_edition.py").read_text()
    tree = ast.parse(src)
    for sub in ast.walk(tree):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                and sub.value.id == "os" and sub.attr == "environ":
            raise AssertionError(
                f"check_edition.py:{sub.lineno} uses os.environ; dry_run passes paths "
                "explicitly and must not select an edition through the environment"
            )
