"""run_attempt.py: behaviours the run.sh orchestrator relies on.

Pins what the existing accounting tests leave open: an ``ok`` finish discards a
stray ``--stage``, finish touches only its own (date, attempt) row, ``main``
scopes the usage log to the finishing attempt and falls back to the clock when
``RUN_STARTED_AT`` is unset, and ``latest`` follows the attempt number.
"""
from __future__ import annotations

import json
import re

import pytest

import db as db_mod
import run_attempt

DAY = "2026-10-17"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    monkeypatch.setattr(run_attempt, "code_state", lambda: ("abc", 0))
    c = db_mod.connect(path)
    yield c
    c.close()


@pytest.fixture()
def cli(tmp_path, monkeypatch):
    """run_attempt.main() wired to a temp DB; returns the DB path."""
    path = tmp_path / "state.db"
    monkeypatch.setattr(run_attempt, "init_db", lambda: db_mod.init_db(path))
    monkeypatch.setattr(run_attempt, "connect", lambda: db_mod.connect(path))
    monkeypatch.setattr(run_attempt, "db_path", lambda: path)
    monkeypatch.setattr(run_attempt, "code_state", lambda: ("abc", 0))
    monkeypatch.setenv("RUN_DATE", DAY)
    monkeypatch.delenv("RUN_STARTED_AT", raising=False)
    monkeypatch.delenv("LLM_USAGE_LOG", raising=False)
    return path


def _rows(path):
    c = db_mod.connect(path)
    rows = [dict(r) for r in c.execute("SELECT * FROM run_attempts ORDER BY date, attempt")]
    c.close()
    return rows


def test_ok_finish_drops_a_stage_that_was_passed(conn):
    n = run_attempt.start(conn, DAY, "t")
    run_attempt.finish(conn, DAY, n, "ok", "rank", 0, None)
    row = conn.execute("SELECT * FROM run_attempts").fetchone()
    assert (row["outcome"], row["failed_stage"], row["exit_code"]) == ("ok", None, 0)


def test_failed_finish_without_stage_records_null_stage(conn):
    n = run_attempt.start(conn, DAY, "t")
    run_attempt.finish(conn, DAY, n, "failed", None, 1, None)
    row = conn.execute("SELECT * FROM run_attempts").fetchone()
    assert (row["outcome"], row["failed_stage"], row["exit_code"]) == ("failed", None, 1)


def test_finish_updates_only_its_own_attempt_and_date(conn):
    run_attempt.start(conn, DAY, "t1")
    run_attempt.start(conn, DAY, "t2")
    run_attempt.start(conn, "2026-10-18", "t3")
    run_attempt.finish(conn, DAY, 2, "failed", "write", 3, None)
    outcomes = {(r["date"], r["attempt"]): r["outcome"]
                for r in conn.execute("SELECT * FROM run_attempts")}
    assert outcomes == {(DAY, 1): "started", (DAY, 2): "failed", ("2026-10-18", 1): "started"}


def test_finish_without_usage_leaves_token_columns_null(conn):
    n = run_attempt.start(conn, DAY, "t")
    run_attempt.finish(conn, DAY, n, "ok", None, 0, None)
    row = conn.execute("SELECT * FROM run_attempts").fetchone()
    assert (row["llm_calls"], row["tokens_in"], row["tokens_out"], row["models"]) == (None,) * 4


def test_start_records_commit_dirty_flag_and_host(conn, monkeypatch):
    monkeypatch.setattr(run_attempt, "code_state", lambda: ("deadbeef", 1))
    monkeypatch.setattr(run_attempt.socket, "gethostname", lambda: "pod-7")
    run_attempt.start(conn, DAY, "2026-10-17T11:00:00Z")
    row = conn.execute("SELECT * FROM run_attempts").fetchone()
    assert (row["code_commit"], row["code_dirty"], row["host"], row["started_at"]) == \
        ("deadbeef", 1, "pod-7", "2026-10-17T11:00:00Z")


def test_latest_follows_the_highest_attempt_number(conn):
    run_attempt.start(conn, DAY, "t1")
    run_attempt.start(conn, DAY, "t2")
    run_attempt.finish(conn, DAY, 2, "failed", "rank", 1, None)
    # attempt 1 finishing late (out of order) must not change the answer
    run_attempt.finish(conn, DAY, 1, "ok", None, 0, None)
    assert run_attempt.latest_outcome(conn, DAY) == "failed"


def test_cli_start_without_run_started_at_stamps_the_clock(cli, monkeypatch, capsys):
    monkeypatch.setattr(run_attempt, "_now", lambda: "2026-10-17T09:30:00Z")
    assert run_attempt.main(["start"]) == 0
    assert capsys.readouterr().out.strip() == "1"
    assert _rows(cli)[0]["started_at"] == "2026-10-17T09:30:00Z"


def test_cli_start_with_empty_run_started_at_falls_back_to_the_clock(cli, monkeypatch):
    monkeypatch.setenv("RUN_STARTED_AT", "")
    monkeypatch.setattr(run_attempt, "_now", lambda: "2026-10-17T09:30:00Z")
    run_attempt.main(["start"])
    assert _rows(cli)[0]["started_at"] == "2026-10-17T09:30:00Z"


def test_cli_start_numbers_successive_attempts(cli, capsys):
    run_attempt.main(["start"])
    run_attempt.main(["start"])
    assert capsys.readouterr().out.split() == ["1", "2"]


def test_now_is_utc_iso_seconds():
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", run_attempt._now())


def test_cli_finish_counts_only_its_own_attempts_usage(cli, tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in [
        {"attempt": "1", "label": "papers", "model": "r", "prompt_tokens": 100, "completion_tokens": 10},
        {"attempt": "2", "label": "writer", "model": "w", "prompt_tokens": 7, "completion_tokens": 3},
    ]))
    monkeypatch.setenv("LLM_USAGE_LOG", str(log))
    run_attempt.main(["start"])
    run_attempt.main(["start"])
    assert run_attempt.main(["finish", "2", "ok"]) == 0
    first, second = _rows(cli)
    assert (second["llm_calls"], second["tokens_in"], second["tokens_out"]) == (1, 7, 3)
    assert (first["outcome"], first["tokens_in"]) == ("started", None)


def test_cli_finish_without_usage_log_records_no_tokens(cli):
    run_attempt.main(["start"])
    assert run_attempt.main(["finish", "1", "ok"]) == 0
    row = _rows(cli)[0]
    assert (row["outcome"], row["tokens_in"], row["llm_calls"]) == ("ok", None, None)


def test_cli_finish_of_unknown_attempt_warns_and_exits_0(cli, caplog):
    run_attempt.main(["start"])
    assert run_attempt.main(["finish", "5", "failed", "--stage", "rank"]) == 0
    assert "no run_attempts row" in caplog.text
    assert _rows(cli)[0]["outcome"] == "started"


def test_cli_rejects_unknown_outcome(cli):
    with pytest.raises(SystemExit) as exc:
        run_attempt.main(["finish", "1", "maybe"])
    assert exc.value.code == 2


def test_cli_requires_a_subcommand(cli):
    with pytest.raises(SystemExit) as exc:
        run_attempt.main([])
    assert exc.value.code == 2
