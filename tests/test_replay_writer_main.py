"""Tests for replay_writer.py main() pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def db_path(tmp_path):
    import db as db_mod
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    return p


def _insert_item(conn, item_id, section="papers", score=8, status="published"):
    conn.execute(
        """
        INSERT OR REPLACE INTO items (
            id, source, url, canonical_url, title, author, published_at,
            fetched_at, raw_text, score, tags, why, status, section,
            first_seen_date, last_seen_date, appearances
        ) VALUES (
            ?, 'arxiv:x', 'https://a.com/' || ?, 'https://a.com/' || ?,
            'Test Paper', 'Author', '2026-07-09T00:00:00Z', '2026-07-09T00:00:00Z',
            'Abstract', ?, '[]', 'good', ?, ?,
            '2026-07-09', '2026-07-09', 1
        )
        """,
        (item_id, item_id, item_id, score, status, section),
    )
    conn.commit()


def _write_issue(issues_dir, date_str, featured_ids):
    if featured_ids:
        feat_items = "\n".join(
            f"- id: {fid}\n  source: arxiv:x\n  url: https://a.com/{fid}\n  title: Paper {fid}"
            for fid in featured_ids
        )
        featured_block = f"featured:\n{feat_items}"
    else:
        featured_block = "featured: []"
    content = (
        f'---\ndate: "{date_str}"\n'
        f'{featured_block}\n'
        f'appendix:\n  papers: []\n  news: []\n  blogs: []\n'
        f'---\nBody.\n'
    )
    (issues_dir / f"{date_str}.md").write_text(content)


class TestReplayWriterMainArgs:
    def test_returns_2_with_no_args(self, monkeypatch):
        import replay_writer as rw
        monkeypatch.setattr("sys.argv", ["replay_writer.py"])
        assert rw.main() == 2

    def test_returns_2_with_too_many_args(self, monkeypatch):
        import replay_writer as rw
        monkeypatch.setattr("sys.argv", ["replay_writer.py", "2026-06-01", "extra"])
        assert rw.main() == 2


class TestReplayWriterMainNoFeatured:
    def test_returns_1_when_no_featured(self, db_path, tmp_path, monkeypatch):
        import replay_writer as rw
        import db as db_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _write_issue(issues_dir, "2026-06-01", [])
        monkeypatch.setattr("sys.argv", ["replay_writer.py", "2026-06-01"])
        monkeypatch.setattr(rw, "ISSUES_DIR", issues_dir)
        monkeypatch.setattr(rw, "connect", lambda: db_mod.connect(db_path))
        monkeypatch.setattr(rw, "init_db", lambda: db_mod.init_db(db_path))
        assert rw.main() == 1


class TestReplayWriterMainNormal:
    def test_calls_invoke_writer(self, db_path, tmp_path, monkeypatch):
        import replay_writer as rw
        import db as db_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        prompt_file = tmp_path / "write.md"
        prompt_file.write_text("# Rubric")
        conn = db_mod.connect(db_path)
        _insert_item(conn, "feat1")
        conn.close()
        _write_issue(issues_dir, "2026-06-01", ["feat1"])
        monkeypatch.setattr("sys.argv", ["replay_writer.py", "2026-06-01"])
        monkeypatch.setattr(rw, "ISSUES_DIR", issues_dir)
        monkeypatch.setattr(rw, "PROMPT_PATH", prompt_file)
        monkeypatch.setattr(rw, "connect", lambda: db_mod.connect(db_path))
        monkeypatch.setattr(rw, "init_db", lambda: db_mod.init_db(db_path))
        monkeypatch.setattr("replay_writer.REPO_ROOT", tmp_path)
        mock_output = {"theme": "Test theme", "items": [{"id": "feat1", "summary": "s"}]}
        with patch("replay_writer.invoke_writer", return_value=mock_output) as mock_writer:
            result = rw.main()
        assert result == 0
        mock_writer.assert_called_once()

    def test_writes_log_file(self, db_path, tmp_path, monkeypatch):
        import replay_writer as rw
        import db as db_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        prompt_file = tmp_path / "write.md"
        prompt_file.write_text("# Rubric")
        conn = db_mod.connect(db_path)
        _insert_item(conn, "feat1")
        conn.close()
        _write_issue(issues_dir, "2026-06-01", ["feat1"])
        monkeypatch.setattr("sys.argv", ["replay_writer.py", "2026-06-01"])
        monkeypatch.setattr(rw, "ISSUES_DIR", issues_dir)
        monkeypatch.setattr(rw, "PROMPT_PATH", prompt_file)
        monkeypatch.setattr(rw, "connect", lambda: db_mod.connect(db_path))
        monkeypatch.setattr(rw, "init_db", lambda: db_mod.init_db(db_path))
        monkeypatch.setattr("replay_writer.REPO_ROOT", tmp_path)
        mock_output = {"theme": "My theme", "items": [{"id": "feat1", "summary": "ok"}]}
        with patch("replay_writer.invoke_writer", return_value=mock_output):
            rw.main()
        log_path = tmp_path / "logs" / "theme-replay-2026-06-01.json"
        assert log_path.exists()
        data = json.loads(log_path.read_text())
        assert data["date"] == "2026-06-01"
        assert data["theme"] == "My theme"
