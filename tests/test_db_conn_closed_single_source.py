"""Every state.db connection opened in ``scripts/`` is closed in a ``finally`` (#743, #744).

``publish.main`` (#743), then ``prefilter.main`` and ``rank.main`` (#744), then
``fetch.main`` and ``backfill.main`` each closed their connection by hand at the
end, so a step that raised in between leaked it. Each fix patched one module;
this guard pins the rule for all of them: an assignment ``x = connect(...)``
(``db.connect``, ``db.connect_readonly`` or ``sqlite3.connect``) must be
followed directly by a ``try`` whose ``finally`` calls ``x.close()``.

Exempt are the functions that hand the connection to their caller
(``db.connect``) and ``candidates.load_candidates_from_db``, which only closes a
connection it opened itself and whose ``try/finally`` follows the ``if``.
"""
from __future__ import annotations

import ast
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
CONNECT_NAMES = {"connect", "connect_readonly"}

# (file, function): why the assignment is not followed by try/finally close.
EXEMPT = {
    ("db.py", "connect"): "factory: returns the connection to its caller",
    ("candidates.py", "load_candidates_from_db"): "closes only a connection it opened (close_conn flag)",
}


def _is_connect_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
    return name in CONNECT_NAMES


def _finally_closes(stmt: ast.stmt, var: str) -> bool:
    if not isinstance(stmt, ast.Try):
        return False
    for node in stmt.finalbody:
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "close"
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == var
            ):
                return True
    return False


def _blocks(node: ast.AST):
    for field in ("body", "orelse", "finalbody"):
        block = getattr(node, field, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            yield block
    for h in getattr(node, "handlers", []) or []:
        yield h.body


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    out: list[str] = []

    def visit(node: ast.AST, func: str | None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        for block in _blocks(node):
            for i, stmt in enumerate(block):
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and _is_connect_call(stmt.value)
                    and (path.name, func) not in EXEMPT
                ):
                    var = stmt.targets[0].id
                    nxt = block[i + 1] if i + 1 < len(block) else None
                    if nxt is None or not _finally_closes(nxt, var):
                        out.append(f"{path.name}:{stmt.lineno} {func}(): {var} = connect(...)")
                visit(stmt, func)

    visit(tree, None)
    return out


def test_every_connection_is_closed_in_a_finally():
    bad = [v for p in sorted(SCRIPTS.glob("*.py")) for v in _violations(p)]
    assert not bad, (
        "close every state.db connection in try/finally right after opening it:\n  "
        + "\n  ".join(bad)
    )


def test_exemptions_still_exist():
    """A stale exemption would silently cover a new function of the same name."""
    for fname, func in EXEMPT:
        tree = ast.parse((SCRIPTS / fname).read_text())
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert func in names, f"{fname}:{func} is exempt but no longer exists"


def test_guard_flags_a_hand_closed_connection(tmp_path):
    p = tmp_path / "x.py"
    p.write_text(
        "def main():\n"
        "    conn = connect()\n"
        "    do(conn)\n"
        "    conn.close()\n"
    )
    assert _violations(p) == ["x.py:2 main(): conn = connect(...)"]


def test_guard_accepts_try_finally_close(tmp_path):
    p = tmp_path / "x.py"
    p.write_text(
        "def main():\n"
        "    conn = db.connect(path)\n"
        "    try:\n"
        "        do(conn)\n"
        "    finally:\n"
        "        conn.close()\n"
    )
    assert _violations(p) == []
