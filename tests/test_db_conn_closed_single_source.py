"""A state.db connection opened in scripts/ is closed in a ``finally`` (#753).

Closing by hand at the end of a function leaks the connection whenever a step
in between raises (``database is locked``, a full disk, an LLM error). That bug
shipped in publish (#743), prefilter and rank (#744), then fetch and backfill
(#753), each fixed one module at a time.

Rule: every ``x = connect(...)`` / ``connect_readonly(...)`` /
``sqlite3.connect(...)`` assignment in ``scripts/*.py`` is directly followed by
a ``try`` whose ``finally`` calls ``x.close()``. A function that returns the
connection to its caller (``db.connect``, ``db.connect_readonly``) is exempt.
"""
from __future__ import annotations

import ast
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CONNECTORS = {"connect", "connect_readonly"}


def _is_connect_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in CONNECTORS
    return isinstance(f, ast.Attribute) and f.attr in CONNECTORS


def _closes(try_node: ast.Try, name: str) -> bool:
    for stmt in try_node.finalbody:
        for n in ast.walk(stmt):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "close"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == name
            ):
                return True
    return False


def _returns_name(func: ast.AST, name: str) -> bool:
    return any(
        isinstance(n, ast.Return) and isinstance(n.value, ast.Name) and n.value.id == name
        for n in ast.walk(func)
    )


def unguarded_connections(source: str) -> list[tuple[int, str]]:
    """(line, variable) of each connection not directly followed by try/finally close."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _is_connect_call(node.value)
        ):
            continue
        name = node.targets[0].id
        func = node
        while func in parents and not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = parents[func]
        if isinstance(func, ast.FunctionDef) and _returns_name(func, name):
            continue  # factory: the caller owns the close
        block = getattr(parents[node], "body", [])
        for field in ("body", "orelse", "finalbody", "handlers"):
            siblings = getattr(parents[node], field, [])
            if node in siblings:
                block = siblings
                break
        nxt = block[block.index(node) + 1] if block.index(node) + 1 < len(block) else None
        if not (isinstance(nxt, ast.Try) and _closes(nxt, name)):
            found.append((node.lineno, name))
    return found


def test_every_connection_in_scripts_is_closed_in_a_finally():
    offenders = []
    for path in sorted(SCRIPTS.glob("*.py")):
        for line, name in unguarded_connections(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}:{line} {name}")
    assert not offenders, (
        "open a state.db connection, then `try: ... finally: <conn>.close()` "
        "right after it: " + ", ".join(offenders)
    )


# --- the checker itself ------------------------------------------------------

def test_checker_flags_a_close_outside_finally():
    src = "def f():\n    conn = connect()\n    work(conn)\n    conn.close()\n"
    assert unguarded_connections(src) == [(2, "conn")]


def test_checker_flags_a_try_that_closes_another_name():
    src = (
        "def f():\n    a = connect()\n    try:\n        work(a)\n"
        "    finally:\n        b.close()\n"
    )
    assert unguarded_connections(src) == [(2, "a")]


def test_checker_flags_a_connection_last_in_its_block():
    assert unguarded_connections("def f():\n    conn = connect()\n") == [(2, "conn")]


def test_checker_accepts_try_finally_close():
    src = (
        "def f():\n    conn = db.connect_readonly(p)\n    try:\n        work(conn)\n"
        "    finally:\n        conn.close()\n"
    )
    assert unguarded_connections(src) == []


def test_checker_accepts_a_nested_connection_in_a_branch():
    src = (
        "def f(x):\n    if x:\n        conn = sqlite3.connect(p)\n        try:\n"
        "            work(conn)\n        finally:\n            conn.close()\n"
    )
    assert unguarded_connections(src) == []


def test_checker_exempts_factories_that_return_the_connection():
    src = "def connect(p):\n    conn = sqlite3.connect(p)\n    conn.execute('x')\n    return conn\n"
    assert unguarded_connections(src) == []
