"""Every path that seals an item as ``published`` records its ``published_date``.

The daily run (``db.publish_selected_items``) and the backfill
(``db.mark_candidates_published`` via ``backfill --apply-published``) both
seal rows. ``db.reset_processed_items_for`` (``reset_day --force``) finds pool
papers sealed on a day only by ``published_date``, so a sealing statement
that forgets the column leaves papers ``published`` forever with no date: the
issue file can be reset but its papers can never compete again (#705).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import db as db_mod
from models import Section, Status

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DAY = "2026-06-09"
FIRST_SEEN = "2026-06-01"


def _render(node: ast.JoinedStr) -> str:
    """An f-string's text with ``{Status.X}`` resolved to its value and any
    other interpolation left as ``{}``. Implicitly concatenated pieces
    (``f"..." "..."``) are a single JoinedStr in the AST."""
    parts = []
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(str(v.value))
        elif (isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Attribute)
              and isinstance(v.value.value, ast.Name) and v.value.value.id == "Status"):
            parts.append(Status[v.value.attr].value)
        else:
            parts.append("{}")
    return "".join(parts)


def _sql_strings(path: Path) -> list[tuple[int, str]]:
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.JoinedStr):
            out.append((node.lineno, _render(node)))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
    return out


def _published_seals() -> list[tuple[str, int, str]]:
    """``(file, line, set_clause)`` of every ``UPDATE items SET status = 'published'``."""
    seals = []
    for path in sorted(SCRIPTS.glob("*.py")):
        for lineno, text in _sql_strings(path):
            flat = " ".join(text.split())
            if not flat.startswith("UPDATE items SET"):
                continue
            set_clause = flat.split(" WHERE ")[0]
            if f"status = '{Status.PUBLISHED}'" in set_clause:
                seals.append((path.name, lineno, set_clause))
    return seals


def test_guard_finds_both_sealing_paths():
    """The scan must see the daily and the backfill seal, or it checks nothing."""
    assert len(_published_seals()) == 2, _published_seals()


def test_every_published_seal_sets_published_date():
    offenders = [
        f"{f}:{line}: {clause}" for f, line, clause in _published_seals()
        if "published_date" not in clause
    ]
    assert not offenders, (
        "UPDATE items SET status = 'published' without published_date "
        "(reset_day --force cannot restore these rows):\n" + "\n".join(offenders)
    )


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    c = db_mod.connect(path)
    yield c
    c.close()


def _add(conn, item_id: str, status: str) -> None:
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, "
        "status, section, first_seen_date, last_seen_date, appearances) "
        "VALUES (?, 'arxiv:x', ?, ?, 'T', '2026-06-01T00:00:00Z', ?, ?, ?, ?, 1)",
        (item_id, f"https://a.example/{item_id}", f"https://a.example/{item_id}",
         status, Section.PAPERS, FIRST_SEEN, FIRST_SEEN),
    )
    conn.commit()


def _row(conn, item_id: str) -> tuple[str, str | None]:
    r = conn.execute(
        "SELECT status, published_date FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    return r[0], r[1]


def test_both_sealing_paths_record_the_same_date(conn):
    _add(conn, "daily", Status.FEATURED)
    _add(conn, "backfill", Status.CANDIDATE)
    db_mod.publish_selected_items(conn, DAY)
    db_mod.mark_candidates_published(conn, ["backfill"], DAY)
    assert _row(conn, "daily") == (Status.PUBLISHED, DAY)
    assert _row(conn, "backfill") == (Status.PUBLISHED, DAY)


def test_reset_day_restores_backfill_sealed_pool_paper(conn):
    """A pool paper first seen earlier and sealed by a backfill for DAY goes
    back to the pool on ``reset_day --force`` for DAY, like a daily seal."""
    _add(conn, "daily", Status.FEATURED)
    _add(conn, "backfill", Status.CANDIDATE)
    db_mod.publish_selected_items(conn, DAY)
    db_mod.mark_candidates_published(conn, ["backfill"], DAY)

    db_mod.reset_processed_items_for(conn, DAY)

    assert _row(conn, "daily") == (Status.CANDIDATE, None)
    assert _row(conn, "backfill") == (Status.CANDIDATE, None)
