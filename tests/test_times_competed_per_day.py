"""``times_competed`` counts competition *days*, not rank attempts.

``PAPER_POOL_MAX_COMPETES`` (prefilter) ages a paper out after it has lost that
many competitions, which SPEC.md sizes as about a week of daily runs. rank runs
once per *attempt*, though: run_edition.sh re-runs it after a later stage
fails, and ``run.sh --failed`` and the watchdog retry a failed day (#587 saw 6
attempts on one day). Counting each attempt let a single bad day age strong,
cap-losing papers out of the pool. ``db.bump_times_competed`` therefore counts at
most once per paper per run date (``items.last_competed_date``), and it is the
only statement in ``scripts/`` that writes the counter.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import db as db_mod
from models import Section, Status

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DAY = "2026-07-09"
NEXT_DAY = "2026-07-10"


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    c = db_mod.connect(path)
    yield c
    c.close()


def _add(conn, item_id, *, status=Status.CANDIDATE, score=None, times_competed=0):
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, "
        "first_seen_date, last_seen_date, status, section, score, tags, why, times_competed) "
        "VALUES (?, 'arxiv:x', ?, ?, 'Title', '2026-07-09T00:00:00Z', ?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, f"https://a.com/{item_id}", f"https://a.com/{item_id}", DAY, DAY,
         status, Section.PAPERS, score, "[]" if score is not None else None,
         "w" if score is not None else None, times_competed),
    )
    conn.commit()


def _competed(conn, item_id):
    r = conn.execute(
        "SELECT times_competed, last_competed_date FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    return r[0], r[1]


# ─── the helper ────────────────────────────────────────────────────────────

def test_same_day_rebump_is_a_noop(conn):
    _add(conn, "p")
    assert db_mod.bump_times_competed(conn, ["p"], DAY) == 1
    assert db_mod.bump_times_competed(conn, ["p"], DAY) == 0
    assert _competed(conn, "p") == (1, DAY)


def test_next_day_counts_again(conn):
    _add(conn, "p")
    db_mod.bump_times_competed(conn, ["p"], DAY)
    assert db_mod.bump_times_competed(conn, ["p"], NEXT_DAY) == 1
    assert _competed(conn, "p") == (2, NEXT_DAY)


def test_row_from_before_the_column_counts_on_first_bump(conn):
    """Pre-migration rows have last_competed_date NULL and keep their count."""
    _add(conn, "p", times_competed=3)
    assert db_mod.bump_times_competed(conn, ["p"], DAY) == 1
    assert _competed(conn, "p") == (4, DAY)


def test_sealed_rows_are_still_not_counted(conn):
    _add(conn, "won", status=Status.FEATURED)
    assert db_mod.bump_times_competed(conn, ["won"], DAY) == 0
    assert _competed(conn, "won") == (0, None)


# ─── rank.main end to end ──────────────────────────────────────────────────

def _run_rank(db_path, tmp_path, monkeypatch, run_date, score):
    import candidates as candidates_mod
    import rank as rank_mod
    from conftest import base_template

    prompt = tmp_path / "rank.md"
    prompt.write_text(base_template("rank"))
    monkeypatch.setenv("RUN_DATE", run_date)
    monkeypatch.setattr(rank_mod, "PROMPT_PATH", prompt)
    monkeypatch.setattr(rank_mod, "ranked_path", lambda: tmp_path / "ranked.json")
    monkeypatch.setattr(rank_mod, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(rank_mod, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.setattr(candidates_mod, "connect", lambda: db_mod.connect(db_path))
    # rank's #587 reset clears the scores of featured rows a failed attempt
    # left behind, so retries send those to the ranker again: echo one score.
    def ranker(prompt, label=None, **_kw):
        ids = sorted(set(re.findall(r'"id": "(i\d+)"', prompt)))
        return [{"id": a, "score": score, "tags": [], "why": "w"} for a in ids]

    with patch("rank.invoke_ranker", side_effect=ranker):
        assert rank_mod.main() == 0


def _pool_counts(db_path):
    c = db_mod.connect(db_path)
    try:
        return {
            r[0]: r[1] for r in c.execute(
                "SELECT id, times_competed FROM items WHERE status = ?", (Status.CANDIDATE,)
            )
        }
    finally:
        c.close()


def test_retried_day_counts_one_competition(tmp_path, monkeypatch):
    """Seven rank attempts on one run date age the pool by one day, not seven."""
    import edition

    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    rules = edition.current().section_rules()[Section.PAPERS]
    c = db_mod.connect(db_path)
    # Featured-worthy but below the burst trigger, so the static cap applies
    # and the 3 extra papers lose it and stay in the pool.
    score = rules["featured_min"]
    assert score < rules.get("burst_trigger_score", score + 1)
    for i in range(rules["cap"] + 3):
        _add(c, f"p{i}", score=score)
    c.close()

    for _attempt in range(7):
        _run_rank(db_path, tmp_path, monkeypatch, DAY, score)
    losers = _pool_counts(db_path)
    assert len(losers) == 3
    assert set(losers.values()) == {1}

    _run_rank(db_path, tmp_path, monkeypatch, NEXT_DAY, score)
    assert set(_pool_counts(db_path).values()) == {2}


# ─── guard: one writer of the counter ──────────────────────────────────────

def _sql_strings(path: Path):
    """``(line, text)`` of every string literal; an f-string counts once, with
    its interpolations dropped (their Constant parts are not visited again)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    inside_fstring = {
        id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield node.lineno, "".join(
                str(v.value) for v in node.values if isinstance(v, ast.Constant))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in inside_fstring):
            yield node.lineno, node.value


# An assignment in an UPDATE's SET list (`SET times_competed =` or
# `, times_competed =`), not a comparison such as `WHEN times_competed = 0`.
_WRITES_COUNTER = re.compile(r"(?:\bSET|,)\s*times_competed\s*=", re.I)


def test_guard_regex_tells_assignment_from_comparison():
    assert _WRITES_COUNTER.search("UPDATE items SET times_competed = 0")
    assert _WRITES_COUNTER.search("UPDATE items SET score = 1, times_competed = 0")
    assert not _WRITES_COUNTER.search("SET status = CASE WHEN times_competed = 0 THEN 'x' END")
    assert not _WRITES_COUNTER.search("WHERE times_competed >= ?")


def test_only_bump_times_competed_writes_the_counter():
    writers = [
        f"{p.name}:{line}"
        for p in sorted(SCRIPTS.glob("*.py"))
        for line, text in _sql_strings(p)
        if _WRITES_COUNTER.search(text)
    ]
    assert len(writers) == 1 and writers[0].startswith("db.py:"), (
        f"times_competed must be written only by db.bump_times_competed: {writers}"
    )
    src = (SCRIPTS / "db.py").read_text()
    fn = src[src.index("def bump_times_competed"):]
    fn = fn[: fn.index("\ndef ", 1)]
    assert "last_competed_date" in fn and "times_competed = times_competed + 1" in fn
