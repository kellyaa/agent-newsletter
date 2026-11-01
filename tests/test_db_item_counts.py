"""db.count_items_seen_on / db.count_items_in_status: the shared item tallies.

fetch, write and publish all report "items considered" and publish counts items
by status; these helpers are the one definition of each so the stages cannot
disagree on what the numbers mean.
"""
from __future__ import annotations

import pytest

import db as db_mod
from models import Status


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    c = db_mod.connect(path)
    rows = [
        ("a", "2026-06-01", Status.FEATURED),
        ("b", "2026-06-01", Status.APPENDIX),
        ("c", "2026-06-01", Status.NEW),
        ("d", "2026-05-31", Status.APPENDIX),
        ("e", "2026-05-31", Status.PUBLISHED),
    ]
    for item_id, seen, status in rows:
        c.execute(
            "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, "
            "first_seen_date, last_seen_date, status) VALUES (?, 's', ?, ?, 't', "
            "'2026-06-01T00:00:00Z', ?, ?, ?)",
            (item_id, f"https://x/{item_id}", f"https://x/{item_id}", seen, seen, status),
        )
    c.commit()
    yield c
    c.close()


def test_count_items_seen_on_counts_only_that_date(conn):
    assert db_mod.count_items_seen_on(conn, "2026-06-01") == 3
    assert db_mod.count_items_seen_on(conn, "2026-05-31") == 2
    assert db_mod.count_items_seen_on(conn, "2026-01-01") == 0


def test_count_items_in_status_single(conn):
    assert db_mod.count_items_in_status(conn, Status.APPENDIX) == 2
    assert db_mod.count_items_in_status(conn, Status.DROPPED) == 0


def test_count_items_in_status_several(conn):
    assert db_mod.count_items_in_status(conn, Status.FEATURED, Status.APPENDIX) == 3


def test_count_items_in_status_requires_a_status(conn):
    with pytest.raises(ValueError):
        db_mod.count_items_in_status(conn)
