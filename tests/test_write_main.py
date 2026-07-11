"""Tests for write.py: main() pipeline and invoke_writer() error path."""
from __future__ import annotations
import contextlib

import json
from datetime import date
from unittest.mock import patch

import pytest


@pytest.fixture()
def db_path(tmp_path):
    import db as db_mod
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    return p


@pytest.fixture()
def issues_dir(tmp_path):
    d = tmp_path / "issues"
    d.mkdir()
    return d


@pytest.fixture()
def prompt_file(tmp_path):
    p = tmp_path / "write.md"
    p.write_text("# Write rubric\nWrite good prose.")
    return p


def _insert_featured(conn, item_id, section="papers", score=8):
    conn.execute(
        """
        INSERT OR REPLACE INTO items (
            id, source, url, canonical_url, title, author, published_at,
            fetched_at, raw_text, score, tags, why, status, section,
            first_seen_date, last_seen_date, appearances
        ) VALUES (
            ?, 'arxiv:x', 'https://a.com/' || ?, 'https://a.com/' || ?,
            'Test Paper', 'Author', '2026-07-09T00:00:00Z', '2026-07-09T00:00:00Z',
            'Abstract', ?, '["evals"]', 'good', 'featured', ?,
            '2026-07-09', '2026-07-09', 1
        )
        """,
        (item_id, item_id, item_id, score, section),
    )
    conn.commit()


def _insert_appendix(conn, item_id, section="blogs"):
    conn.execute(
        """
        INSERT OR REPLACE INTO items (
            id, source, url, canonical_url, title, fetched_at,
            status, section, first_seen_date, last_seen_date, appearances
        ) VALUES (
            ?, 'rss:x', 'https://a.com/' || ?, 'https://a.com/' || ?,
            'Appendix Item', '2026-07-09T00:00:00Z',
            'appendix', ?,
            '2026-07-09', '2026-07-09', 1
        )
        """,
        (item_id, item_id, item_id, section),
    )
    conn.commit()


def _run_main(db_path, issues_dir, prompt_file, monkeypatch):
    import write as write_mod
    import db as db_mod
    monkeypatch.setattr(write_mod, "ISSUES_DIR", issues_dir)
    monkeypatch.setattr(write_mod, "PROMPT_PATH", prompt_file)
    monkeypatch.setattr(write_mod, "connect", lambda: db_mod.connect(db_path))
    import contextlib
    @contextlib.contextmanager
    def _mc(p=None):
        c = db_mod.connect(db_path)
        try:
            yield c
        finally:
            c.close()
    monkeypatch.setattr(write_mod, "managed_connect", _mc)
    monkeypatch.setattr(write_mod, "init_db", lambda: db_mod.init_db(db_path))
    return write_mod.main()


class TestWriteMainGuards:
    def test_returns_2_when_prompt_missing(self, db_path, issues_dir, tmp_path, monkeypatch):
        import write as write_mod
        import db as db_mod
        monkeypatch.setattr(write_mod, "ISSUES_DIR", issues_dir)
        monkeypatch.setattr(write_mod, "PROMPT_PATH", tmp_path / "nonexistent.md")
        monkeypatch.setattr(write_mod, "connect", lambda: db_mod.connect(db_path))
        import contextlib
        @contextlib.contextmanager
        def _mc(p=None):
            c = db_mod.connect(db_path)
            try:
                yield c
            finally:
                c.close()
        monkeypatch.setattr(write_mod, "managed_connect", _mc)
        monkeypatch.setattr(write_mod, "init_db", lambda: db_mod.init_db(db_path))
        result = write_mod.main()
        assert result == 2

    def test_returns_0_skip_when_issue_already_exists(self, db_path, issues_dir, prompt_file, monkeypatch):
        today = date.today().isoformat()
        (issues_dir / f"{today}.md").write_text("---\ndate: existing\n---\nBody")
        result = _run_main(db_path, issues_dir, prompt_file, monkeypatch)
        assert result == 0
        assert "existing" in (issues_dir / f"{today}.md").read_text()

    def test_returns_0_when_nothing_to_publish(self, db_path, issues_dir, prompt_file, monkeypatch):
        result = _run_main(db_path, issues_dir, prompt_file, monkeypatch)
        assert result == 0


class TestWriteMainAppendixOnly:
    def test_appendix_only_skips_llm(self, db_path, issues_dir, prompt_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_appendix(conn, "app1")
        conn.close()
        with patch("write.invoke_writer") as mock_writer:
            result = _run_main(db_path, issues_dir, prompt_file, monkeypatch)
        assert result == 0
        mock_writer.assert_not_called()

    def test_appendix_only_writes_file(self, db_path, issues_dir, prompt_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_appendix(conn, "app1")
        conn.close()
        with patch("write.invoke_writer"):
            _run_main(db_path, issues_dir, prompt_file, monkeypatch)
        today = date.today().isoformat()
        assert (issues_dir / f"{today}.md").exists()


class TestWriteMainNormal:
    def test_normal_calls_invoke_writer(self, db_path, issues_dir, prompt_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_featured(conn, "feat1", section="papers", score=8)
        conn.close()
        mock_output = {
            "theme": "Agents take over",
            "items": [{"id": "feat1", "summary": "Great paper.", "takeaway": None, "open_question": None}],
        }
        with patch("write.invoke_writer", return_value=mock_output) as mock_writer:
            result = _run_main(db_path, issues_dir, prompt_file, monkeypatch)
        assert result == 0
        mock_writer.assert_called_once()

    def test_normal_writes_issue_file(self, db_path, issues_dir, prompt_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_featured(conn, "feat1")
        conn.close()
        mock_output = {
            "theme": "Test theme",
            "items": [{"id": "feat1", "summary": "Summary.", "takeaway": None, "open_question": None}],
        }
        with patch("write.invoke_writer", return_value=mock_output):
            _run_main(db_path, issues_dir, prompt_file, monkeypatch)
        today = date.today().isoformat()
        content = (issues_dir / f"{today}.md").read_text()
        assert "---" in content
        assert "feat1" in content

    def test_normal_issue_has_theme(self, db_path, issues_dir, prompt_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_featured(conn, "feat1")
        conn.close()
        mock_output = {
            "theme": "My special theme",
            "items": [{"id": "feat1", "summary": "Summary.", "takeaway": None, "open_question": None}],
        }
        with patch("write.invoke_writer", return_value=mock_output):
            _run_main(db_path, issues_dir, prompt_file, monkeypatch)
        today = date.today().isoformat()
        content = (issues_dir / f"{today}.md").read_text()
        assert "My special theme" in content


class TestInvokeWriter:
    def test_raises_when_items_missing(self, tmp_path):
        import write as write_mod
        with patch("write.call_llm", return_value={"theme": "x"}):
            with patch.object(write_mod, "REPO_ROOT", tmp_path):
                with pytest.raises(RuntimeError, match="writer returned no items"):
                    write_mod.invoke_writer("some prompt")

    def test_returns_output_when_items_present(self):
        import write as write_mod
        expected = {"theme": "t", "items": [{"id": "x", "summary": "s"}]}
        with patch("write.call_llm", return_value=expected):
            result = write_mod.invoke_writer("prompt")
        assert result is expected


def test_issues_dir_uses_content_root_env(monkeypatch, tmp_path):
    """ISSUES_DIR resolves under CONTENT_ROOT when the env var is set."""
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    import importlib
    import db
    importlib.reload(db)
    import write
    importlib.reload(write)
    assert write.ISSUES_DIR == tmp_path / "site" / "src" / "content" / "issues"
