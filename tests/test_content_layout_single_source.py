"""The content-branch layout and run-commit subjects live in models.py.

scripts/run_edition.sh writes ``editions/<slug>/state.db`` and commits it as
``newsletter(<slug>): <date> daily run`` (the subject is built by
scripts/contentbranch.sh, #665). backfill.py reads both back (it finds
the last run commit before a date and extracts that commit's state.db), and
edition.py and migrate_layout.py build the same paths. Each used to keep its own
copy, and no test tied the shell writer to the Python reader. Backfill's tests
fed it hand-written ``git log`` lines, so a change to the shell subject would
pass CI and break the next real backfill (#576).

These tests:
- check the shell's subjects (from scripts/contentbranch.sh) and paths against models
- run a real git round trip from the shell's subject through backfill
- fail if a module rebuilds the layout from literals instead of using models
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from models import (
    EDITIONS_DIRNAME,
    ISSUES_DIRNAME,
    LEGACY_EDITION,
    LEGACY_RUN_COMMIT_PREFIX,
    STATE_DB_NAME,
    edition_state_db_relpath,
    run_commit_prefix,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RUN_EDITION = SCRIPTS / "run_edition.sh"

CONTENT_LIB = SCRIPTS / "contentbranch.sh"
_EDITION_PATH_RE = re.compile(r'"(editions/\$EDITION[^"]*)"')


def _expand(template: str, slug: str, date: str, **extra: str) -> str:
    out = template.replace("$EDITION", slug).replace("$DATE_TODAY", date)
    for k, v in extra.items():
        out = out.replace(f"${k}", v)
    return out


def _lib(fn: str, *args: str) -> str:
    """Call a scripts/contentbranch.sh helper, the one place run subjects are built."""
    return subprocess.run(
        ["bash", "-c", f'. "{CONTENT_LIB}"; {fn} "$@"', "_", *args],
        capture_output=True, text=True, check=True,
    ).stdout.rstrip("\n")


# ---------------------------------------------------------------------------
# models vocabulary
# ---------------------------------------------------------------------------

def test_layout_vocabulary():
    assert edition_state_db_relpath("beta") == f"{EDITIONS_DIRNAME}/beta/{STATE_DB_NAME}"
    assert edition_state_db_relpath("beta") == "editions/beta/state.db"
    assert run_commit_prefix("beta") == "newsletter(beta): "
    assert LEGACY_RUN_COMMIT_PREFIX != run_commit_prefix(LEGACY_EDITION)


def test_runtime_paths_and_backfill_use_models(tmp_path):
    import backfill
    import edition

    paths = edition.runtime_paths(tmp_path, "beta")
    assert paths.state_db == tmp_path / edition_state_db_relpath("beta")
    assert paths.issues_dir == tmp_path / EDITIONS_DIRNAME / "beta" / ISSUES_DIRNAME
    assert backfill.edition_db_path("beta") == edition_state_db_relpath("beta")
    assert backfill.LEGACY_EDITION == LEGACY_EDITION


def test_migrate_layout_uses_the_same_legacy_edition():
    import migrate_layout

    assert migrate_layout.LEGACY_EDITION == LEGACY_EDITION


# ---------------------------------------------------------------------------
# run_edition.sh (writer) agrees with models (reader)
# ---------------------------------------------------------------------------

def test_run_edition_builds_subjects_with_the_library():
    text = RUN_EDITION.read_text()
    assert '"$(content_run_subject "$EDITION" "$DATE_TODAY")"' in text
    assert '"$(content_failed_subject "$EDITION" "$DATE_TODAY" "$RUN_ATTEMPT" "$1")"' in text


@pytest.mark.parametrize("slug", [LEGACY_EDITION, "beta"])
def test_shell_commit_subjects_match_models(slug):
    # the daily run commit and the failed-attempt commit
    subjects = [_lib("content_run_subject", slug, "2026-07-09"),
                _lib("content_failed_subject", slug, "2026-07-09", "3", "rank")]
    assert subjects[0].endswith("daily run"), subjects
    for s in subjects:
        assert s.startswith(run_commit_prefix(slug) + "2026-07-09 "), (
            f"scripts/contentbranch.sh subject {s!r} no longer starts with "
            "models.run_commit_prefix(<slug>) + '<date> '; backfill.find_pre_run_commit "
            "would not find it. Change both together."
        )


def test_shell_paths_match_models():
    paths = set(_EDITION_PATH_RE.findall(RUN_EDITION.read_text()))
    assert paths, "run_edition.sh no longer stages editions/$EDITION"
    for p in paths:
        expanded = _expand(p, "beta", "2026-07-09")
        if expanded.endswith(STATE_DB_NAME):
            assert expanded == edition_state_db_relpath("beta"), p
        else:
            assert expanded == f"{EDITIONS_DIRNAME}/beta", p
    assert any(p.endswith(STATE_DB_NAME) for p in paths)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
    ).stdout


@pytest.mark.parametrize("slug", [LEGACY_EDITION, "beta"])
def test_backfill_reads_what_run_edition_writes(tmp_path, monkeypatch, slug):
    """A real commit made with run_edition.sh's own subject and path is the
    snapshot backfill picks, with no hand-written log lines in between."""
    import backfill as bf

    db_rel = next(p for p in _EDITION_PATH_RE.findall(RUN_EDITION.read_text())
                  if p.endswith(STATE_DB_NAME))

    repo = tmp_path / "content"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "content")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.com")
    for date, payload in (("2026-07-08", b"OLD"), ("2026-07-09", b"PRE"), ("2026-07-10", b"SAME")):
        db = repo / _expand(db_rel, slug, date)
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(payload)
        _git(repo, "add", "--", _expand(db_rel, slug, date))
        _git(repo, "commit", "-q", "-m", _lib("content_run_subject", slug, date))

    sandbox = tmp_path / "sb"
    real_path = bf.Path
    monkeypatch.setattr(
        "backfill.Path", lambda x: sandbox if str(x).startswith("/tmp/backfill-") else real_path(x))
    monkeypatch.setattr("backfill.REPO", repo)
    monkeypatch.setenv("EDITION", slug)

    assert bf.setup_sandbox("2026-07-10") == sandbox
    assert (sandbox / STATE_DB_NAME).read_bytes() == b"PRE"


# ---------------------------------------------------------------------------
# no module rebuilds the layout from literals
# ---------------------------------------------------------------------------

# new_edition.py reads the repo's example edition (editions/agents/prompts) to
# draft a new one; that is repo config, not the content-branch layout.
_EXEMPT = {"models.py", "new_edition.py"}
_FORBIDDEN_EXACT = {EDITIONS_DIRNAME, LEGACY_EDITION, LEGACY_RUN_COMMIT_PREFIX}


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = n.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _layout_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstring_ids(tree)
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            v = n.value
            if v in _FORBIDDEN_EXACT or v.startswith("newsletter(") or v.startswith("editions/"):
                hits.append(f"{path.name}:{n.lineno}: {v!r}")
    return hits


def test_detector_flags_layout_literals(tmp_path):
    p = tmp_path / "x.py"
    p.write_text(
        '"""editions/<slug>/state.db is fine in a docstring."""\n'
        'a = root / "editions" / slug\n'
        'b = f"editions/{slug}/state.db"\n'
        'c = f"newsletter({slug}): "\n'
        'd = slug != "agents"\n'
        'e = "newsletter: "\n'
        'f = "see editions/_template for help"\n'
    )
    assert sorted(h.split(": ", 1)[0] for h in _layout_literals(p)) == [
        "x.py:2", "x.py:3", "x.py:4", "x.py:5", "x.py:6"]


def test_no_module_rebuilds_the_content_layout():
    hits = [h for p in sorted(SCRIPTS.glob("*.py")) if p.name not in _EXEMPT
            for h in _layout_literals(p)]
    assert not hits, (
        "content-branch layout / run-commit literals outside models.py; use "
        "models.EDITIONS_DIRNAME / STATE_DB_NAME / ISSUES_DIRNAME / LEGACY_EDITION / "
        f"edition_state_db_relpath / run_commit_prefix instead: {hits}"
    )
