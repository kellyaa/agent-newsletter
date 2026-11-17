"""prefilter.main and rank.main close state.db when a step raises (#744).

Both closed the connection by hand at the end of main, so an exception in
between leaked it. publish.main had the same bug, fixed in #743.
"""
from __future__ import annotations

import sqlite3

import pytest

import db as db_mod
import prefilter as pf_mod
import rank as rank_mod
from test_prefilter_main import FROZEN_NOW, _insert_item
from test_publish_atomic_seal import _TrackedConn
from test_rank_main import _insert_candidate


@pytest.fixture()
def db_path(tmp_path):
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    return p


def _track(monkeypatch, module, db_path):
    opened: list[_TrackedConn] = []

    def _connect():
        c = _TrackedConn(db_mod.connect(db_path))
        opened.append(c)
        return c

    monkeypatch.setattr(module, "connect", _connect)
    return opened


def _boom(*_a, **_kw):
    raise sqlite3.OperationalError("database or disk is full")


# --- prefilter ---------------------------------------------------------------

@pytest.fixture()
def prefilter_env(tmp_path, db_path, monkeypatch):
    from conftest import base_template

    rubric = tmp_path / "rank.md"
    rubric.write_text(base_template("rank"))
    monkeypatch.setattr(pf_mod, "candidates_path", lambda: tmp_path / "candidates.json")
    monkeypatch.setattr(pf_mod, "RUBRIC_PATH", rubric)
    monkeypatch.setattr(pf_mod, "init_db", lambda: db_mod.init_db(db_path))
    seed = db_mod.connect(db_path)
    db_mod.set_meta(seed, pf_mod.RUBRIC_HASH_KEY, pf_mod._rubric_hash())
    _insert_item(seed, "i1", status="new", source="rss:blog")
    seed.close()
    return _track(monkeypatch, pf_mod, db_path)


@pytest.mark.parametrize("step", ["age_out_papers", "promote_to_candidates", "load_ranking_pool"])
def test_prefilter_main_closes_connection_when_a_step_raises(prefilter_env, monkeypatch, step):
    monkeypatch.setattr(pf_mod, step, _boom)
    with pytest.raises(sqlite3.OperationalError):
        pf_mod.main(now=FROZEN_NOW)
    assert len(prefilter_env) == 1 and prefilter_env[0].closed


def test_prefilter_main_closes_connection_on_success(prefilter_env):
    assert pf_mod.main(now=FROZEN_NOW) == 0
    assert len(prefilter_env) == 1 and prefilter_env[0].closed


# --- rank --------------------------------------------------------------------

@pytest.fixture()
def rank_env(tmp_path, db_path, monkeypatch):
    """rank.main against a tmp DB holding one prescored paper (no LLM call)."""
    import candidates as candidates_mod
    from conftest import base_template

    prompt = tmp_path / "rank.md"
    prompt.write_text(base_template("rank"))
    monkeypatch.setattr(rank_mod, "PROMPT_PATH", prompt)
    monkeypatch.setattr(rank_mod, "ranked_path", lambda: tmp_path / "ranked.json")
    monkeypatch.setattr(rank_mod, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.setattr(candidates_mod, "connect", lambda: db_mod.connect(db_path))
    seed = db_mod.connect(db_path)
    _insert_candidate(seed, "pp1", section="papers", score=8, tags=["evals"], why="good")
    seed.close()
    return _track(monkeypatch, rank_mod, db_path)


@pytest.mark.parametrize("step", ["persist", "bump_times_competed"])
def test_rank_main_closes_connection_when_a_step_raises(rank_env, monkeypatch, step):
    monkeypatch.setattr(rank_mod, step, _boom)
    with pytest.raises(sqlite3.OperationalError):
        rank_mod.main()
    # The reset connection (already try/finally) and the persist connection.
    assert len(rank_env) == 2 and all(c.closed for c in rank_env)


def test_rank_main_closes_connection_on_success(rank_env):
    assert rank_mod.main() == 0
    assert len(rank_env) == 2 and all(c.closed for c in rank_env)


# --- fetch and backfill (#753) -----------------------------------------------

@pytest.fixture()
def fetch_env(tmp_path, db_path, monkeypatch):
    import fetch as fetch_mod

    sources = tmp_path / "sources.yaml"
    sources.write_text("rss:\n  - id: a\n    url: https://example.com/a.xml\n")
    monkeypatch.setenv("RUN_DATE", "2026-07-09")
    monkeypatch.setattr(fetch_mod, "sources_path", lambda: sources)
    monkeypatch.setattr(fetch_mod, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.setattr(fetch_mod, "fetch_rss", lambda src, **kw: [])
    return fetch_mod, _track(monkeypatch, fetch_mod, db_path)


@pytest.mark.parametrize(
    "step",
    ["_already_fetched_today", "_make_http_client", "prune_source_fetches", "set_meta"],
)
def test_fetch_main_closes_connection_when_a_step_raises(fetch_env, monkeypatch, step):
    fetch_mod, opened = fetch_env
    monkeypatch.setattr(fetch_mod, step, _boom)
    with pytest.raises(sqlite3.OperationalError):
        fetch_mod.main()
    assert len(opened) == 1 and opened[0].closed


def test_fetch_main_closes_connection_when_recording_a_source_raises(fetch_env, monkeypatch):
    fetch_mod, opened = fetch_env
    monkeypatch.setattr(fetch_mod, "upsert_items", _boom)
    with pytest.raises(sqlite3.OperationalError):
        fetch_mod.main()
    assert len(opened) == 1 and opened[0].closed


def test_fetch_main_closes_connection_on_the_already_fetched_skip(fetch_env, monkeypatch):
    fetch_mod, opened = fetch_env
    monkeypatch.setattr(fetch_mod, "_already_fetched_today", lambda conn: 3)
    assert fetch_mod.main() == 0
    assert len(opened) == 1 and opened[0].closed


def test_fetch_main_closes_connection_on_success(fetch_env):
    fetch_mod, opened = fetch_env
    fetch_mod.main()
    assert len(opened) == 1 and opened[0].closed


@pytest.fixture()
def backfill_env(tmp_path, db_path, monkeypatch):
    import backfill as bf

    opened: list[_TrackedConn] = []

    def _connect(path=None):
        c = _TrackedConn(sqlite3.connect(db_path))
        opened.append(c)
        return c

    monkeypatch.setattr(bf, "setup_sandbox", lambda date: tmp_path)
    monkeypatch.setattr(db_mod, "init_db", lambda *a, **kw: None)
    monkeypatch.setattr(db_mod, "connect", _connect)
    monkeypatch.setattr(bf, "age_out_for_synthetic_date", lambda conn, date: 0)
    monkeypatch.setattr(bf, "rank_with_optional_llm", lambda conn, candidates: {})
    monkeypatch.setattr(bf, "run_writer_for_date", lambda conn, date, force: None)
    monkeypatch.setattr("sys.argv", ["backfill.py", "--date", "2026-07-15"])
    return bf, opened


@pytest.mark.parametrize(
    "step", ["age_out_for_synthetic_date", "rank_with_optional_llm", "run_writer_for_date"],
)
def test_backfill_main_closes_connection_when_a_step_raises(backfill_env, monkeypatch, step):
    bf, opened = backfill_env
    monkeypatch.setattr(bf, step, _boom)
    with pytest.raises(sqlite3.OperationalError):
        bf.main()
    assert len(opened) == 1 and opened[0].closed


def test_backfill_main_closes_connection_when_loading_the_pool_raises(backfill_env, monkeypatch):
    import candidates

    bf, opened = backfill_env
    monkeypatch.setattr(candidates, "load_ranking_pool", _boom)
    with pytest.raises(sqlite3.OperationalError):
        bf.main()
    assert len(opened) == 1 and opened[0].closed


def test_backfill_main_closes_connection_when_nothing_was_written(backfill_env):
    bf, opened = backfill_env
    assert bf.main() == 1
    assert len(opened) == 1 and opened[0].closed
