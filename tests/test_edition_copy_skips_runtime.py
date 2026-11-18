"""Copying an edition folder takes its config, never its runtime state (#759).

With ``CONTENT_ROOT`` unset (any dev checkout outside run.sh),
``edition.runtime_paths`` puts ``state.db`` and ``issues/`` inside
``editions/<slug>/``, next to ``edition.yaml``. Both are .gitignored, so they
are invisible to ``git status``, but a bare ``shutil.copytree`` of the folder
copied them. The check_edition e2e test then failed on any machine that had
run a stage locally (its "dry run never touches the content tree" assertion
saw the copied state.db), and a fixture or scaffold built that way started
from another run's DB.

``edition.copy_config`` is the one copy and skips ``RUNTIME_ARTIFACTS``. The
guard below keeps every edition-folder copy in scripts/ and tests/ on it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import edition
import new_edition
from models import ISSUES_DIRNAME, STATE_DB_NAME

ROOT = Path(__file__).resolve().parent.parent
GUARDED_DIRS = (ROOT / "scripts", ROOT / "tests")


def _seed_runtime_state(folder: Path) -> None:
    for name in edition.RUNTIME_ARTIFACTS:
        if name == ISSUES_DIRNAME:
            (folder / name).mkdir()
            (folder / name / "2026-01-01.md").write_text("old issue")
        else:
            (folder / name).write_bytes(b"old state")
    (folder / "__pycache__").mkdir()


def test_runtime_artifacts_match_runtime_paths(tmp_path):
    # Every in-folder path runtime_paths() hands out must be skipped by the copy.
    paths = edition.runtime_paths(tmp_path, "agents")
    folder = tmp_path / "editions" / "agents"
    for p in (paths.state_db, paths.issues_dir):
        assert p.parent == folder
        assert p.name in edition.RUNTIME_ARTIFACTS
    assert STATE_DB_NAME in edition.RUNTIME_ARTIFACTS
    for suffix in ("-wal", "-shm", "-journal"):
        assert f"{STATE_DB_NAME}{suffix}" in edition.RUNTIME_ARTIFACTS


def test_copy_config_skips_runtime_state(tmp_path):
    src = edition.copy_config(edition.EDITIONS_DIR / "agents", tmp_path / "src")
    _seed_runtime_state(src)
    dest = edition.copy_config(src, tmp_path / "dest")
    assert dest == tmp_path / "dest"
    for name in (*edition.RUNTIME_ARTIFACTS, "__pycache__"):
        assert not (dest / name).exists(), name
    assert (dest / "edition.yaml").read_text() == (src / "edition.yaml").read_text()
    assert (dest / "sources.yaml").exists()
    assert sorted(p.name for p in (dest / "prompts").iterdir()) == sorted(
        p.name for p in (src / "prompts").iterdir()
    )


def test_copy_config_tree_skips_nested_state_and_excludes(tmp_path):
    src = tmp_path / "editions"
    edition.copy_config(edition.EDITIONS_DIR, src)
    _seed_runtime_state(src / "agents")
    dest = edition.copy_config(src, tmp_path / "copy", "engineering-with-ai")
    assert (dest / "agents" / "edition.yaml").exists()
    assert not (dest / "engineering-with-ai").exists()
    for name in edition.RUNTIME_ARTIFACTS:
        assert not (dest / "agents" / name).exists(), name
    assert edition.load("agents", editions_dir=dest).slug == "agents"


def test_scaffold_does_not_inherit_template_state(tmp_path, monkeypatch):
    editions_dir = edition.copy_config(edition.EDITIONS_DIR, tmp_path / "editions",
                                       "engineering-with-ai")
    monkeypatch.setattr(edition, "EDITIONS_DIR", editions_dir)
    _seed_runtime_state(editions_dir / new_edition.TEMPLATE_SLUG)
    dest = new_edition.scaffold("fresh", "Fresh", "", [], use_llm=False)
    for name in edition.RUNTIME_ARTIFACTS:
        assert not (dest / name).exists(), name
    assert (dest / "edition.yaml").exists()


# --- guard: no bare copytree of an edition folder --------------------------------

def _mentions_editions(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in {"EDITIONS_DIR", "EDITIONS_DIRNAME"}:
            return True
        if isinstance(sub, ast.Name) and sub.id in {"REAL", "editions_dir", "template"}:
            return True
        if isinstance(sub, ast.Constant) and sub.value == "editions":
            return True
    return False


def _bare_edition_copies(tree: ast.AST) -> list[int]:
    hits = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "copytree"
            and node.args
            and _mentions_editions(node.args[0])
        ):
            hits.append(node.lineno)
    return hits


def test_no_bare_copytree_of_an_edition_folder():
    offenders = []
    for d in GUARDED_DIRS:
        for path in sorted(d.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for lineno in _bare_edition_copies(tree):
                if path.name == "edition.py" and path.parent.name == "scripts":
                    continue  # copy_config itself
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, (
        "copy edition folders with edition.copy_config (skips state.db/issues): "
        + ", ".join(offenders)
    )


@pytest.mark.parametrize("src, flagged", [
    ('shutil.copytree(REPO / "editions" / "agents", d)', True),
    ("shutil.copytree(edition.EDITIONS_DIR / '_template', d)", True),
    ("shutil.copytree(REAL, d, ignore=x)", True),
    ("shutil.copytree(template, dest)", True),
    ('shutil.copytree(REPO_ROOT / "scripts", d)', False),
    ("edition.copy_config(REAL, d)", False),
])
def test_guard_self_check(src, flagged):
    assert bool(_bare_edition_copies(ast.parse(src))) is flagged
