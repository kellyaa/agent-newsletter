"""Tests for write.py DB layer: load_today_items(), find_previous_newsletter(),
and build_writer_input(). These functions have no LLM calls and are purely
deterministic — DB queries and file-system reads."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from write import (
    load_today_items,
    find_previous_newsletter,
    build_writer_input,
    RAW_TEXT_MAX,
    PREV_NEWSLETTER_MAX,
    READER_PROFILE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    import db as db_mod
    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    yield conn
    conn.close()


def _insert_item(
    conn,
    item_id: str,
    status: str,
    section: str = "papers",
    score: int | None = 8,
    tags: list | None = None,
    raw_text: str | None = "Abstract text",
    title: str = "Test Paper",
    author: str | None = "Smith et al.",
    last_seen_date: str = "2026-06-01",
) -> None:
    tags_json = json.dumps(tags or [])
    conn.execute(
        """
        INSERT OR REPLACE INTO items (
            id, source, url, canonical_url, title, author, published_at,
            fetched_at, raw_text, score, tags, why, status, section,
            first_seen_date, last_seen_date, appearances
        ) VALUES (
            ?, 'arxiv:cs.AI', 'https://arxiv.org/abs/' || ?, 'https://arxiv.org/abs/' || ?,
            ?, ?, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z',
            ?, ?, ?, 'good paper', ?, ?,
            '2026-06-01', ?, 1
        )
        """,
        (item_id, item_id, item_id, title, author, raw_text,
         score, tags_json, status, section, last_seen_date),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# load_today_items()
# ---------------------------------------------------------------------------

class TestLoadTodayItemsEmpty:
    def test_empty_db_returns_empty_results(self, db):
        featured, appendix, metadata = load_today_items(db, "2026-06-01")
        assert featured == []
        assert appendix == {"papers": [], "news": [], "blogs": []}
        assert metadata["items_featured_total"] == 0
        assert metadata["items_appendix"] == 0

    def test_items_considered_counts_last_seen_today(self, db):
        _insert_item(db, "i1", "featured", last_seen_date="2026-06-01")
        _insert_item(db, "i2", "dropped", last_seen_date="2026-05-30")  # different day
        _, _, metadata = load_today_items(db, "2026-06-01")
        assert metadata["items_considered"] == 1  # only today's


class TestLoadTodayItemsFeatured:
    def test_featured_items_returned(self, db):
        _insert_item(db, "p1", "featured", section="papers", score=9)
        featured, _, _ = load_today_items(db, "2026-06-01")
        assert len(featured) == 1
        assert featured[0]["id"] == "p1"
        assert featured[0]["score"] == 9
        assert featured[0]["section"] == "papers"

    def test_featured_item_fields_complete(self, db):
        _insert_item(db, "p1", "featured", section="news", score=7,
                     tags=["evals"], title="My Article", author="Jane")
        featured, _, _ = load_today_items(db, "2026-06-01")
        item = featured[0]
        assert item["id"] == "p1"
        assert item["section"] == "news"
        assert item["source"] == "arxiv:cs.AI"
        assert item["url"] == "https://arxiv.org/abs/p1"
        assert item["title"] == "My Article"
        assert item["author"] == "Jane"
        assert item["score"] == 7
        assert item["tags"] == ["evals"]
        assert item["why"] == "good paper"

    def test_multiple_sections_counted_correctly(self, db):
        _insert_item(db, "p1", "featured", section="papers")
        _insert_item(db, "p2", "featured", section="papers")
        _insert_item(db, "n1", "featured", section="news")
        _insert_item(db, "b1", "featured", section="blogs")
        _, _, metadata = load_today_items(db, "2026-06-01")
        assert metadata["items_featured_papers"] == 2
        assert metadata["items_featured_news"] == 1
        assert metadata["items_featured_blogs"] == 1
        assert metadata["items_featured_total"] == 4

    def test_tags_parsed_from_json(self, db):
        _insert_item(db, "p1", "featured", tags=["research", "evals"])
        featured, _, _ = load_today_items(db, "2026-06-01")
        assert featured[0]["tags"] == ["research", "evals"]

    def test_null_tags_returns_empty_list(self, db):
        # Insert with NULL tags field
        conn_inner = db
        conn_inner.execute(
            """
            INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
                status, section, score, first_seen_date, last_seen_date, appearances)
            VALUES ('null-tags','rss:x','https://a.com/1','https://a.com/1',
                    'T','2026-06-01T00:00:00Z','featured','blogs',7,
                    '2026-06-01','2026-06-01',1)
            """
        )
        conn_inner.commit()
        featured, _, _ = load_today_items(db, "2026-06-01")
        item = next(i for i in featured if i["id"] == "null-tags")
        assert item["tags"] == []

    def test_malformed_tags_json_falls_back_to_empty_list(self, db):
        conn_inner = db
        conn_inner.execute(
            """
            INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
                status, section, score, tags, first_seen_date, last_seen_date, appearances)
            VALUES ('bad-tags','rss:x','https://a.com/2','https://a.com/2',
                    'T','2026-06-01T00:00:00Z','featured','blogs',7,
                    'not-valid-json', '2026-06-01','2026-06-01',1)
            """
        )
        conn_inner.commit()
        featured, _, _ = load_today_items(db, "2026-06-01")
        item = next(i for i in featured if i["id"] == "bad-tags")
        assert item["tags"] == []

    def test_raw_text_truncated_at_max(self, db):
        long_text = "x" * (RAW_TEXT_MAX + 200)
        _insert_item(db, "p1", "featured", raw_text=long_text)
        featured, _, _ = load_today_items(db, "2026-06-01")
        raw = featured[0]["raw_text"]
        assert len(raw) <= RAW_TEXT_MAX + len("\n...[truncated]")
        assert raw.endswith("\n...[truncated]")

    def test_raw_text_not_truncated_when_short(self, db):
        short_text = "Short abstract"
        _insert_item(db, "p1", "featured", raw_text=short_text)
        featured, _, _ = load_today_items(db, "2026-06-01")
        assert featured[0]["raw_text"] == short_text

    def test_none_raw_text_becomes_empty_string(self, db):
        _insert_item(db, "p1", "featured", raw_text=None)
        featured, _, _ = load_today_items(db, "2026-06-01")
        assert featured[0]["raw_text"] == ""

    def test_featured_ordered_by_section_then_score_desc(self, db):
        # ORDER BY section, score DESC, id — section is alphabetical string order
        # "blogs" < "news" < "papers", so blogs comes first
        _insert_item(db, "p1", "featured", section="papers", score=7)
        _insert_item(db, "p2", "featured", section="papers", score=9)
        _insert_item(db, "b1", "featured", section="blogs", score=8)
        featured, _, _ = load_today_items(db, "2026-06-01")
        ids = [it["id"] for it in featured]
        # blogs before papers (alphabetical: "blogs" < "papers")
        assert ids.index("b1") < ids.index("p2")
        assert ids.index("b1") < ids.index("p1")
        # p2 (score=9) before p1 (score=7) within papers
        assert ids.index("p2") < ids.index("p1")


class TestLoadTodayItemsAppendix:
    def test_appendix_items_returned_by_section(self, db):
        _insert_item(db, "a1", "appendix", section="papers")
        _insert_item(db, "a2", "appendix", section="blogs")
        _, appendix, metadata = load_today_items(db, "2026-06-01")
        assert len(appendix["papers"]) == 1
        assert len(appendix["blogs"]) == 1
        assert metadata["items_appendix"] == 2

    def test_appendix_item_fields(self, db):
        _insert_item(db, "a1", "appendix", section="news", title="News Item", author="Bob")
        _, appendix, _ = load_today_items(db, "2026-06-01")
        item = appendix["news"][0]
        assert item["id"] == "a1"
        assert item["section"] == "news"
        assert item["title"] == "News Item"
        assert "url" in item

    def test_appendix_null_section_defaults_to_blogs(self, db):
        db.execute(
            """
            INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
                status, first_seen_date, last_seen_date, appearances)
            VALUES ('no-section','rss:x','https://a.com/3','https://a.com/3',
                    'T','2026-06-01T00:00:00Z','appendix','2026-06-01','2026-06-01',1)
            """
        )
        db.commit()
        _, appendix, _ = load_today_items(db, "2026-06-01")
        assert any(it["id"] == "no-section" for it in appendix["blogs"])

    def test_non_featured_statuses_excluded_from_featured(self, db):
        _insert_item(db, "cand", "candidate", section="papers")
        _insert_item(db, "drop", "dropped", section="papers")
        _insert_item(db, "feat", "featured", section="papers")
        featured, _, _ = load_today_items(db, "2026-06-01")
        ids = [it["id"] for it in featured]
        assert "feat" in ids
        assert "cand" not in ids
        assert "drop" not in ids


# ---------------------------------------------------------------------------
# find_previous_newsletter()
# ---------------------------------------------------------------------------

class TestFindPreviousNewsletter:
    def test_returns_none_when_issues_dir_absent(self, tmp_path, monkeypatch):
        import write as write_mod
        monkeypatch.setattr(write_mod, "ISSUES_DIR", tmp_path / "nonexistent")
        result = find_previous_newsletter("2026-06-10")
        assert result is None

    def test_returns_none_when_no_prior_files(self, tmp_path, monkeypatch):
        import write as write_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        # Only a file AFTER the target date
        (issues_dir / "2026-06-20.md").write_text("future")
        monkeypatch.setattr(write_mod, "ISSUES_DIR", issues_dir)
        result = find_previous_newsletter("2026-06-10")
        assert result is None

    def test_returns_most_recent_prior_file(self, tmp_path, monkeypatch):
        import write as write_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        (issues_dir / "2026-06-05.md").write_text("old issue")
        (issues_dir / "2026-06-08.md").write_text("recent issue")
        (issues_dir / "2026-06-15.md").write_text("future issue")
        monkeypatch.setattr(write_mod, "ISSUES_DIR", issues_dir)
        result = find_previous_newsletter("2026-06-10")
        assert result == "recent issue"

    def test_returns_file_content_not_path(self, tmp_path, monkeypatch):
        import write as write_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        (issues_dir / "2026-06-01.md").write_text("---\ndate: 2026-06-01\n---\nBody text")
        monkeypatch.setattr(write_mod, "ISSUES_DIR", issues_dir)
        result = find_previous_newsletter("2026-06-10")
        assert isinstance(result, str)
        assert "Body text" in result

    def test_excludes_exact_match_on_today(self, tmp_path, monkeypatch):
        """A file with exactly today's date should NOT be returned as 'previous'."""
        import write as write_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        (issues_dir / "2026-06-10.md").write_text("today")
        (issues_dir / "2026-06-09.md").write_text("yesterday")
        monkeypatch.setattr(write_mod, "ISSUES_DIR", issues_dir)
        result = find_previous_newsletter("2026-06-10")
        assert result == "yesterday"

    def test_returns_none_when_only_file_is_today(self, tmp_path, monkeypatch):
        import write as write_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        (issues_dir / "2026-06-10.md").write_text("today")
        monkeypatch.setattr(write_mod, "ISSUES_DIR", issues_dir)
        result = find_previous_newsletter("2026-06-10")
        assert result is None


# ---------------------------------------------------------------------------
# build_writer_input()
# ---------------------------------------------------------------------------

class TestBuildWriterInput:
    def _make_featured(self):
        return [{"id": "p1", "section": "papers", "title": "Paper"}]

    def _make_appendix(self):
        return {"papers": [], "news": [], "blogs": []}

    def _make_metadata(self):
        return {
            "items_considered": 50,
            "items_featured_total": 1,
            "items_featured_papers": 1,
            "items_featured_news": 0,
            "items_featured_blogs": 0,
            "items_appendix": 0,
        }

    def test_contains_required_keys(self):
        result = build_writer_input(
            "2026-06-01", self._make_featured(),
            self._make_appendix(), self._make_metadata(), None
        )
        assert "date" in result
        assert "featured" in result
        assert "appendix" in result
        assert "metadata" in result
        assert "reader_profile" in result

    def test_date_set_correctly(self):
        result = build_writer_input(
            "2026-06-15", self._make_featured(),
            self._make_appendix(), self._make_metadata(), None
        )
        assert result["date"] == "2026-06-15"

    def test_previous_newsletter_included_when_provided(self):
        prev = "Previous newsletter content"
        result = build_writer_input(
            "2026-06-01", self._make_featured(),
            self._make_appendix(), self._make_metadata(), prev
        )
        assert "previous_newsletter" in result
        assert result["previous_newsletter"] == prev

    def test_previous_newsletter_absent_when_none(self):
        result = build_writer_input(
            "2026-06-01", self._make_featured(),
            self._make_appendix(), self._make_metadata(), None
        )
        assert "previous_newsletter" not in result

    def test_previous_newsletter_truncated_at_max(self):
        long_prev = "x" * (PREV_NEWSLETTER_MAX + 500)
        result = build_writer_input(
            "2026-06-01", self._make_featured(),
            self._make_appendix(), self._make_metadata(), long_prev
        )
        assert len(result["previous_newsletter"]) == PREV_NEWSLETTER_MAX

    def test_reader_profile_is_non_empty_string(self):
        result = build_writer_input(
            "2026-06-01", self._make_featured(),
            self._make_appendix(), self._make_metadata(), None
        )
        assert isinstance(result["reader_profile"], str)
        assert len(result["reader_profile"]) > 0

    def test_featured_and_appendix_passed_through(self):
        featured = self._make_featured()
        appendix = self._make_appendix()
        result = build_writer_input(
            "2026-06-01", featured, appendix, self._make_metadata(), None
        )
        assert result["featured"] is featured
        assert result["appendix"] is appendix
