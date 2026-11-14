"""Code examples and CLI lines in docs/ must match the real scripts (#711).

The guides under docs/ are where contributors and agents copy code from, and
nothing else fails when they drift: they once showed a ``call_llm`` signature
that never existed, imported names no module defines, used ``from scripts.x``
(``scripts/`` is not a package; its modules import each other top-level), and
told readers to run ``rank.py --dry-run`` (rank.py reads no argv, so that is a
live, token-spending ranking). This pins the parts that can be checked
mechanically:

- every ``from <script> import <name>`` in a python block names a real attribute;
- no python block imports ``scripts.<module>``;
- a keyword argument passed to an imported script function exists in its
  signature (blocks that are deliberate fragments and don't parse are skipped);
- every ``--flag`` on a ``uv run scripts/<x>.py`` / ``python scripts/<x>.py``
  line is one that script's argparse defines.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
DOCS = sorted((REPO_ROOT / "docs").rglob("*.md"))

_PY_BLOCK = re.compile(r"```python\n(.*?)```", re.S)
_ANY_BLOCK = re.compile(r"```[\w-]*\n(.*?)```", re.S)
_FROM_IMPORT = re.compile(r"^[ \t]*from[ \t]+([\w.]+)[ \t]+import[ \t]+\(?([\w \t,]+)", re.M)
_CLI = re.compile(r"(?:uv run|python3?)\s+scripts/(\w+)\.py([^\n`|#;&>)]*)")
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def _is_script(name: str) -> bool:
    return (SCRIPTS / f"{name}.py").is_file()


def _module(mod: str) -> str:
    # Check a `scripts.x` import's names too, so the package-import test is
    # not the only thing standing between a bad example and the reader.
    return mod.removeprefix("scripts.")


def _blocks():
    for doc in DOCS:
        for block in _PY_BLOCK.findall(doc.read_text()):
            yield doc.relative_to(REPO_ROOT), block


def _imports(block: str):
    for mod, names in _FROM_IMPORT.findall(block):
        for name in (n.strip() for n in names.split(",")):
            if name:
                yield mod, name


def _script_flags(name: str) -> set[str]:
    """``--flags`` the script's argparse defines (empty if it reads no argv)."""
    src = (SCRIPTS / f"{name}.py").read_text()
    if "add_argument" not in src:
        return set()
    return {
        f for call in re.findall(r"add_argument\((.*?)\)", src, re.S)
        for f in re.findall(r"[\"'](--[a-z][a-z0-9-]*)[\"']", call)
    }


def test_docs_have_python_examples():
    assert sum(1 for _ in _blocks()) > 10


def test_docs_never_import_scripts_as_a_package():
    # Every fenced block, not just python: a `python3 -c "from scripts.db ..."`
    # one-liner in a shell block fails the same way. Prose that warns against
    # the pattern (inline `code`) is outside the fences and not checked.
    bad = sorted({
        f"{doc.relative_to(REPO_ROOT)}: {m}" for doc in DOCS
        for block in _ANY_BLOCK.findall(doc.read_text())
        for m in re.findall(r"(?:from|import)\s+scripts\.\w+", block)
    })
    assert not bad, (
        "scripts/ is not a package (its modules `import edition` etc.); put "
        f"scripts/ on sys.path and `from db import ...` instead: {bad}"
    )


def test_docs_import_only_names_scripts_define():
    bad = sorted({
        f"{doc}: {mod}.{name}" for doc, b in _blocks()
        for mod, name in _imports(b)
        if _is_script(_module(mod))
        and not hasattr(importlib.import_module(_module(mod)), name)
    })
    assert not bad, f"docs import names that do not exist: {bad}"


def test_docs_pass_only_real_keyword_arguments():
    bad: set[str] = set()
    for doc, block in _blocks():
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue  # a deliberate fragment ("..." bodies, excerpts)
        funcs = {}
        for mod, name in _imports(block):
            if _is_script(_module(mod)):
                obj = getattr(importlib.import_module(_module(mod)), name, None)
                if inspect.isfunction(obj):
                    funcs[name] = inspect.signature(obj)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            sig = funcs.get(node.func.id)
            if sig is None:
                continue
            for kw in node.keywords:
                if kw.arg and kw.arg not in sig.parameters:
                    bad.add(f"{doc}: {node.func.id}({kw.arg}=...)")
    assert not bad, f"docs pass keyword arguments the function does not take: {sorted(bad)}"


def test_docs_cli_flags_exist():
    bad: set[str] = set()
    for doc in DOCS + [REPO_ROOT / "README.md", REPO_ROOT / "CONTRIBUTING.md"]:
        for script, rest in _CLI.findall(doc.read_text()):
            if not _is_script(script):
                continue
            allowed = _script_flags(script)
            for flag in _FLAG.findall(rest):
                if flag not in allowed:
                    bad.add(f"{doc.relative_to(REPO_ROOT)}: scripts/{script}.py {flag}")
    assert not bad, (
        "docs pass flags the script does not define (a script with no argparse "
        f"ignores them and runs for real): {sorted(bad)}"
    )


@pytest.mark.parametrize("script,flag", [("backfill", "--apply-published"), ("new_edition", "--no-llm")])
def test_flag_extractor_sees_real_flags(script, flag):
    assert flag in _script_flags(script)


def test_flag_extractor_sees_no_flags_for_argv_free_stage():
    assert _script_flags("rank") == set()
