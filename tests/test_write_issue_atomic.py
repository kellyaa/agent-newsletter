"""The issue file appears whole or not at all (write.write_atomic).

Its existence is the "write finished" signal: write and rank skip the day once
``issues/<date>.md`` exists, and publish seals the DB against it. write used a
plain ``write_text``, so a write that died part way (disk full, killed) left a
truncated file: every ``run.sh --failed`` retry then skipped rank and write,
and publish failed as ISSUE_TOO_SMALL (or shipped a cut-off issue) until the
file was deleted by hand.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from conftest import base_template

from models import ISSUE_GLOB

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture()
def core(tmp_path, monkeypatch):
    import db as db_mod
    import write as write_mod

    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, published_at, fetched_at, "
        "status, section, score, tags, why, first_seen_date, last_seen_date) "
        "VALUES ('f1','arxiv:cs','http://a/1','http://a/1','T','2026-07-09','2026-07-09',"
        "'featured','papers',9,'[]','why','2026-07-09','2026-07-09')"
    )
    conn.commit()
    issues = tmp_path / "issues"
    prompt = tmp_path / "write.md"
    prompt.write_text(base_template("write"))
    monkeypatch.setattr(write_mod, "issues_dir", lambda: issues)
    monkeypatch.setattr(write_mod, "PROMPT_PATH", prompt)
    monkeypatch.setattr(
        write_mod, "invoke_writer",
        lambda p: {"theme": "Pinned theme", "items": [{"id": "f1", "summary": "Pinned prose."}]},
    )
    monkeypatch.setattr(write_mod.prompts, "render_and_log", lambda *a, **k: "rubric")
    yield write_mod, conn, issues
    conn.close()


def _die_part_way(monkeypatch):
    """Make Path.write_text of the issue (or its temp file) write half its
    text, then raise; other writes (debug artifacts) go through."""
    real = Path.write_text

    def partial(self, data, *a, **k):
        if ".md" not in self.name:
            return real(self, data, *a, **k)
        real(self, data[: len(data) // 2], *a, **k)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", partial)
    return real


def test_failed_write_leaves_no_issue_and_retry_writes_it(core, monkeypatch):
    write_mod, conn, issues = core
    real = _die_part_way(monkeypatch)
    with pytest.raises(OSError):
        write_mod.write_issue(conn, "2026-07-09")
    # No truncated issue that would make the retry skip, and no temp file left.
    assert list(issues.iterdir()) == []

    monkeypatch.setattr(Path, "write_text", real)
    out = write_mod.write_issue(conn, "2026-07-09")
    assert out == issues / "2026-07-09.md"
    assert "Pinned prose." in out.read_text()
    assert [p.name for p in issues.iterdir()] == ["2026-07-09.md"]


def test_failed_rewrite_keeps_the_previous_file(tmp_path, monkeypatch):
    import write as write_mod

    path = tmp_path / "2026-07-09.md"
    path.write_text("old complete issue")
    _die_part_way(monkeypatch)
    with pytest.raises(OSError):
        write_mod.write_atomic(path, "new issue text")
    assert path.read_text() == "old complete issue"
    assert [p.name for p in tmp_path.iterdir()] == ["2026-07-09.md"]


def test_temp_file_is_not_an_issue(tmp_path, monkeypatch):
    # A temp file left by a hard kill must not be read as an issue by
    # find_previous_newsletter or the site (both glob ISSUE_GLOB).
    import write as write_mod

    seen = []
    real_replace = write_mod.os.replace
    monkeypatch.setattr(
        write_mod.os, "replace",
        lambda src, dst: seen.append(Path(src)) or real_replace(src, dst),
    )
    write_mod.write_atomic(tmp_path / "sub" / "2026-07-09.md", "x")
    (tmp,) = seen
    assert tmp.parent == tmp_path / "sub"  # same directory, so the rename is atomic
    assert not tmp.match(ISSUE_GLOB)
    assert tmp.name.startswith(".")


def test_write_issue_writes_only_through_write_atomic():
    tree = ast.parse((SCRIPTS / "write.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "write_issue")
    attrs = {n.func.attr for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    names = {n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "write_atomic" in names
    assert "write_text" not in attrs
