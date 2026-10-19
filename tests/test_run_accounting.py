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
        monkeypatch.delenv("RUN_ATTEMPT", raising=False)
        _append_usage("papers (attempt 1)", "rank-m", _usage(100, 20))
        _append_usage("writer", "write-m", _usage(300, 40))
        lines = [json.loads(l) for l in path.read_text().splitlines()]
        assert lines == [
            {"attempt": None, "label": "papers (attempt 1)", "model": "rank-m",
             "prompt_tokens": 100, "completion_tokens": 20},
            {"attempt": None, "label": "writer", "model": "write-m",
             "prompt_tokens": 300, "completion_tokens": 40},
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


# ---------------------------------------------------------------------------
# run_attempts table (scripts/run_attempt.py)
# ---------------------------------------------------------------------------

class TestUsageTotalsByAttempt:
    def test_counts_only_the_given_attempt(self, tmp_path):
        from publish import usage_totals
        path = tmp_path / "usage.jsonl"
        rows = [
            {"attempt": "1", "label": "papers", "model": "r", "prompt_tokens": 100, "completion_tokens": 10},
            {"attempt": "2", "label": "papers", "model": "r", "prompt_tokens": 40, "completion_tokens": 4},
            {"attempt": "2", "label": "writer", "model": "w", "prompt_tokens": 60, "completion_tokens": 6},
        ]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        day = usage_totals(str(path))
        second = usage_totals(str(path), attempt="2")
        assert (day["tokens_in"], day["calls"]) == (200, 3)
        assert (second["tokens_in"], second["tokens_out"], second["calls"]) == (100, 10, 2)
        assert usage_totals(str(path), attempt="3")["calls"] == 0


class TestAppendUsageAttempt:
    def test_tags_line_with_run_attempt(self, monkeypatch, tmp_path):
        from llm import _append_usage
        path = tmp_path / "usage.jsonl"
        monkeypatch.setenv("LLM_USAGE_LOG", str(path))
        monkeypatch.setenv("RUN_ATTEMPT", "2")
        _append_usage("writer", "w", _usage(1, 2))
        assert json.loads(path.read_text())["attempt"] == "2"


class TestRunAttempts:
    def test_start_numbers_attempts_per_date(self, db, monkeypatch):
        import run_attempt
        monkeypatch.setattr(run_attempt, "code_state", lambda: ("abc123", 0))
        assert run_attempt.start(db, "2026-10-17", "2026-10-17T11:00:00Z") == 1
        assert run_attempt.start(db, "2026-10-17", "2026-10-17T12:00:00Z") == 2
        assert run_attempt.start(db, "2026-10-18", "2026-10-18T11:00:00Z") == 1
        row = db.execute("SELECT * FROM run_attempts WHERE date = '2026-10-17' AND attempt = 1").fetchone()
        assert (row["outcome"], row["code_commit"], row["code_dirty"], row["finished_at"]) == \
            ("started", "abc123", 0, None)

    def test_finish_failed_records_stage_and_usage(self, db, monkeypatch):
        import run_attempt
        monkeypatch.setattr(run_attempt, "code_state", lambda: (None, None))
        n = run_attempt.start(db, "2026-10-17", "2026-10-17T11:00:00Z")
        usage = {"tokens_in": 100, "tokens_out": 10, "calls": 1, "models": "papers:r",
                 "tokens_by_model": "r:100/10"}
        run_attempt.finish(db, "2026-10-17", n, "failed", "rank", 1, usage)
        row = db.execute("SELECT * FROM run_attempts").fetchone()
        assert (row["outcome"], row["failed_stage"], row["exit_code"]) == ("failed", "rank", 1)
        assert (row["tokens_in"], row["models"], row["tokens_by_model"]) == (100, "papers:r", "r:100/10")
        assert row["finished_at"] is not None

    def test_finish_ok_drops_stage(self, db, monkeypatch):
        import run_attempt
        monkeypatch.setattr(run_attempt, "code_state", lambda: (None, None))
        n = run_attempt.start(db, "2026-10-17", "2026-10-17T11:00:00Z")
        run_attempt.finish(db, "2026-10-17", n, "ok", "git", None, None)
        row = db.execute("SELECT * FROM run_attempts").fetchone()
        assert (row["outcome"], row["failed_stage"], row["tokens_in"]) == ("ok", None, None)

    def test_finish_unknown_attempt_warns(self, db, caplog):
        import run_attempt
        run_attempt.finish(db, "2026-10-17", 9, "ok", None, None, None)
        assert "no run_attempts row" in caplog.text

    def test_code_state_reads_this_checkout(self):
        import run_attempt
        commit, dirty = run_attempt.code_state()
        assert commit is None or len(commit) == 40
        assert dirty in (None, 0, 1)

    def test_code_state_without_git(self, monkeypatch):
        import run_attempt
        def boom(*a, **k):
            raise FileNotFoundError("git")
        monkeypatch.setattr(run_attempt.subprocess, "run", boom)
        assert run_attempt.code_state() == (None, None)

    def test_main_start_then_finish(self, tmp_path, monkeypatch, capsys):
        import db as db_mod
        import run_attempt
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(run_attempt, "init_db", lambda: db_mod.init_db(db_path))
        monkeypatch.setattr(run_attempt, "connect", lambda: db_mod.connect(db_path))
        monkeypatch.setattr(run_attempt, "code_state", lambda: ("abc", 1))
        monkeypatch.setenv("RUN_DATE", "2026-10-17")
        monkeypatch.setenv("RUN_STARTED_AT", "2026-10-17T11:00:00Z")
        usage = tmp_path / "usage.jsonl"
        usage.write_text(json.dumps({"attempt": "1", "label": "writer", "model": "w",
                                     "prompt_tokens": 5, "completion_tokens": 1}) + "\n")
        monkeypatch.setenv("LLM_USAGE_LOG", str(usage))
        assert run_attempt.main(["start"]) == 0
        assert capsys.readouterr().out.strip() == "1"
        assert run_attempt.main(["finish", "1", "failed", "--stage", "write", "--exit-code", "3"]) == 0
        conn = db_mod.connect(db_path)
        row = conn.execute("SELECT * FROM run_attempts").fetchone()
        conn.close()
        assert (row["started_at"], row["outcome"], row["failed_stage"], row["exit_code"],
                row["tokens_in"], row["code_dirty"]) == \
            ("2026-10-17T11:00:00Z", "failed", "write", 3, 5, 1)

    def test_existing_db_gains_the_table(self, tmp_path):
        import sqlite3
        import db as db_mod
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE runs (date TEXT PRIMARY KEY)")
        conn.close()
        db_mod.init_db(db_path)
        conn = sqlite3.connect(db_path)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "run_attempts" in names
