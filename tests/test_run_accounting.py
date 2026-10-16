"""Tests for run accounting (#13): llm._append_usage, publish.usage_totals,
publish.run_duration_s, and record_run's token and duration columns."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


@pytest.fixture()
def db(tmp_path):
    import db as db_mod
    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    yield conn
    conn.close()


def _usage(prompt, completion):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=None)


# ---------------------------------------------------------------------------
# llm._append_usage
# ---------------------------------------------------------------------------

class TestAppendUsage:
    def test_noop_without_env(self, monkeypatch, tmp_path):
        from llm import _append_usage
        monkeypatch.delenv("LLM_USAGE_LOG", raising=False)
        _append_usage("writer", "m", _usage(1, 2))
        assert list(tmp_path.iterdir()) == []

    def test_appends_one_line_per_call(self, monkeypatch, tmp_path):
        from llm import _append_usage
        path = tmp_path / "usage.jsonl"
        monkeypatch.setenv("LLM_USAGE_LOG", str(path))
        _append_usage("papers (attempt 1)", "rank-m", _usage(100, 20))
        _append_usage("writer", "write-m", _usage(300, 40))
        lines = [json.loads(l) for l in path.read_text().splitlines()]
        assert lines == [
            {"label": "papers (attempt 1)", "model": "rank-m", "prompt_tokens": 100, "completion_tokens": 20},
            {"label": "writer", "model": "write-m", "prompt_tokens": 300, "completion_tokens": 40},
        ]

    def test_missing_usage_records_none(self, monkeypatch, tmp_path):
        from llm import _append_usage
        path = tmp_path / "usage.jsonl"
        monkeypatch.setenv("LLM_USAGE_LOG", str(path))
        _append_usage("writer", "m", None)
        assert json.loads(path.read_text())["prompt_tokens"] is None

    def test_unwritable_path_does_not_raise(self, monkeypatch, tmp_path):
        from llm import _append_usage
        monkeypatch.setenv("LLM_USAGE_LOG", str(tmp_path / "no-such-dir" / "usage.jsonl"))
        _append_usage("writer", "m", _usage(1, 2))


# ---------------------------------------------------------------------------
# publish.usage_totals / run_duration_s
# ---------------------------------------------------------------------------

class TestUsageTotals:
    def test_none_without_file(self, tmp_path):
        from publish import usage_totals
        assert usage_totals(None) is None
        assert usage_totals(str(tmp_path / "absent.jsonl")) is None

    def test_sums_calls_and_folds_retries(self, tmp_path):
        from publish import usage_totals
        path = tmp_path / "usage.jsonl"
        rows = [
            {"label": "papers (attempt 1)", "model": "r", "prompt_tokens": 100, "completion_tokens": 10},
            {"label": "papers (attempt 2)", "model": "r", "prompt_tokens": 100, "completion_tokens": 12},
            {"label": "writer", "model": "w", "prompt_tokens": 500, "completion_tokens": None},
        ]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
        assert usage_totals(str(path)) == {
            "tokens_in": 700, "tokens_out": 22, "calls": 3, "models": "papers:r,writer:w",
            "tokens_by_model": "r:200/22,w:500/?",
        }

    def test_unreported_tokens_stay_none(self, tmp_path):
        from publish import usage_totals
        path = tmp_path / "usage.jsonl"
        path.write_text(json.dumps({"label": "writer", "model": "w", "prompt_tokens": None, "completion_tokens": None}) + "\n")
        totals = usage_totals(str(path))
        assert totals["tokens_in"] is None and totals["tokens_out"] is None and totals["calls"] == 1


class TestRunDuration:
    def test_seconds_since_start(self):
        from publish import run_duration_s
        now = datetime(2026, 10, 17, 11, 4, 5, tzinfo=timezone.utc)
        assert run_duration_s("2026-10-17T11:00:00Z", now) == 245

    def test_missing_or_bad_start_is_none(self):
        from publish import run_duration_s
        assert run_duration_s(None) is None
        assert run_duration_s("") is None
        assert run_duration_s("yesterday") is None


# ---------------------------------------------------------------------------
# record_run with usage and duration
# ---------------------------------------------------------------------------

class TestRecordRunAccounting:
    def test_writes_tokens_duration_and_models(self, db):
        from publish import record_run
        usage = {"tokens_in": 700, "tokens_out": 22, "calls": 4, "models": "papers:r,writer:w",
                 "tokens_by_model": "r:200/22,w:500/?"}
        record_run(db, "2026-10-17", {"papers": 2}, 3, 50, usage=usage, duration_s=245)
        row = db.execute("SELECT * FROM runs WHERE date = '2026-10-17'").fetchone()
        assert (row["tokens_in"], row["tokens_out"], row["duration_seconds"]) == (700, 22, 245)
        assert row["cost_usd"] is None
        assert row["notes"] == ("appendix=3 llm_calls=4 models=papers:r,writer:w"
                                " tokens_by_model=r:200/22,w:500/?")

    def test_without_usage_matches_old_behaviour(self, db):
        from publish import record_run
        record_run(db, "2026-10-17", {"papers": 2}, 3, 50)
        row = db.execute("SELECT * FROM runs WHERE date = '2026-10-17'").fetchone()
        assert row["tokens_in"] is None and row["duration_seconds"] is None
        assert row["notes"] == "appendix=3"

    def test_rerun_without_usage_keeps_earlier_values(self, db):
        from publish import record_run
        usage = {"tokens_in": 700, "tokens_out": 22, "calls": 4, "models": "writer:w",
                 "tokens_by_model": "w:700/22"}
        record_run(db, "2026-10-17", {"papers": 2}, 3, 50, usage=usage, duration_s=245)
        record_run(db, "2026-10-17", {"papers": 2}, 3, 50)
        row = db.execute("SELECT * FROM runs WHERE date = '2026-10-17'").fetchone()
        assert (row["tokens_in"], row["duration_seconds"]) == (700, 245)
