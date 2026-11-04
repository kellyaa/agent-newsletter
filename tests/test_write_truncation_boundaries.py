"""Boundary tests for the writer-input truncation limits in write.py and
replay_writer.py (RAW_TEXT_MAX, PREV_NEWSLETTER_MAX).

The older tests in test_write_db.py / test_replay_writer.py assert
``len(raw) <= RAW_TEXT_MAX + len(marker)``, which a too-aggressive cut still
satisfies, and none of them hit the exact limit.  These pin the off-by-one
edge (limit chars: untouched; limit+1: cut) and the exact kept prefix.
"""
from __future__ import annotations

import pytest

from replay_writer import load_from_issue_file
from write import (
    PREV_NEWSLETTER_MAX,
    RAW_TEXT_MAX,
    build_writer_input,
    load_today_items,
)

MARKER = "\n...[truncated]"


@pytest.fixture()
def db(tmp_path):
    import db as db_mod
    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    yield conn
    conn.close()


def _insert(conn, item_id: str, raw_text: str, status: str = "featured") -> None:
    conn.execute(
        """
        INSERT INTO items (
            id, source, url, canonical_url, title, author, published_at,
            fetched_at, raw_text, score, tags, why, status, section,
            first_seen_date, last_seen_date, appearances
        ) VALUES (
            ?, 'arxiv:cs.AI', 'https://arxiv.org/abs/' || ?, 'https://arxiv.org/abs/' || ?,
            'T', 'A', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z',
            ?, 8, '[]', 'why', ?, 'papers', '2026-06-01', '2026-06-01', 1
        )
        """,
        (item_id, item_id, item_id, raw_text, status),
    )
    conn.commit()


def _payload(prev):
    return build_writer_input(
        "2026-06-01", [], {"papers": [], "news": [], "blogs": []}, {}, prev
    )


class TestRawTextLimit:
    def test_exactly_at_limit_is_untouched(self, db):
        text = "a" * RAW_TEXT_MAX
        _insert(db, "p1", text)
        featured, _, _ = load_today_items(db, "2026-06-01")
        assert featured[0]["raw_text"] == text

    def test_one_over_limit_keeps_exact_prefix_plus_marker(self, db):
        text = "a" * RAW_TEXT_MAX + "b"
        _insert(db, "p1", text)
        featured, _, _ = load_today_items(db, "2026-06-01")
        assert featured[0]["raw_text"] == "a" * RAW_TEXT_MAX + MARKER

    def test_far_over_limit_keeps_exactly_limit_chars(self, db):
        _insert(db, "p1", "x" * (RAW_TEXT_MAX * 3))
        featured, _, _ = load_today_items(db, "2026-06-01")
        assert featured[0]["raw_text"] == "x" * RAW_TEXT_MAX + MARKER
        assert len(featured[0]["raw_text"]) == RAW_TEXT_MAX + len(MARKER)

    def test_null_raw_text_becomes_empty_string(self, db):
        _insert(db, "p1", None)
        featured, _, _ = load_today_items(db, "2026-06-01")
        assert featured[0]["raw_text"] == ""


class TestPreviousNewsletterLimit:
    def test_exactly_at_limit_is_untouched(self):
        prev = "p" * PREV_NEWSLETTER_MAX
        assert _payload(prev)["previous_newsletter"] == prev

    def test_one_over_limit_drops_only_the_overflow(self):
        prev = "p" * PREV_NEWSLETTER_MAX + "Z"
        assert _payload(prev)["previous_newsletter"] == "p" * PREV_NEWSLETTER_MAX

    def test_keeps_the_head_not_the_tail(self):
        prev = "HEAD" + "m" * PREV_NEWSLETTER_MAX + "TAIL"
        kept = _payload(prev)["previous_newsletter"]
        assert kept.startswith("HEAD")
        assert "TAIL" not in kept

    def test_empty_string_is_omitted(self):
        assert "previous_newsletter" not in _payload("")


class TestReplayWriterRawTextLimit:
    """replay_writer keeps its own copy of the truncation; keep it in step."""

    def _issue(self, tmp_path, monkeypatch):
        import replay_writer as rw
        issues = tmp_path / "issues"
        issues.mkdir()
        (issues / "2026-06-01.md").write_text(
            "---\n"
            'date: "2026-06-01"\n'
            "featured:\n"
            "- id: p1\n"
            "  source: arxiv:cs.AI\n"
            "  url: https://arxiv.org/abs/p1\n"
            "  title: T\n"
            "appendix:\n  papers: []\n  news: []\n  blogs: []\n"
            "---\nbody\n"
        )
        monkeypatch.setattr(rw, "ISSUES_DIR", issues)

    def test_exactly_at_limit_is_untouched(self, db, tmp_path, monkeypatch):
        self._issue(tmp_path, monkeypatch)
        text = "a" * RAW_TEXT_MAX
        _insert(db, "p1", text, status="published")
        featured, _, _ = load_from_issue_file(db, "2026-06-01")
        assert featured[0]["raw_text"] == text

    def test_one_over_limit_keeps_exact_prefix_plus_marker(self, db, tmp_path, monkeypatch):
        self._issue(tmp_path, monkeypatch)
        _insert(db, "p1", "a" * RAW_TEXT_MAX + "b", status="published")
        featured, _, _ = load_from_issue_file(db, "2026-06-01")
        assert featured[0]["raw_text"] == "a" * RAW_TEXT_MAX + MARKER
