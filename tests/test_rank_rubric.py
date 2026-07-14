"""Tests for rank.py rubric-hash invalidation logic.

These tests were originally in test_prefilter_internals.py but moved here
as part of the refactor that relocated rubric-hash ownership from prefilter
to rank (issue #131).
"""
from __future__ import annotations

import hashlib

import pytest

from rank import (
    _rubric_hash,
    _maybe_invalidate_papers_scores,
)


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
        h = hashlib.sha256(b"fixed rubric").hexdigest()
        hash_file.write_text(h)

        monkeypatch.setattr(rank_mod, "PROMPT_PATH", rubric)
        monkeypatch.setattr(rank_mod, "RUBRIC_HASH_PATH", hash_file)

        count = _maybe_invalidate_papers_scores(conn)
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

        count = _maybe_invalidate_papers_scores(conn)
        assert count == 1

        # Score should now be NULL
        row = conn.execute("SELECT score FROM items WHERE id='p1'").fetchone()
        assert row["score"] is None

    def test_hash_file_updated_after_invalidation(self, db_with_scored_papers, monkeypatch):
        conn, tmp_path = db_with_scored_papers
        import rank as rank_mod

        rubric = tmp_path / "rank.md"
        rubric.write_text("rubric v2")
        hash_file = tmp_path / ".rubric_hash"
        hash_file.write_text("stale")

        monkeypatch.setattr(rank_mod, "PROMPT_PATH", rubric)
        monkeypatch.setattr(rank_mod, "RUBRIC_HASH_PATH", hash_file)

        _maybe_invalidate_papers_scores(conn)

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

        count = _maybe_invalidate_papers_scores(conn)
        assert count == 0

        # Score should be untouched
        row = conn.execute("SELECT score FROM items WHERE id='p1'").fetchone()
        assert row["score"] == 8

    def test_first_run_records_hash_without_invalidation(self, db_with_scored_papers, monkeypatch):
        """When hash changed but no scored papers exist, records hash only."""
        import rank as rank_mod
        import db as db_mod
        tmp_path = db_with_scored_papers[1]

        # Use a FRESH db with no scored papers
        fresh_db = tmp_path / "fresh.db"
        db_mod.init_db(fresh_db)
        conn_fresh = db_mod.connect(fresh_db)

        rubric = tmp_path / "rank.md"
        rubric.write_text("rubric first run")
        hash_file = tmp_path / ".rubric_hash"
        if hash_file.exists():
            hash_file.unlink()

        monkeypatch.setattr(rank_mod, "PROMPT_PATH", rubric)
        monkeypatch.setattr(rank_mod, "RUBRIC_HASH_PATH", hash_file)

        try:
            count = _maybe_invalidate_papers_scores(conn_fresh)
            assert count == 0

            # Hash file should now exist with current rubric hash
            assert hash_file.exists()
            expected = hashlib.sha256(b"rubric first run").hexdigest()
            assert hash_file.read_text().strip() == expected
        finally:
            conn_fresh.close()
