"""Tests for prefilter.py internals: _prerank_score, dedup helpers, rubric invalidation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from prefilter import (
    _prerank_score,
    _normalize_title,
    _title_tokens,
    _jaccard,
    _passes_recency,
)
from rank import (
    _rubric_hash,
    maybe_invalidate_papers_scores,
)


# ---------------------------------------------------------------------------
# _normalize_title
# ---------------------------------------------------------------------------

class TestNormalizeTitle:
    def test_lowercases(self):
        assert _normalize_title("Hello World") == "hello world"

    def test_strips_punctuation(self):
        assert _normalize_title("Hello, World!") == "hello world"

    def test_strips_leading_trailing_whitespace(self):
        assert _normalize_title("  hello  ") == "hello"

    def test_preserves_alphanumeric(self):
        assert _normalize_title("Agent2025") == "agent2025"

    def test_empty_string(self):
        assert _normalize_title("") == ""


# ---------------------------------------------------------------------------
# _title_tokens
# ---------------------------------------------------------------------------

class TestTitleTokens:
    def test_splits_into_words(self):
        assert _title_tokens("hello world") == {"hello", "world"}

    def test_normalizes_before_split(self):
        tokens = _title_tokens("OpenAI Releases Agent SDK!")
        assert "openai" in tokens
        assert "releases" in tokens

    def test_empty_title_returns_empty_set(self):
        assert _title_tokens("") == set()

    def test_single_word(self):
        assert _title_tokens("agents") == {"agents"}


# ---------------------------------------------------------------------------
# _jaccard
# ---------------------------------------------------------------------------

class TestJaccard:
    def test_identical_sets_return_one(self):
        a = {"a", "b", "c"}
        assert _jaccard(a, a) == 1.0

    def test_disjoint_sets_return_zero(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        # intersection=1, union=3
        assert abs(_jaccard({"a", "b"}, {"b", "c"}) - 1/3) < 1e-9

    def test_empty_set_returns_zero(self):
        assert _jaccard(set(), {"a", "b"}) == 0.0
        assert _jaccard({"a"}, set()) == 0.0


# ---------------------------------------------------------------------------
# _prerank_score
# ---------------------------------------------------------------------------

class TestPrerankScore:
    def _item(self, pub_offset_days=0, title="agent llm", raw_text=""):
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        pub = (now - timedelta(days=pub_offset_days)).isoformat()
        return {"published_at": pub, "fetched_at": pub, "title": title, "raw_text": raw_text}

    def test_recent_item_scores_higher_than_old(self):
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        recent = self._item(pub_offset_days=0)
        old = self._item(pub_offset_days=6)
        assert _prerank_score(recent, now) > _prerank_score(old, now)

    def test_keyword_rich_scores_higher_than_poor(self):
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        # Same age; more keyword hits → higher score
        rich = self._item(pub_offset_days=0, title="agent llm workflow planning", raw_text="evals rag")
        poor = self._item(pub_offset_days=0, title="database tuning", raw_text="index rebuild")
        assert _prerank_score(rich, now) > _prerank_score(poor, now)

    def test_score_is_positive(self):
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        item = self._item()
        assert _prerank_score(item, now) > 0.0

    def test_score_bounded_above(self):
        """Score = recency * (0.5 + kw); recency ≤ 1, kw ≤ 1 → max = 1.5."""
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        # Just-published item with max keyword hits
        item = {"published_at": now.isoformat(), "fetched_at": now.isoformat(),
                 "title": "agent llm workflow planning evals",
                 "raw_text": "rag multi-agent code generation"}
        score = _prerank_score(item, now)
        assert score <= 1.5

    def test_unparseable_published_at_defaults_to_zero_age(self):
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        item = {"published_at": "not-a-date", "fetched_at": "not-a-date",
                "title": "agent", "raw_text": ""}
        # Should not raise; defaults age_days=0.0
        score = _prerank_score(item, now)
        assert score > 0.0


# ---------------------------------------------------------------------------
# _rubric_hash
# ---------------------------------------------------------------------------

class TestRubricHash:
    def test_returns_empty_string_when_file_absent(self, tmp_path, monkeypatch):
        import rank as rank_mod
        monkeypatch.setattr(rank_mod, "PROMPT_PATH", tmp_path / "nonexistent.md")
        assert _rubric_hash() == ""

    def test_returns_sha256_hex_string(self, tmp_path, monkeypatch):
        import rank as rank_mod
        rubric = tmp_path / "rank.md"
        rubric.write_text("score by novelty")
        monkeypatch.setattr(rank_mod, "PROMPT_PATH", rubric)
        h = _rubric_hash()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_changes_when_content_changes(self, tmp_path, monkeypatch):
        import rank as rank_mod
        rubric = tmp_path / "rank.md"
        rubric.write_text("v1 content")
        monkeypatch.setattr(rank_mod, "PROMPT_PATH", rubric)
        h1 = _rubric_hash()
        rubric.write_text("v2 content")
        h2 = _rubric_hash()
        assert h1 != h2


# ---------------------------------------------------------------------------
# _maybe_invalidate_papers_scores
# ---------------------------------------------------------------------------

class TestMaybeInvalidatePapersScores:
    @pytest.fixture()
    def db_with_scored_papers(self, tmp_path):
        import db as db_mod
        db_path = tmp_path / "state.db"
        db_mod.init_db(db_path)
        conn = db_mod.connect(db_path)
        # Insert a scored papers candidate
        conn.execute(
            """
            INSERT INTO items (
                id, source, url, canonical_url, title, fetched_at, status,
                section, score, first_seen_date, last_seen_date, appearances
            ) VALUES ('p1','arxiv:x','https://a.com/1','https://a.com/1',
                      'Paper','2026-06-01T00:00:00Z','candidate','papers',
                      8,'2026-06-01','2026-06-01',1)
            """
        )
        conn.commit()
        yield conn, tmp_path
        conn.close()

    def test_no_invalidation_when_hash_unchanged(self, db_with_scored_papers, monkeypatch):
        conn, tmp_path = db_with_scored_papers
        import rank as rank_mod

        rubric = tmp_path / "rank.md"
        rubric.write_text("fixed rubric")
        hash_file = tmp_path / ".rubric_hash"
        import hashlib
        h = hashlib.sha256(b"fixed rubric").hexdigest()
        hash_file.write_text(h)

        monkeypatch.setattr(rank_mod, "PROMPT_PATH", rubric)
        monkeypatch.setattr(rank_mod, "RUBRIC_HASH_PATH", hash_file)

        count = maybe_invalidate_papers_scores(conn)
        assert count == 0

        # Score should NOT have been wiped
        row = conn.execute("SELECT score FROM items WHERE id='p1'").fetchone()
        assert row["score"] == 8

    def test_invalidates_when_rubric_changed(self, db_with_scored_papers, monkeypatch):
        conn, tmp_path = db_with_scored_papers
        import rank as rank_mod

        rubric = tmp_path / "rank.md"
        rubric.write_text("new rubric content")
        hash_file = tmp_path / ".rubric_hash"
        hash_file.write_text("old_hash_value")  # stale hash

        monkeypatch.setattr(rank_mod, "PROMPT_PATH", rubric)
        monkeypatch.setattr(rank_mod, "RUBRIC_HASH_PATH", hash_file)

        count = maybe_invalidate_papers_scores(conn)
        assert count == 1

        # Score should now be NULL
        row = conn.execute("SELECT score FROM items WHERE id='p1'").fetchone()
        assert row["score"] is None

    def test_hash_file_updated_after_invalidation(self, db_with_scored_papers, monkeypatch):
        conn, tmp_path = db_with_scored_papers
        import rank as rank_mod
        import hashlib

        rubric = tmp_path / "rank.md"
        rubric.write_text("rubric v2")
        hash_file = tmp_path / ".rubric_hash"
        hash_file.write_text("stale")

        monkeypatch.setattr(rank_mod, "PROMPT_PATH", rubric)
        monkeypatch.setattr(rank_mod, "RUBRIC_HASH_PATH", hash_file)

        maybe_invalidate_papers_scores(conn)

        expected = hashlib.sha256(b"rubric v2").hexdigest()
        assert hash_file.read_text().strip() == expected

    def test_returns_zero_when_rubric_file_absent(self, db_with_scored_papers, monkeypatch):
        """Returns 0 without DB changes when rank.md is absent."""
        conn, tmp_path = db_with_scored_papers
        import rank as rank_mod

        # Point to a non-existent rubric file
        rubric = tmp_path / "missing_rank.md"
        hash_file = tmp_path / ".rubric_hash"
        monkeypatch.setattr(rank_mod, "PROMPT_PATH", rubric)
        monkeypatch.setattr(rank_mod, "RUBRIC_HASH_PATH", hash_file)

        count = maybe_invalidate_papers_scores(conn)
        assert count == 0

        # Score should be untouched
        row = conn.execute("SELECT score FROM items WHERE id='p1'").fetchone()
        assert row["score"] == 8

    def test_first_run_records_hash_without_invalidation(self, db_with_scored_papers, monkeypatch):
        """When hash changed but no scored papers exist, logs 'rubric hash recorded'."""
        import rank as rank_mod
        import hashlib
        import db as db_mod
        tmp_path = db_with_scored_papers[1]  # only use tmp_path, not the scored-papers conn

        # Use a FRESH db with no scored papers (so invalidated == 0, triggering log line)
        fresh_db = tmp_path / "fresh.db"
        db_mod.init_db(fresh_db)
        conn_fresh = db_mod.connect(fresh_db)

        rubric = tmp_path / "rank.md"
        rubric.write_text("rubric first run")
        hash_file = tmp_path / ".rubric_hash"
        # Ensure no existing hash file
        if hash_file.exists():
            hash_file.unlink()

        monkeypatch.setattr(rank_mod, "PROMPT_PATH", rubric)
        monkeypatch.setattr(rank_mod, "RUBRIC_HASH_PATH", hash_file)

        try:
            count = maybe_invalidate_papers_scores(conn_fresh)
            # No papers to invalidate → returns 0
            assert count == 0

            # Hash file should now exist with current rubric hash
            assert hash_file.exists()
            expected = hashlib.sha256(b"rubric first run").hexdigest()
            assert hash_file.read_text().strip() == expected
        finally:
            conn_fresh.close()
