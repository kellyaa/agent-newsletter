"""Pipeline stages return models.ExitCode from main(), not bare integers.

fetch, prefilter, rank, write and publish each picked their own exit numbers
(1 = all sources failed, 2 = missing input, 4/5 = publish refusals). Those
numbers end up in run_attempts.exit_code, so a reader of a failed attempt
needs one place that says what each one means. This guard fails if a stage's
main() goes back to returning a bare number, and pins the values so a
renumbering can't silently change what old run_attempts rows mean.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from models import ExitCode

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
STAGES = ["fetch", "prefilter", "rank", "write", "publish"]


def _main_returns(stage: str) -> list[ast.Return]:
    tree = ast.parse((SCRIPTS / f"{stage}.py").read_text())
    main = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
    )

    def walk(node: ast.AST):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # a nested helper's returns are not exit codes
            yield child
            yield from walk(child)

    return [n for n in walk(main) if isinstance(n, ast.Return)]


@pytest.mark.parametrize("stage", STAGES)
def test_stage_main_returns_named_exit_codes(stage):
    returns = _main_returns(stage)
    assert returns, f"{stage}.main() has no return statements"
    offenders = [
        (r.lineno, ast.unparse(r.value) if r.value else "None")
        for r in returns
        if not (
            isinstance(r.value, ast.Attribute)
            and isinstance(r.value.value, ast.Name)
            and r.value.value.id == "ExitCode"
            and r.value.attr in ExitCode.__members__
        )
    ]
    assert offenders == [], f"{stage}.main() must return models.ExitCode.*: {offenders}"


def test_exit_code_values_are_stable():
    # run_attempts.exit_code stores these numbers; changing one changes what
    # existing rows mean.
    assert {c.name: c.value for c in ExitCode} == {
        "OK": 0,
        "NO_DATA": 1,
        "MISSING_INPUT": 2,
        "ISSUE_TOO_SMALL": 4,
        "EMPTY_ISSUE": 5,
    }


def test_exit_codes_are_ints_for_sys_exit():
    # sys.exit(main()) must keep producing a numeric process status.
    assert all(isinstance(c, int) for c in ExitCode)
    assert ExitCode.OK == 0
