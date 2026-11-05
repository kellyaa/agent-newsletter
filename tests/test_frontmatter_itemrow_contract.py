"""The last two unbound models TypedDicts are pinned to their real shapes (#536).

* ``FrontmatterFeatured`` documents what ``write.assemble_issue`` writes into
  each issue's YAML ``featured`` list, and ``AppendixItem`` what it writes
  into ``appendix``. Both lists were built from bare dict literals, so the
  declared shape could drift from the file the site actually ingests. These
  tests assert the emitted keys equal the declared keys, and that the
  declared keys equal the fields of the site's zod schemas in
  ``site/src/content.config.ts`` (the consumer at the other end).
* ``ItemRow`` claims "all columns present" for ``state.db``'s ``items``
  table. It is pinned to the columns of a freshly initialised *and* a
  migrated database, so a new column added to ``SCHEMA``/``migrate`` must be
  declared too.
"""
from __future__ import annotations

import re
import sqlite3
import typing
from pathlib import Path

import pytest
import yaml

import db as db_mod
import write
from models import AppendixItem, FeaturedItem, FrontmatterFeatured, ItemRow

REPO = Path(__file__).resolve().parents[1]
CONTENT_CONFIG = REPO / "site" / "src" / "content.config.ts"


def _zod_object_keys(name: str) -> set[str]:
    src = CONTENT_CONFIG.read_text()
    m = re.search(rf"const {name} = z\.object\(\{{(.*?)\n\}}\);", src, re.S)
    assert m, f"{name} not found in {CONTENT_CONFIG}"
    return set(re.findall(r"^\s{2}(\w+):", m.group(1), re.M))


def _featured(i: int) -> FeaturedItem:
    return FeaturedItem(
        id=f"id-{i}", section="papers", source="arxiv:cs.AI",
        url=f"https://arxiv.org/abs/2401.{i:05d}", title=f"Paper {i}",
        author=None, published_at=None, raw_text="x", score=7,
        tags=["agents"], why="w",
    )


def _frontmatter(text: str) -> dict:
    end = text.find("\n---\n", 4)
    return yaml.safe_load(text[4:end])


def test_assemble_issue_is_annotated_with_writer_item_types():
    hints = typing.get_type_hints(write.assemble_issue)
    assert hints["featured"] == list[FeaturedItem]
    assert hints["appendix_by_section"] == dict[str, list[AppendixItem]]


@pytest.mark.parametrize("takeaway", ["insight", None, ""])
def test_emitted_frontmatter_keys_match_declared_types(takeaway):
    featured = [_featured(0), _featured(1)]
    appendix = {"news": [AppendixItem(
        id="a", section="news", source="hn", url="https://a.com/a", title="A",
    )]}
    writer_output = {"theme": "t", "items": [
        {"id": it["id"], "summary": "s", "takeaway": takeaway, "open_question": None}
        for it in featured
    ]}
    fm = _frontmatter(write.assemble_issue("2026-07-09", featured, appendix, {}, writer_output))

    assert len(fm["featured"]) == 2
    for item in fm["featured"]:
        assert set(item) == set(FrontmatterFeatured.__annotations__)
        assert item["takeaway"] == (takeaway or None)
        assert item["open_question"] is None
    (app,) = fm["appendix"]["news"]
    assert set(app) == set(AppendixItem.__annotations__)


def test_declared_frontmatter_types_match_site_schema():
    assert set(FrontmatterFeatured.__annotations__) == _zod_object_keys("featuredItem")
    assert set(AppendixItem.__annotations__) == _zod_object_keys("appendixItem")


def _columns(conn) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(items)")}


def test_item_row_matches_fresh_items_table(tmp_path):
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    conn = db_mod.connect(p)
    try:
        assert set(ItemRow.__annotations__) == _columns(conn)
    finally:
        conn.close()


def test_item_row_matches_migrated_items_table(tmp_path):
    # A pre-migration DB: items without the columns migrate() adds.
    p = tmp_path / "old.db"
    legacy = sqlite3.connect(p)
    legacy.execute(
        "CREATE TABLE items (id TEXT PRIMARY KEY, source TEXT NOT NULL, "
        "url TEXT NOT NULL, canonical_url TEXT NOT NULL, title TEXT NOT NULL, "
        "author TEXT, published_at TEXT, fetched_at TEXT NOT NULL, raw_text TEXT, "
        "score INTEGER, tags TEXT, section TEXT, section_override TEXT, why TEXT, "
        "status TEXT NOT NULL, first_seen_date TEXT NOT NULL, "
        "last_seen_date TEXT NOT NULL, appearances INTEGER NOT NULL DEFAULT 1)"
    )
    legacy.commit()
    legacy.close()
    db_mod.init_db(p)
    conn = db_mod.connect(p)
    try:
        assert set(ItemRow.__annotations__) == _columns(conn)
    finally:
        conn.close()
