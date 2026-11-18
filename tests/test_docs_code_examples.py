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


# --- Where a constant lives, and the source-family contracts (#767) ----------
#
# The source-family guides kept pointing at the pre-registry layout: gate tables
# "in prefilter.py" (they live in gates.py; prefilter only re-exports them),
# FAMILIES / REQUIRED_KEYS literals for check_edition (derived from
# models.SourceFamily), and adapter examples building ``items.source`` and the
# per-source label with f-strings (models.item_source / models.source_label
# only). Followed literally, the guides failed the single-source guards.

# Dated design records, kept as written; not guides to follow.
_HISTORICAL = ("superpowers",)
_LIVE_DOCS = [
    *(d for d in DOCS if not set(_HISTORICAL) & set(d.relative_to(REPO_ROOT).parts)),
    REPO_ROOT / "SPEC.md", REPO_ROOT / "CONTRIBUTING.md", REPO_ROOT / "README.md",
]
# "`CONST` ... in `scripts/<mod>.py`" within one clause.
_CONST_IN = re.compile(
    r"`([A-Z][A-Z0-9_]{2,})`(?:(?!`[A-Z][A-Z0-9_]{2,}`)[^.;:\n])*?\bin `(?:scripts/)?(\w+)\.py`"
)


def _module_assigns(name: str) -> set[str]:
    """Names a script assigns at module level (re-exported imports do not count)."""
    names: set[str] = set()
    for node in ast.parse((SCRIPTS / f"{name}.py").read_text()).body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        names |= {t.id for t in targets if isinstance(t, ast.Name)}
    return names


def _misplaced_constants(text: str) -> list[str]:
    bad = []
    for line in text.splitlines():
        for m in re.finditer(r"`[A-Z][A-Z0-9_]{2,}`", line):
            hit = _CONST_IN.match(line, m.start())
            if hit and _is_script(hit[2]) and hit[1] not in _module_assigns(hit[2]):
                bad.append(f"`{hit[1]}` in {hit[2]}.py")
    return bad


def test_docs_name_the_module_that_defines_a_constant():
    bad = [f"{doc.relative_to(REPO_ROOT)}: {b}"
           for doc in _LIVE_DOCS for b in _misplaced_constants(doc.read_text())]
    assert not bad, f"docs say a constant lives in a module that does not define it: {bad}"


def test_misplaced_constant_detector():
    assert _misplaced_constants("Windows (`RECENCY_DAYS` in `scripts/prefilter.py`: 7)") == [
        "`RECENCY_DAYS` in prefilter.py"]
    assert _misplaced_constants("Windows (`RECENCY_DAYS` in `scripts/gates.py`: 7)") == []
    assert _misplaced_constants("add `X` to `RECENCY_DAYS`. Then edit in `prefilter.py`") == []


def _fstring_source_builders(block: str) -> list[str]:
    """f-strings in a python block that spell an items.source value or a source label."""
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return re.findall(r"""(?:source=|yield )f["'][a-z_]+[:/]\{""", block)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and len(node.values) >= 2:
            first = node.values[0]
            if (isinstance(first, ast.Constant)
                    and re.fullmatch(r"[a-z_]+[:/]", str(first.value))
                    and isinstance(node.values[1], ast.FormattedValue)):
                hits.append(ast.unparse(node))
    return hits


def test_docs_build_source_values_only_via_models():
    bad = [f"{doc}: {h}" for doc, block in _blocks() for h in _fstring_source_builders(block)]
    assert not bad, (
        "build Item.source with models.item_source and the per-source label with "
        f"models.source_label, never an f-string: {bad}"
    )


def test_source_builder_detector():
    assert _fstring_source_builders("x = Item(source=f\"x:{s['id']}\")") == ["f\"x:{s['id']}\""]
    assert _fstring_source_builders("yield f\"x/{src['id']}\", gen") == ["f\"x/{src['id']}\""]
    assert _fstring_source_builders("u = f\"https://x.com/{user}\"") == []


def test_docs_do_not_hardcode_check_edition_family_tables():
    pattern = re.compile(r"^\s*(FAMILIES|REQUIRED_KEYS)\s*=\s*[({]", re.M)
    bad = [str(doc) for doc, block in _blocks() if pattern.search(block)]
    assert not bad, ("check_edition.FAMILIES / REQUIRED_KEYS are derived from "
                     f"models.SourceFamily / SOURCE_REQUIRED_KEYS; docs must not spell them: {bad}")
