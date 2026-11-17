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
