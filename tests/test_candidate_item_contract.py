"""candidates.py emits exactly the shape models.CandidateItem declares (#536).

models.CandidateItem documents what the ranker receives for each candidate, but
nothing bound the producer to it: _load() built a plain dict field by field, so
a key added or renamed on either side left the "contract" silently wrong. The
repo runs no type checker, so these tests pin the binding at runtime:

* the producer's return annotations name CandidateItem;
* unscored items carry exactly CandidateItem's keys minus the prescored-only
  ones, and papers_prescored items carry all of them.
"""
from __future__ import annotations

import typing

import pytest

import candidates
import db as db_mod
from candidates import PAPERS_PRESCORED, load_candidates_from_db
from models import CandidateItem, Section

# Keys only papers_prescored items carry (cached score from a prior run).
PRESCORED_ONLY = {"score", "tags", "why"}
ALL_KEYS = set(CandidateItem.__annotations__)


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    c = db_mod.connect(p)
    yield c
    c.close()


def _insert(conn, item_id, section, **extra):
    row = {
        "id": item_id, "source": "src", "url": f"https://a.com/{item_id}",
        "canonical_url": f"https://a.com/{item_id}", "title": item_id,
        "fetched_at": "2026-07-09T00:00:00Z", "status": "candidate",
        "section": section, "first_seen_date": "2026-07-09",
        "last_seen_date": "2026-07-09", "published_at": "2026-07-09T00:00:00Z",
        "raw_text": "x", **extra,
    }
    cols = ", ".join(row)
    conn.execute(f"INSERT INTO items ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    conn.commit()


def test_prescored_only_keys_are_declared_on_candidate_item():
    assert PRESCORED_ONLY < ALL_KEYS


@pytest.mark.parametrize("fn", [candidates.load_candidates_from_db, candidates._load])
def test_producer_is_annotated_with_candidate_item(fn):
    assert typing.get_type_hints(fn)["return"] == dict[str, list[CandidateItem]]


def test_emitted_keys_match_candidate_item(conn):
    for s in Section:
        _insert(conn, f"unscored-{s.value}", s.value)
    _insert(conn, "prescored", Section.PAPERS.value, score=7, tags='["agents"]', why="w")

    grouped = load_candidates_from_db(conn)

    for s in Section:
        (item,) = grouped[s.value]
        assert set(item) == ALL_KEYS - PRESCORED_ONLY, s.value
    (pre,) = grouped[PAPERS_PRESCORED]
    assert set(pre) == ALL_KEYS
    assert (pre["score"], pre["tags"], pre["why"]) == (7, ["agents"], "w")
