"""write.load_today_items emits exactly the shapes models declares (#536).

models.FeaturedItem / models.AppendixItem document what the writer receives,
but load_today_items built plain dicts field by field, so a key added or
renamed on either side left the "contract" silently wrong. The repo runs no
type checker, so these tests pin the binding at runtime:

* the producer's return annotation names FeaturedItem and AppendixItem;
* emitted featured/appendix items carry exactly the declared keys.
"""
from __future__ import annotations

import typing

import pytest

import db as db_mod
import write
from models import AppendixItem, FeaturedItem, Section, Status


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    c = db_mod.connect(p)
    yield c
    c.close()


def _insert(conn, item_id, section, status, **extra):
    row = {
        "id": item_id, "source": "src", "url": f"https://a.com/{item_id}",
        "canonical_url": f"https://a.com/{item_id}", "title": item_id,
        "fetched_at": "2026-07-09T00:00:00Z", "status": status,
        "section": section, "first_seen_date": "2026-07-09",
        "last_seen_date": "2026-07-09", "published_at": "2026-07-09T00:00:00Z",
        "raw_text": "x", **extra,
    }
    cols = ", ".join(row)
    conn.execute(
        f"INSERT INTO items ({cols}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )
    conn.commit()


def test_producer_is_annotated_with_writer_item_types():
    ret = typing.get_type_hints(write.load_today_items)["return"]
    featured, appendix, metadata = typing.get_args(ret)
    assert featured == list[FeaturedItem]
    assert appendix == dict[str, list[AppendixItem]]
    assert metadata == dict[str, int]


def test_emitted_keys_match_declared_types(conn):
    for s in Section:
        _insert(conn, f"f-{s.value}", s.value, Status.FEATURED.value,
                score=8, tags='["agents"]', why="w")
        _insert(conn, f"a-{s.value}", s.value, Status.APPENDIX.value)

    featured, appendix_by_section, _ = write.load_today_items(conn, "2026-07-09")

    assert len(featured) == len(Section)
    for item in featured:
        assert set(item) == set(FeaturedItem.__annotations__), item["id"]
    for s in Section:
        (item,) = appendix_by_section[s.value]
        assert set(item) == set(AppendixItem.__annotations__), s.value
        assert item["section"] == s.value


def test_appendix_null_section_defaults_to_blogs(conn):
    _insert(conn, "a-null", None, Status.APPENDIX.value)
    _, appendix_by_section, _ = write.load_today_items(conn, "2026-07-09")
    (item,) = appendix_by_section[Section.BLOGS.value]
    assert set(item) == set(AppendixItem.__annotations__)
    assert item["section"] == Section.BLOGS.value
