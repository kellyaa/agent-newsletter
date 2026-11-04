"""db.py helpers behind run_attempt's bookkeeping and check_edition's dry-run read.

run_attempt.py and check_edition.py used to issue these statements inline.
They now live in db.py so the SQL has one home; these tests pin each helper's
contract, and the guard at the bottom keeps new inline SQL from creeping back
into either module.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import db as db_mod

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    c = db_mod.connect(path)
    yield c
    c.close()


def test_next_attempt_number_per_date(conn):
    assert db_mod.next_attempt_number(conn, "2026-10-17") == 1
    db_mod.insert_run_attempt(conn, "2026-10-17", 1, "t1", "abc", 0, "h")
    db_mod.insert_run_attempt(conn, "2026-10-17", 2, "t2", "abc", 0, "h")
    assert db_mod.next_attempt_number(conn, "2026-10-17") == 3
    assert db_mod.next_attempt_number(conn, "2026-10-18") == 1


def test_insert_run_attempt_records_started_row(conn):
    db_mod.insert_run_attempt(conn, "2026-10-17", 1, "2026-10-17T11:00:00Z", "abc", 1, "host1")
    row = dict(conn.execute("SELECT * FROM run_attempts").fetchone())
    assert row["date"] == "2026-10-17"
    assert row["attempt"] == 1
    assert row["started_at"] == "2026-10-17T11:00:00Z"
    assert row["outcome"] == "started"
    assert row["code_commit"] == "abc"
    assert row["code_dirty"] == 1
    assert row["host"] == "host1"
    assert row["finished_at"] is None
    assert not conn.in_transaction


def test_finish_run_attempt_updates_only_target(conn):
    db_mod.insert_run_attempt(conn, "2026-10-17", 1, "t1", None, None, "h")
    db_mod.insert_run_attempt(conn, "2026-10-17", 2, "t2", None, None, "h")
    usage = {"calls": 3, "tokens_in": 10, "tokens_out": 20,
             "models": '["m"]', "tokens_by_model": '{"m": 30}'}
    n = db_mod.finish_run_attempt(
        conn, "2026-10-17", 2, finished_at="t3", outcome="failed",
        failed_stage="rank", exit_code=1, usage=usage,
    )
    assert n == 1
    assert not conn.in_transaction
    rows = {r["attempt"]: dict(r) for r in conn.execute("SELECT * FROM run_attempts")}
    assert rows[1]["outcome"] == "started"
    assert rows[1]["finished_at"] is None
    r = rows[2]
    assert (r["finished_at"], r["outcome"], r["failed_stage"], r["exit_code"]) == ("t3", "failed", "rank", 1)
    assert (r["llm_calls"], r["tokens_in"], r["tokens_out"]) == (3, 10, 20)
    assert (r["models"], r["tokens_by_model"]) == ('["m"]', '{"m": 30}')


def test_finish_run_attempt_missing_row_returns_zero(conn):
    n = db_mod.finish_run_attempt(
        conn, "2026-10-17", 9, finished_at="t", outcome="ok",
        failed_stage=None, exit_code=None, usage={},
    )
    assert n == 0


def test_latest_attempt_outcome(conn):
    assert db_mod.latest_attempt_outcome(conn, "2026-10-17") is None
    db_mod.insert_run_attempt(conn, "2026-10-17", 1, "t1", None, None, "h")
    db_mod.finish_run_attempt(conn, "2026-10-17", 1, finished_at="t", outcome="failed",
                              failed_stage="write", exit_code=2, usage={})
    db_mod.insert_run_attempt(conn, "2026-10-17", 2, "t2", None, None, "h")
    assert db_mod.latest_attempt_outcome(conn, "2026-10-17") == "started"
    assert db_mod.latest_attempt_outcome(conn, "2026-10-18") is None


def _add_item(conn, item_id):
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, "
        "first_seen_date, last_seen_date, status, appearances) "
        "VALUES (?, 's', ?, ?, 't', '2026-06-01T00:00:00Z', '2026-06-01', '2026-06-01', 'new', 1)",
        (item_id, f"https://x/{item_id}", f"https://x/{item_id}"),
    )


def test_load_gate_rows_columns_and_missing_ids(conn):
    _add_item(conn, "a")
    _add_item(conn, "b")
    _add_item(conn, "c")
    rows = db_mod.load_gate_rows(conn, ["a", "c", "missing"])
    assert sorted(r["id"] for r in rows) == ["a", "c"]
    assert set(rows[0]) == {
        "id", "source", "url", "title", "published_at", "fetched_at", "raw_text",
        "section_override", "keyword_gate_bypass", "recency_days_override",
    }


def test_load_gate_rows_empty(conn):
    assert db_mod.load_gate_rows(conn, []) == []


def test_load_gate_rows_spans_chunks(conn):
    ids = [f"i{n:04d}" for n in range(db_mod._IN_CHUNK * 2 + 5)]
    for i in ids:
        _add_item(conn, i)
    rows = db_mod.load_gate_rows(conn, ids)
    assert sorted(r["id"] for r in rows) == ids


@pytest.mark.parametrize("module", ["run_attempt.py", "check_edition.py"])
def test_module_has_no_inline_execute(module):
    """SQL in these modules goes through db.py helpers."""
    tree = ast.parse((SCRIPTS / module).read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    ]
    assert calls == [], f"move new {module} SQL into db.py helpers: lines {calls}"
