"""Regression tests for the CONTENT_ROOT / content-branch worktree feature.

PR #63 introduced an orphan 'content' branch and a CONTENT_ROOT env var that
redirects machine-authored artifacts (state.db, issue files) to a separate git
worktree.  These tests pin the path contracts so future changes to either the
main scripts or the worktree setup don't silently break artifact routing.

Design contract:
  - state.db         → CONTENT_ROOT (set in run.sh from the content worktree)
  - issue files      → CONTENT_ROOT/site/src/content/issues/
  - candidates.json  → REPO_ROOT  (intermediate pipeline artifact, NOT content)
  - ranked.json      → REPO_ROOT  (intermediate pipeline artifact, NOT content)
  - prompts/         → REPO_ROOT  (code artifact, NOT content)

Known regression (issue #64):
  backfill.py currently writes issue files to REPO instead of CONTENT_ROOT.
  The test below documents and asserts the EXPECTED behaviour post-fix, so it
  will fail until issue #64 is resolved.  It is clearly marked xfail.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# db.py — CONTENT_ROOT controls DB_PATH
# ---------------------------------------------------------------------------

class TestDbContentRoot:
    def test_db_path_set_via_env(self, monkeypatch, tmp_path):
        """DB_PATH resolves under CONTENT_ROOT when the env var is set."""
        monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
        import db
        importlib.reload(db)
        assert db.DB_PATH == tmp_path / "state.db"
        assert db.CONTENT_ROOT == tmp_path

    def test_db_path_defaults_to_repo_root(self, monkeypatch):
        """DB_PATH falls back to REPO_ROOT when CONTENT_ROOT is unset."""
        monkeypatch.delenv("CONTENT_ROOT", raising=False)
        import db
        importlib.reload(db)
        assert db.DB_PATH == db.REPO_ROOT / "state.db"
        assert db.CONTENT_ROOT == db.REPO_ROOT

    def test_content_root_distinct_from_repo_root(self, monkeypatch, tmp_path):
        """When set, CONTENT_ROOT differs from REPO_ROOT (the whole point)."""
        monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
        import db
        importlib.reload(db)
        assert db.CONTENT_ROOT != db.REPO_ROOT
        assert db.DB_PATH.parent == tmp_path


# ---------------------------------------------------------------------------
# write.py — ISSUES_DIR uses CONTENT_ROOT
# ---------------------------------------------------------------------------

class TestWriteContentRoot:
    def test_issues_dir_set_via_env(self, monkeypatch, tmp_path):
        """ISSUES_DIR resolves under CONTENT_ROOT when the env var is set."""
        monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
        import db
        importlib.reload(db)
        import write
        importlib.reload(write)
        assert write.ISSUES_DIR == tmp_path / "site" / "src" / "content" / "issues"

    def test_prompt_path_stays_in_repo_root(self, monkeypatch, tmp_path):
        """PROMPT_PATH is always in REPO_ROOT, never in CONTENT_ROOT."""
        monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
        import db
        importlib.reload(db)
        import write
        importlib.reload(write)
        # Prompts are code, not content — they live in the main repo
        assert write.PROMPT_PATH.is_relative_to(write.REPO_ROOT)
        assert not write.PROMPT_PATH.is_relative_to(tmp_path)

    def test_find_previous_newsletter_reads_from_content_root(self, monkeypatch, tmp_path):
        """find_previous_newsletter() reads issue files from CONTENT_ROOT issues dir."""
        monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
        import db
        importlib.reload(db)
        import write
        importlib.reload(write)

        # Create an issue file in the content-root issues dir
        issues_dir = tmp_path / "site" / "src" / "content" / "issues"
        issues_dir.mkdir(parents=True)
        (issues_dir / "2026-06-01.md").write_text("# Previous issue")

        result = write.find_previous_newsletter("2026-06-10")
        assert result is not None
        assert "Previous issue" in result


# ---------------------------------------------------------------------------
# publish.py — ISSUES_DIR uses CONTENT_ROOT
# ---------------------------------------------------------------------------

class TestPublishContentRoot:
    def test_issues_dir_set_via_env(self, monkeypatch, tmp_path):
        """publish.ISSUES_DIR resolves under CONTENT_ROOT when the env var is set."""
        monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
        import db
        importlib.reload(db)
        import publish
        importlib.reload(publish)
        assert publish.ISSUES_DIR == tmp_path / "site" / "src" / "content" / "issues"


# ---------------------------------------------------------------------------
# prefilter.py / rank.py — candidates.json stays in REPO_ROOT (by design)
# ---------------------------------------------------------------------------

class TestIntermediateArtifactsStayInRepoRoot:
    def test_prefilter_candidates_out_not_in_content_root(self, monkeypatch, tmp_path):
        """prefilter.CANDIDATES_OUT is in REPO_ROOT, not CONTENT_ROOT (intermediate artifact)."""
        monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
        import db
        importlib.reload(db)
        import prefilter
        importlib.reload(prefilter)
        # candidates.json is an intermediate file, intentionally in REPO_ROOT
        assert prefilter.CANDIDATES_OUT.is_relative_to(prefilter.REPO_ROOT)
        # and NOT in CONTENT_ROOT (which is tmp_path here)
        assert not str(prefilter.CANDIDATES_OUT).startswith(str(tmp_path))

    def test_rank_candidates_path_not_in_content_root(self, monkeypatch, tmp_path):
        """rank.CANDIDATES_PATH is in REPO_ROOT, not CONTENT_ROOT (intermediate artifact)."""
        monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
        import db
        importlib.reload(db)
        import rank
        importlib.reload(rank)
        assert rank.CANDIDATES_PATH.is_relative_to(rank.REPO_ROOT)
        assert not str(rank.CANDIDATES_PATH).startswith(str(tmp_path))

    def test_rank_rubric_prompt_not_in_content_root(self, monkeypatch, tmp_path):
        """rank.PROMPT_PATH (prompts/rank.md) is in REPO_ROOT — code, not content."""
        monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
        import db
        importlib.reload(db)
        import rank
        importlib.reload(rank)
        assert rank.PROMPT_PATH.is_relative_to(rank.REPO_ROOT)
        assert not str(rank.PROMPT_PATH).startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# backfill.py — regression test (XFAIL: issue #64 not yet fixed)
# ---------------------------------------------------------------------------

class TestBackfillContentRoot:
    @pytest.mark.xfail(
        reason="issue #64: backfill.run_writer_for_date() hardcodes REPO for ISSUES_DIR"
                " instead of using write.ISSUES_DIR which resolves under CONTENT_ROOT",
        strict=True,
    )
    def test_run_writer_for_date_uses_content_root(self, tmp_path, monkeypatch):
        """run_writer_for_date() SHOULD write the issue file to CONTENT_ROOT.

        Currently FAILS (xfail) because backfill.py uses REPO (hardcoded REPO_ROOT)
        instead of importing write.ISSUES_DIR.  When issue #64 is fixed, this test
        should be un-xfailed and will pass.
        """
        import db as db_mod
        import write as write_mod
        import backfill as bf

        # Set CONTENT_ROOT to a separate directory
        content_root = tmp_path / "content"
        content_root.mkdir()
        monkeypatch.setenv("CONTENT_ROOT", str(content_root))
        importlib.reload(db_mod)
        importlib.reload(write_mod)

        # Create DB in CONTENT_ROOT
        db_path = content_root / "state.db"
        db_mod.init_db(db_path)
        conn_raw = sqlite3.connect(db_path)
        conn_raw.execute("""
            INSERT INTO items (id, source, url, canonical_url, title, author,
                               published_at, fetched_at, raw_text, status, section,
                               score, tags, why, first_seen_date, last_seen_date,
                               appearances, keyword_gate_bypass, times_competed)
            VALUES ('feat1', 'arxiv:cs', 'http://arxiv.org/1', 'http://arxiv.org/1',
                    'Test Paper', 'Author', '2026-07-09', '2026-07-09',
                    'Abstract.', 'featured', 'papers', 9, '["agents"]',
                    'great', '2026-07-09', '2026-07-09', 0, 0, 0)
        """)
        conn_raw.commit()
        conn_raw.close()

        conn = db_mod.connect(db_path)

        # Create issues dir in CONTENT_ROOT (where write.py would write)
        content_issues = content_root / "site" / "src" / "content" / "issues"
        content_issues.mkdir(parents=True)

        # Mock invoke_writer to avoid LLM call
        fake_output = {"theme": "AI theme", "items": [{"id": "feat1", "summary": "Good paper."}]}
        monkeypatch.setattr(write_mod, "invoke_writer", lambda p: fake_output)

        # Point REPO to tmp_path root (different from content_root)
        monkeypatch.setattr(bf, "REPO", tmp_path)
        (tmp_path / "prompts").mkdir(exist_ok=True)
        (tmp_path / "prompts" / "write.md").write_text("rubric")

        result = bf.run_writer_for_date(conn, "2026-07-15", force=False)
        conn.close()

        # EXPECTED (post-fix): file written to CONTENT_ROOT issues dir
        expected_path = content_issues / "2026-07-15.md"
        # ACTUAL (current bug): file written to REPO issues dir
        actual_path = tmp_path / "site" / "src" / "content" / "issues" / "2026-07-15.md"

        # This assertion captures the expected (fixed) behavior.
        # It currently fails because result points to REPO, not CONTENT_ROOT.
        assert result == expected_path, (
            f"Expected issue at CONTENT_ROOT ({expected_path}), "
            f"but got {result} — backfill.py is writing to REPO_ROOT instead"
        )
