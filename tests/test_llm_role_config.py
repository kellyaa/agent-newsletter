"""LLM role settings (model, timeout, token cap) have one home: scripts/llm.py.

new_edition.py used to re-read WRITER_MODEL / WRITER_TIMEOUT_S /
WRITER_MAX_TOKENS inline with its own copy of the defaults, so a change to the
writer's defaults in write.py would silently not reach the edition drafter.
These tests pin the shared ``llm.writer_config()`` / ``llm.ranker_config()``
and fail if a script grows its own env read for a role setting again.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import llm
import rank
import write

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ROLE_VARS = {
    f"{role}_{key}"
    for role in ("WRITER", "RANKER")
    for key in ("MODEL", "TIMEOUT_S", "MAX_TOKENS")
}


def _env_reads(tree: ast.Module) -> set[str]:
    """String literals passed as the first argument to os.environ.get / os.getenv / os.environ[...]."""
    names: set[str] = set()
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Call) and node.args:
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in {"get", "getenv"}:
                key = node.args[0]
        elif isinstance(node, ast.Subscript):
            v = node.value
            if isinstance(v, ast.Attribute) and v.attr == "environ":
                key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            names.add(key.value)
    return names


def test_role_env_vars_read_only_in_llm():
    offenders = {}
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name == "llm.py":
            continue
        hit = _env_reads(ast.parse(path.read_text(encoding="utf-8"))) & ROLE_VARS
        if hit:
            offenders[path.name] = sorted(hit)
    assert not offenders, f"LLM role settings read outside llm.py: {offenders}"


def test_llm_reads_every_role_var():
    assert ROLE_VARS <= _env_reads(ast.parse((SCRIPTS / "llm.py").read_text(encoding="utf-8")))


@pytest.mark.parametrize("fn, role, defaults", [
    (llm.writer_config, "WRITER", ("gpt-4o-mini", 1200, 16000)),
    (llm.ranker_config, "RANKER", ("gpt-4o-mini", 1800, 32000)),
])
def test_role_config_defaults_and_env_overrides(monkeypatch, fn, role, defaults):
    for key in ("MODEL", "TIMEOUT_S", "MAX_TOKENS"):
        monkeypatch.delenv(f"{role}_{key}", raising=False)
    assert fn() == llm.RoleConfig(*defaults)
    monkeypatch.setenv(f"{role}_MODEL", "m")
    monkeypatch.setenv(f"{role}_TIMEOUT_S", "7")
    monkeypatch.setenv(f"{role}_MAX_TOKENS", "8")
    assert fn() == llm.RoleConfig("m", 7, 8)


def test_role_config_is_immutable():
    cfg = llm.writer_config()
    with pytest.raises(AttributeError):
        cfg.model = "x"  # type: ignore[misc]


def test_stages_pass_their_role_config_to_call_llm(monkeypatch):
    seen = []

    def fake(prompt, schema, schema_name, **kw):
        seen.append((kw["label"], kw["model"], kw["timeout_s"], kw["max_tokens"]))
        return {"rankings": [], "items": []}

    for k, v in {
        "RANKER_MODEL": "r-model", "RANKER_TIMEOUT_S": "11", "RANKER_MAX_TOKENS": "22",
        "WRITER_MODEL": "w-model", "WRITER_TIMEOUT_S": "33", "WRITER_MAX_TOKENS": "44",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(rank, "call_llm", fake)
    monkeypatch.setattr(write, "call_llm", fake)
    rank.invoke_ranker("p", "news")
    write.invoke_writer("p")
    assert ("news", "r-model", 11, 22) in seen
    assert ("writer", "w-model", 33, 44) in seen
