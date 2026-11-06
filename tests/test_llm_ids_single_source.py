"""The LLM id contract lives in llm_ids; rank and write only consume it (#586).

Both stages used to ask the model to copy 64-hex ids verbatim and handled a
mis-copy differently: the ranker silently dropped the item to the appendix,
the writer grew its own `_dup`/Hamming repair. These tests keep the id parsing
in one place and the stages on it.
"""
from __future__ import annotations

import ast
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# Names that implement the id contract; only llm_ids.py may define them.
_CONTRACT_NAMES = {"alias_items", "resolve_ids", "recover_writer_ids", "_hamming",
                   "_DUP_SUFFIX", "DUP_SUFFIX", "_MAX_ID_HAMMING"}


def _defined(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _called(path: Path, fn: str | None = None) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scope = tree
    if fn:
        scope = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fn)
    return {n.func.id for n in ast.walk(scope)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def test_contract_is_defined_only_in_llm_ids():
    assert {"alias_items", "resolve_ids"} <= _defined(SCRIPTS / "llm_ids.py")
    for script in sorted(SCRIPTS.glob("*.py")):
        if script.name == "llm_ids.py":
            continue
        leaked = _defined(script) & _CONTRACT_NAMES
        assert not leaked, f"{script.name} re-implements the LLM id contract: {sorted(leaked)}"


def test_rank_section_aliases_and_resolves():
    called = _called(SCRIPTS / "rank.py", "rank_section")
    assert {"alias_items", "resolve_ids"} <= called


def test_writer_calls_go_through_call_writer():
    assert {"alias_items", "resolve_ids"} <= (
        _called(SCRIPTS / "write.py", "call_writer")
        | _called(SCRIPTS / "write.py", "resolve_writer_output")
    )
    # Only call_writer builds a writer prompt from featured items, so no writer
    # call can skip aliasing.
    tree = ast.parse((SCRIPTS / "write.py").read_text(encoding="utf-8"))
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        if fn.name in ("call_writer", "build_writer_prompt"):
            continue
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "invoke_writer" not in calls or fn.name == "invoke_writer", (
            f"write.{fn.name} calls invoke_writer directly; use call_writer so ids are aliased")


def test_guard_detects_a_regression(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("def recover_writer_ids(a, b):\n    return b\n_DUP_SUFFIX = '_dup'\n")
    assert _defined(p) & _CONTRACT_NAMES == {"recover_writer_ids", "_DUP_SUFFIX"}
