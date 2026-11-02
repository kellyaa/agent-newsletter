"""Section and candidate-bucket names come from one place.

write.py used to spell out {"papers": [], "news": [], "blogs": []} and the
ranker/prefilter/backfill each re-typed the "papers_prescored" bucket key that
candidates.py emits. Renaming a section in models.Section, or the bucket in
candidates.PAPERS_PRESCORED, would then leave those modules silently reading
an empty bucket. These guards fail if a bare literal comes back. Docstrings
and comments are ignored.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from candidates import PAPERS_PRESCORED
from models import Section

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SECTION_NAMES = {s.value for s in Section}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _string_literals(path: Path, names: set[str]) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text())
    skip = _docstring_nodes(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and node.value in names
    ]


@pytest.mark.parametrize("module", ["rank.py", "prefilter.py", "backfill.py", "write.py"])
def test_prescored_bucket_name_comes_from_candidates(module):
    offenders = _string_literals(SCRIPTS / module, {PAPERS_PRESCORED})
    assert offenders == [], f"use candidates.PAPERS_PRESCORED in {module}: {offenders}"


def test_write_has_no_bare_section_literals():
    # Only section names: "featured"/"appendix" in write.py are frontmatter
    # and writer-payload schema keys, not item statuses.
    offenders = _string_literals(SCRIPTS / "write.py", SECTION_NAMES)
    assert offenders == [], f"use models.Section in write.py: {offenders}"

