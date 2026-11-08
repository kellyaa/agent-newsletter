"""rank/write read their LLM role settings per call, not at import.

rank.py and write.py used to bind ``RANKER = ranker_config()`` /
``WRITER = writer_config()`` at module level. Because of that, a malformed
``RANKER_TIMEOUT_S`` / ``WRITER_MAX_TOKENS`` broke every importer
(backfill, replay_writer, the test suite) with a ``ValueError`` at import.
A role env var set after import was also silently ignored, the same
import-time binding that #446 removed for the edition and content root.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import llm
import rank
import write

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CASES = [
    ("rank.py", "RANKER", "ranker_config"),
    ("write.py", "WRITER", "writer_config"),
]


@pytest.mark.parametrize("module,var", [("rank", "RANKER_TIMEOUT_S"), ("write", "WRITER_MAX_TOKENS")])
def test_import_survives_malformed_role_env(module, var):
    env = {**os.environ, var: "not-an-int"}
    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=SCRIPTS, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("fname,alias,factory", CASES)
def test_no_module_level_role_config(fname, alias, factory):
    tree = ast.parse((SCRIPTS / fname).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else \
            [node.target] if isinstance(node, ast.AnnAssign) else []
        for t in targets:
            assert not (isinstance(t, ast.Name) and t.id == alias), (
                f"{fname}:{node.lineno} binds {alias} at import; call llm.{factory}() per call"
            )
        stack = [node]
        while stack:
            sub = stack.pop()
            if isinstance(sub, ast.Lambda):
                continue  # _LAZY_ALIASES values run on attribute access, not import
            if isinstance(sub, ast.Call) and isinstance(sub.func, (ast.Name, ast.Attribute)) \
                    and getattr(sub.func, "id", getattr(sub.func, "attr", None)) == factory:
                raise AssertionError(f"{fname}:{node.lineno} calls {factory}() at import time")
            stack.extend(ast.iter_child_nodes(sub))


def _capture(monkeypatch):
    seen = []

    def fake(prompt, schema, schema_name, **kw):
        seen.append((kw["model"], kw["timeout_s"], kw["max_tokens"]))
        return {"rankings": [], "items": []}

    monkeypatch.setattr(rank, "call_llm", fake)
    monkeypatch.setattr(write, "call_llm", fake)
    return seen


def test_env_change_after_import_reaches_call_llm(monkeypatch):
    seen = _capture(monkeypatch)
    monkeypatch.setenv("RANKER_MODEL", "r1")
    monkeypatch.setenv("WRITER_MODEL", "w1")
    rank.invoke_ranker("p", "news")
    write.invoke_writer("p")
    monkeypatch.setenv("RANKER_MODEL", "r2")
    monkeypatch.setenv("RANKER_MAX_TOKENS", "7")
    monkeypatch.setenv("WRITER_MODEL", "w2")
    monkeypatch.setenv("WRITER_TIMEOUT_S", "9")
    rank.invoke_ranker("p", "news")
    write.invoke_writer("p")
    assert [m for m, _, _ in seen] == ["r1", "w1", "r2", "w2"]
    assert seen[2][2] == 7
    assert seen[3][1] == 9


def test_deprecated_aliases_resolve_lazily(monkeypatch):
    monkeypatch.setenv("RANKER_MODEL", "late-r")
    monkeypatch.setenv("WRITER_MODEL", "late-w")
    assert rank.RANKER == llm.ranker_config()
    assert rank.RANKER.model == "late-r"
    assert write.WRITER == llm.writer_config()
    assert write.WRITER.model == "late-w"
