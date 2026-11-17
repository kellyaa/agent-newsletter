"""prefilter, rank, fetch and backfill main close state.db when a step raises (#744).

Each closed the connection by hand at the end of main, so an exception in
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


# --- fetch -------------------------------------------------------------------

@pytest.fixture()
def fetch_env(tmp_path, db_path, monkeypatch):
    """fetch.main against a tmp DB and one fake RSS source (no network)."""
    import fetch as fetch_mod
    from fetch import Item

    sources = tmp_path / "sources.yaml"
    sources.write_text("rss:\n  - id: a\n    url: https://example.com/a.xml\n")
    monkeypatch.setenv("RUN_DATE", "2026-07-09")
    monkeypatch.setattr(fetch_mod, "sources_path", lambda: sources)
    monkeypatch.setattr(fetch_mod, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.setattr(
        fetch_mod, "fetch_rss",
        lambda src, **kw: [Item("rss:a", "https://a.com/1", "T", None, None, None)],
    )
    return fetch_mod, _track(monkeypatch, fetch_mod, db_path)


@pytest.mark.parametrize(
    "step", ["_already_fetched_today", "upsert_items", "prune_source_fetches", "set_meta"],
)
def test_fetch_main_closes_connection_when_a_step_raises(fetch_env, monkeypatch, step):
    fetch_mod, opened = fetch_env
    monkeypatch.setattr(fetch_mod, step, _boom)
    with pytest.raises(sqlite3.OperationalError):
        fetch_mod.main()
    assert len(opened) == 1 and opened[0].closed


def test_fetch_main_closes_connection_on_success_and_on_skip(fetch_env):
    fetch_mod, opened = fetch_env
    assert fetch_mod.main() == 0
    # Second run the same day takes the "already fetched" early return.
    assert fetch_mod.main() == 0
    assert len(opened) == 2 and all(c.closed for c in opened)


# --- backfill ----------------------------------------------------------------

@pytest.fixture()
def backfill_env(tmp_path, monkeypatch):
    """backfill.main against an empty sandbox DB, tracking the sandbox connection."""
    import sys

    import backfill

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setattr(backfill, "setup_sandbox", lambda date: sandbox)
    monkeypatch.setattr(sys, "argv", ["backfill.py", "--date", "2026-07-15"])
    opened: list[_TrackedConn] = []
    real_connect = db_mod.connect

    def _connect(path=None):
        c = _TrackedConn(real_connect(path))
        opened.append(c)
        return c

    monkeypatch.setattr(db_mod, "connect", _connect)
    return backfill, opened


@pytest.mark.parametrize(
    "step", ["age_out_for_synthetic_date", "rank_with_optional_llm", "run_writer_for_date"],
)
def test_backfill_main_closes_sandbox_connection_when_a_step_raises(backfill_env, monkeypatch, step):
    backfill, opened = backfill_env
    monkeypatch.setattr(backfill, step, _boom)
    with pytest.raises(sqlite3.OperationalError):
        backfill.main()
    # init_db opens (and closes) its own connection first; main's is the last.
    assert opened and all(c.closed for c in opened)


def test_backfill_main_closes_sandbox_connection_on_success(backfill_env, monkeypatch):
    backfill, opened = backfill_env
    monkeypatch.setattr(backfill, "rank_with_optional_llm", lambda conn, c: {})
    monkeypatch.setattr(backfill, "run_writer_for_date", lambda conn, d, f: None)
    assert backfill.main() == 1
    assert opened and all(c.closed for c in opened)
