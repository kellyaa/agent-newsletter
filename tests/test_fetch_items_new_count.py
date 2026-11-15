"""fetch counts an item as new only when its id was not already in ``items`` (#723).

``db.upsert_item`` used to report "inserted" as ``rowcount and lastrowid``.
With ``INSERT ... ON CONFLICT DO UPDATE`` both are set on a conflict update
too: ``lastrowid`` keeps the connection's last real insert. ``fetch.main`` runs
every source on one connection, so once any item was new, every re-seen item
after it also counted as new, in ``source_fetch_outcomes.items_new`` and in the
``seen=... new=...`` log lines operators read for source yield.
"""
from __future__ import annotations

import pytest

import db as db_mod
import fetch as fetch_mod
from fetch import Item, upsert_items


def _item(url: str) -> Item:
    return Item("rss:x", url, "T", None, None, None)


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    return path


def _seed(db_path, urls):
    conn = db_mod.connect(db_path)
    assert upsert_items(conn, [_item(u) for u in urls]) == (len(urls), len(urls))
    conn.close()


OLD = [f"https://old.example/{i}" for i in range(5)]


def test_reseen_after_a_new_insert_on_same_connection_is_not_new(db_path):
    """The #723 repro: one new item first, then five re-seen ones."""
    _seed(db_path, OLD)
    conn = db_mod.connect(db_path)
    assert upsert_items(conn, [_item("https://new.example/1")] + [_item(u) for u in OLD]) == (6, 1)
    # A later source on the same connection that only re-serves old items.
    assert upsert_items(conn, [_item(u) for u in OLD]) == (5, 0)
    conn.close()


def test_new_after_reseen_is_counted(db_path):
    _seed(db_path, OLD)
    conn = db_mod.connect(db_path)
    items = [_item(u) for u in OLD] + [_item("https://new.example/a"), _item("https://new.example/b")]
    assert upsert_items(conn, items) == (7, 2)
    conn.close()


def test_duplicate_within_one_batch_counts_once(db_path):
    conn = db_mod.connect(db_path)
    u = "https://new.example/dup"
    assert upsert_items(conn, [_item(u), _item(u)]) == (2, 1)
    assert conn.execute("SELECT appearances FROM items").fetchone()[0] == 2
    conn.close()


def test_upsert_item_reports_insert_vs_update(db_path):
    conn = db_mod.connect(db_path)
    kw = dict(
        source="rss:x", url="https://x/a", canonical_url="https://x/a", title="T",
        author=None, published_at=None, fetched_at="t", raw_text=None,
        section_override=None, keyword_gate_bypass=False,
        recency_days_override=None, seen_date="2026-06-01",
    )
    assert db_mod.upsert_item(conn, item_id="a", **kw) is True
    assert db_mod.upsert_item(conn, item_id="b", **kw) is True
    # lastrowid is now b's rowid; a conflict update on a must still say "not new".
    assert db_mod.upsert_item(conn, item_id="a", **kw) is False
    assert db_mod.upsert_item(conn, item_id="b", **kw) is False
    conn.close()


def test_fetch_main_records_items_new_per_source(db_path, tmp_path, monkeypatch):
    """End to end: source ``a`` brings one new item, ``b`` only re-serves old ones."""
    _seed(db_path, OLD)
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "rss:\n"
        "  - id: a\n    url: https://example.com/a.xml\n"
        "  - id: b\n    url: https://example.com/b.xml\n"
    )
    monkeypatch.setenv("RUN_DATE", "2026-07-09")
    monkeypatch.setattr(fetch_mod, "sources_path", lambda: sources)
    monkeypatch.setattr(fetch_mod, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(fetch_mod, "init_db", lambda: db_mod.init_db(db_path))
    # _already_fetched_today skips the run if items were seen "today"; the seed
    # rows were stamped with the real local date, so move them to an earlier day.
    conn = db_mod.connect(db_path)
    conn.execute("UPDATE items SET first_seen_date = '2026-07-01', last_seen_date = '2026-07-01'")
    conn.commit()
    conn.close()

    def fake_rss(src, **kw):
        if src["id"] == "a":
            return [_item("https://new.example/1"), _item(OLD[0])]
        return [_item(u) for u in OLD]

    monkeypatch.setattr(fetch_mod, "fetch_rss", fake_rss)
    assert fetch_mod.main() == 0
    conn = db_mod.connect(db_path)
    rows = {r["label"]: (r["items_seen"], r["items_new"]) for r in conn.execute(
        "SELECT label, items_seen, items_new FROM source_fetch_outcomes")}
    conn.close()
    assert rows == {"rss/a": (2, 1), "rss/b": (5, 0)}
