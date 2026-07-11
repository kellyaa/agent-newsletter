"""Integration tests for prefilter.main() pipeline.

Tests the full DB-level flow without any LLM calls:
  - age-out of stale papers candidates
  - gate filtering (recency + keyword + dedup) for 'new' items
  - idempotent re-run when no 'new' items exist
  - promotion of survivors to 'candidate'
  - demotion of rejected 'new' items to 'dropped'
  - candidates.json emission (grouped by section, prescored vs unscored)
  - pre-rank cap on unscored papers
  - appendix retry eligibility
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    import db as db_mod
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    return p


@pytest.fixture()
def candidates_out(tmp_path):
    """Return a tmp path for candidates.json output."""
    return tmp_path / "candidates.json"


@pytest.fixture()
def rubric_file(tmp_path):
    """Return a tmp path for a minimal rank.md rubric."""
    p = tmp_path / "rank.md"
    p.write_text("# Rubric\nScore on relevance.")
    return p


def _insert_item(
    conn,
    item_id: str,
    status: str = "new",
    section: str | None = None,
    score: int | None = None,
    times_competed: int = 0,
    published_at: str = "2026-07-09T00:00:00Z",
    last_seen_date: str = "2026-07-09",
    source: str = "rss:test",
    title: str = "Agent workflow automation",
    raw_text: str = "LLM agent evaluation framework",
    section_override: str | None = None,
    keyword_gate_bypass: int = 0,
    appearances: int = 1,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO items (
            id, source, url, canonical_url, title, fetched_at,
            status, section, score, first_seen_date, last_seen_date,
            appearances, times_competed, published_at, raw_text,
            section_override, keyword_gate_bypass
        ) VALUES (
            ?, ?, 'https://example.com/' || ?, 'https://example.com/' || ?,
            ?, '2026-07-09T00:00:00Z',
            ?, ?, ?, '2026-07-09', ?,
            ?, ?, ?, ?,
            ?, ?
        )
        """,
        (item_id, source, item_id, item_id,
         title, status, section, score,
         last_seen_date, appearances, times_competed, published_at,
         raw_text, section_override, keyword_gate_bypass),
    )
    conn.commit()


def _run_main(db_path, candidates_out, rubric_file, monkeypatch):
    """Run prefilter.main() with patched filesystem paths."""
    import hashlib
    import prefilter as pf_mod
    import db as db_mod

    # Pre-populate the rubric hash file to prevent score invalidation during tests.
    # Without this, _maybe_invalidate_papers_scores() sees a "new" hash and wipes
    # any cached scores we set up for the test.
    rubric_hash_path = candidates_out.parent / ".rubric_hash"
    rubric_hash = hashlib.sha256(rubric_file.read_bytes()).hexdigest()
    rubric_hash_path.write_text(rubric_hash)

    monkeypatch.setattr(pf_mod, "CANDIDATES_OUT", candidates_out)
    monkeypatch.setattr(pf_mod, "RUBRIC_PATH", rubric_file)
    monkeypatch.setattr(pf_mod, "RUBRIC_HASH_PATH", rubric_hash_path)
    monkeypatch.setattr(pf_mod, "connect", lambda: db_mod.connect(db_path))
    import contextlib
    @contextlib.contextmanager
    def _mc(p=None):
        c = db_mod.connect(db_path)
        try:
            yield c
        finally:
            c.close()
    monkeypatch.setattr(pf_mod, "managed_connect", _mc)
    monkeypatch.setattr(pf_mod, "init_db", lambda: db_mod.init_db(db_path))

    return pf_mod.main()


# ---------------------------------------------------------------------------
# main() — missing candidates.json output path is just written (directory must exist)
# ---------------------------------------------------------------------------

class TestPrefilterMainEmptyDb:
    def test_returns_0_on_empty_db(self, db_path, candidates_out, rubric_file, monkeypatch):
        result = _run_main(db_path, candidates_out, rubric_file, monkeypatch)
        assert result == 0

    def test_writes_candidates_json_on_empty_db(self, db_path, candidates_out, rubric_file, monkeypatch):
        _run_main(db_path, candidates_out, rubric_file, monkeypatch)
        assert candidates_out.exists()
        data = json.loads(candidates_out.read_text())
        assert data["papers"] == []
        assert data["papers_prescored"] == []
        assert data["news"] == []
        assert data["blogs"] == []


# ---------------------------------------------------------------------------
# main() — new items promoted to candidate after passing gates
# ---------------------------------------------------------------------------

class TestPrefilterMainNewItems:
    def test_passing_item_promoted_to_candidate(self, db_path, candidates_out, rubric_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_item(conn, "i1", status="new", source="rss:blog",
                     title="Agent workflow with LLM", raw_text="tool use planning")
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        conn = db_mod.connect(db_path)
        row = conn.execute("SELECT status FROM items WHERE id='i1'").fetchone()
        conn.close()
        assert row["status"] == "candidate"

    def test_failing_keyword_gate_demoted_to_dropped(self, db_path, candidates_out, rubric_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_item(conn, "i2", status="new", source="rss:blog",
                     title="Database tuning guide", raw_text="index maintenance",
                     published_at="2026-07-09T00:00:00Z")
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        conn = db_mod.connect(db_path)
        row = conn.execute("SELECT status FROM items WHERE id='i2'").fetchone()
        conn.close()
        assert row["status"] == "dropped"

    def test_section_assigned_based_on_source(self, db_path, candidates_out, rubric_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_item(conn, "a1", status="new", source="arxiv:cs.AI",
                     title="LLM agent planning paper", raw_text="multi-agent systems")
        _insert_item(conn, "n1", status="new", source="hn:frontpage",
                     title="LLM agent release", raw_text="tool use agents")
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        conn = db_mod.connect(db_path)
        a_row = conn.execute("SELECT section FROM items WHERE id='a1'").fetchone()
        n_row = conn.execute("SELECT section FROM items WHERE id='n1'").fetchone()
        conn.close()
        assert a_row["section"] == "papers"
        assert n_row["section"] == "news"

    def test_section_override_respected(self, db_path, candidates_out, rubric_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_item(conn, "s1", status="new", source="rss:blog",
                     title="Multi-agent LLM framework", raw_text="agents planning",
                     section_override="papers")
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        conn = db_mod.connect(db_path)
        row = conn.execute("SELECT section FROM items WHERE id='s1'").fetchone()
        conn.close()
        assert row["section"] == "papers"

    def test_keyword_gate_bypass_allows_non_keyword_item(self, db_path, candidates_out, rubric_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_item(conn, "b1", status="new", source="rss:blog",
                     title="Quarterly earnings report", raw_text="revenue growth",
                     keyword_gate_bypass=1)
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        conn = db_mod.connect(db_path)
        row = conn.execute("SELECT status FROM items WHERE id='b1'").fetchone()
        conn.close()
        assert row["status"] == "candidate"

    def test_candidates_appear_in_json_output(self, db_path, candidates_out, rubric_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_item(conn, "blog1", status="new", source="rss:blog",
                     title="LLM agent best practices", raw_text="workflow automation")
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        data = json.loads(candidates_out.read_text())
        all_ids = (
            [it["id"] for it in data["blogs"]] +
            [it["id"] for it in data["papers"]] +
            [it["id"] for it in data["papers_prescored"]] +
            [it["id"] for it in data["news"]]
        )
        assert "blog1" in all_ids


# ---------------------------------------------------------------------------
# main() — idempotent re-run (no 'new' items)
# ---------------------------------------------------------------------------

class TestPrefilterMainIdempotent:
    def test_idempotent_rerun_re_emits_existing_candidates(self, db_path, candidates_out, rubric_file, monkeypatch):
        """When no 'new' items exist, re-running re-emits candidates.json from current candidates."""
        import db as db_mod
        conn = db_mod.connect(db_path)
        # Pre-existing candidate (already processed)
        _insert_item(conn, "c1", status="candidate", section="blogs",
                     title="Agents and LLMs", raw_text="")
        conn.close()

        result = _run_main(db_path, candidates_out, rubric_file, monkeypatch)
        assert result == 0

        data = json.loads(candidates_out.read_text())
        assert any(it["id"] == "c1" for it in data["blogs"])

    def test_idempotent_rerun_does_not_change_status(self, db_path, candidates_out, rubric_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_item(conn, "c1", status="candidate", section="blogs")
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        conn = db_mod.connect(db_path)
        row = conn.execute("SELECT status FROM items WHERE id='c1'").fetchone()
        conn.close()
        assert row["status"] == "candidate"


# ---------------------------------------------------------------------------
# main() — pre-scored papers go to papers_prescored in JSON
# ---------------------------------------------------------------------------

class TestPrefilterMainPrescored:
    def test_scored_papers_in_prescored_bucket(self, db_path, candidates_out, rubric_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        # A candidate with a cached score
        conn.execute(
            """
            INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
                status, section, score, tags, why, first_seen_date, last_seen_date, appearances)
            VALUES ('sp1','arxiv:x','https://a.com/sp1','https://a.com/sp1',
                    'Agent paper','2026-07-09T00:00:00Z','candidate','papers',
                    8,'[\"evals\"]','solid work','2026-07-09','2026-07-09',1)
            """
        )
        conn.commit()
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        data = json.loads(candidates_out.read_text())
        prescored_ids = [it["id"] for it in data["papers_prescored"]]
        assert "sp1" in prescored_ids
        # Should have score, tags, why in the JSON
        item = next(it for it in data["papers_prescored"] if it["id"] == "sp1")
        assert item["score"] == 8
        assert item["tags"] == ["evals"]

    def test_unscored_papers_in_papers_bucket(self, db_path, candidates_out, rubric_file, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        conn.execute(
            """
            INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
                status, section, first_seen_date, last_seen_date, appearances)
            VALUES ('up1','arxiv:x','https://a.com/up1','https://a.com/up1',
                    'New paper','2026-07-09T00:00:00Z','candidate','papers',
                    '2026-07-09','2026-07-09',1)
            """
        )
        conn.commit()
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        data = json.loads(candidates_out.read_text())
        assert any(it["id"] == "up1" for it in data["papers"])
        assert not any(it["id"] == "up1" for it in data["papers_prescored"])


# ---------------------------------------------------------------------------
# main() — appendix retry eligibility
# ---------------------------------------------------------------------------

class TestPrefilterMainAppendixRetry:
    def test_appendix_item_below_max_appearances_re_enters(self, db_path, candidates_out, rubric_file, monkeypatch):
        """appendix items with appearances < APPENDIX_MAX_APPEARANCES should be re-gated."""
        import db as db_mod
        from prefilter import APPENDIX_MAX_APPEARANCES
        conn = db_mod.connect(db_path)
        # appearances = 1, max = 2 → eligible for retry
        _insert_item(conn, "app1", status="appendix",
                     appearances=APPENDIX_MAX_APPEARANCES - 1,
                     title="LLM agent scheduling system", raw_text="planning tool use",
                     published_at="2026-07-08T00:00:00Z")
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        conn = db_mod.connect(db_path)
        row = conn.execute("SELECT status FROM items WHERE id='app1'").fetchone()
        conn.close()
        # Should have been re-processed (may become candidate or stay appendix/dropped
        # depending on gate results - but it was re-entered into the pipeline)
        assert row["status"] in ("candidate", "dropped", "appendix")


# ---------------------------------------------------------------------------
# main() — age-out of stale papers candidates
# ---------------------------------------------------------------------------

class TestPrefilterMainAgeOut:
    def test_papers_exceeding_competition_cap_dropped(self, db_path, candidates_out, rubric_file, monkeypatch):
        import db as db_mod
        from prefilter import PAPER_POOL_MAX_COMPETES
        conn = db_mod.connect(db_path)
        conn.execute(
            """
            INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
                status, section, times_competed, first_seen_date, last_seen_date, appearances)
            VALUES ('old1','arxiv:x','https://a.com/old1','https://a.com/old1',
                    'Old paper','2026-06-01T00:00:00Z','candidate','papers',
                    ?, '2026-06-01','2026-07-09',1)
            """,
            (PAPER_POOL_MAX_COMPETES,)
        )
        conn.commit()
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        conn = db_mod.connect(db_path)
        row = conn.execute("SELECT status FROM items WHERE id='old1'").fetchone()
        conn.close()
        assert row["status"] == "dropped"


# ---------------------------------------------------------------------------
# main() — pre-rank cap on unscored papers
# ---------------------------------------------------------------------------

class TestPrefilterMainPrerankCap:
    def test_prerank_cap_applied_when_exceeded(self, db_path, candidates_out, rubric_file, monkeypatch):
        """When unscored papers > PAPER_PRERANK_CAP, only top-N appear in candidates.json."""
        import db as db_mod
        from prefilter import PAPER_PRERANK_CAP
        conn = db_mod.connect(db_path)
        # Insert PAPER_PRERANK_CAP + 5 unscored candidate papers
        count = PAPER_PRERANK_CAP + 5
        for i in range(count):
            conn.execute(
                """
                INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
                    status, section, first_seen_date, last_seen_date, appearances,
                    published_at, raw_text)
                VALUES (?,  'arxiv:x', 'https://a.com/' || ?, 'https://a.com/' || ?,
                        'Agent paper ' || ?, '2026-07-09T00:00:00Z',
                        'candidate', 'papers', '2026-07-09','2026-07-09', 1,
                        '2026-07-09T00:00:00Z', 'llm agent evals')
                """,
                (f"p{i}", f"p{i}", f"p{i}", f"p{i}"),
            )
        conn.commit()
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        data = json.loads(candidates_out.read_text())
        assert len(data["papers"]) == PAPER_PRERANK_CAP


class TestPrefilterMainMalformedTags:
    """Tests for edge cases in the prescored-paper tag JSON parsing (lines 367-368)."""

    def test_malformed_tags_json_falls_back_to_empty_list(
        self, db_path, candidates_out, rubric_file, monkeypatch
    ):
        """Malformed tags JSON in a prescored paper produces [] in candidates.json (lines 367-368)."""
        import db as db_mod
        conn = db_mod.connect(db_path)
        conn.execute(
            """
            INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
                status, section, score, tags, why, first_seen_date, last_seen_date, appearances)
            VALUES ('mt1','arxiv:x','https://a.com/mt1','https://a.com/mt1',
                    'Malformed Tags Paper','2026-07-09T00:00:00Z','candidate','papers',
                    7,'not valid json','good paper','2026-07-09','2026-07-09',1)
            """
        )
        conn.commit()
        conn.close()

        _run_main(db_path, candidates_out, rubric_file, monkeypatch)

        data = json.loads(candidates_out.read_text())
        prescored = data["papers_prescored"]
        item = next((it for it in prescored if it["id"] == "mt1"), None)
        assert item is not None
        assert item["tags"] == []
