"""Candidate pool query — shared between prefilter (write) and rank (read).

This module provides the canonical way to load the current candidate pool
from state.db, grouped by section and split into scored/unscored buckets.

Previously this logic was duplicated:
  - prefilter.py assembled the grouped dict and wrote it to candidates.json
  - rank.py read candidates.json and immediately cross-checked it against the
    DB to filter out already-ranked items

Now both stages call load_candidates_from_db() directly. prefilter still
writes candidates.json as a *debug artifact* (useful for inspecting what the
ranker will see), but rank.py no longer depends on the file existing.

This eliminates:
  1. Implicit filesystem coupling between stages
  2. Stale-read risk if stages are ever parallelized
  3. The "missing candidates.json" error path in rank.py
  4. Schema drift between prefilter's writer and rank's reader
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from db import connect

log = logging.getLogger(__name__)


def load_candidates_from_db(
    conn=None,
    *,
    prerank_cap: int | None = None,
    prerank_scorer: Any | None = None,
    now: datetime | None = None,
) -> dict[str, list[dict]]:
    """Load the current candidate pool from state.db, grouped by section.

    Returns a dict with keys: papers, papers_prescored, news, blogs.

    Parameters
    ----------
    conn : sqlite3.Connection, optional
        An existing DB connection. If None, one is opened and closed internally.
    prerank_cap : int, optional
        If set, cap the unscored papers bucket to this many items by
        applying *prerank_scorer*. Items beyond the cap stay in the DB
        (they'll re-compete on the next run).
    prerank_scorer : callable, optional
        A function (item: dict, now: datetime) -> float used to sort unscored
        papers when prerank_cap is applied. Higher scores sort first.
    now : datetime, optional
        The reference time for the prerank scorer. Defaults to UTC now.
    """
    close_conn = False
    if conn is None:
        conn = connect()
        close_conn = True

    try:
        return _load(conn, prerank_cap, prerank_scorer, now)
    finally:
        if close_conn:
            conn.close()


def _load(conn, prerank_cap, prerank_scorer, now) -> dict[str, list[dict]]:
    """Internal: query and group candidates."""
    grouped: dict[str, list[dict]] = {
        "papers": [],
        "papers_prescored": [],
        "news": [],
        "blogs": [],
    }

    rows = conn.execute(
        """
        SELECT id, source, url, title, author, published_at, raw_text,
               section, section_override, score, tags, why
        FROM items
        WHERE status = 'candidate'
        """
    ).fetchall()

    for r in rows:
        d = dict(r)
        section = d.get("section") or "blogs"
        emitted: dict[str, Any] = {
            "id": d["id"],
            "source": d["source"],
            "url": d["url"],
            "title": d["title"],
            "author": d["author"],
            "published_at": d["published_at"],
            "raw_text": d["raw_text"],
        }
        if section == "papers" and d.get("score") is not None:
            # Prescored paper — carry cached score/tags/why so the ranker
            # can skip the LLM call for this item.
            emitted["score"] = d["score"]
            try:
                emitted["tags"] = json.loads(d["tags"]) if d["tags"] else []
            except (TypeError, ValueError):
                emitted["tags"] = []
            emitted["why"] = d["why"] or ""
            grouped["papers_prescored"].append(emitted)
        elif section in grouped:
            grouped[section].append(emitted)
        else:
            grouped["blogs"].append(emitted)

    # Apply pre-rank cap to unscored papers if requested.
    if prerank_cap and len(grouped["papers"]) > prerank_cap:
        if now is None:
            now = datetime.now(timezone.utc)
        if prerank_scorer is not None:
            before = len(grouped["papers"])
            grouped["papers"].sort(
                key=lambda it: prerank_scorer(it, now), reverse=True
            )
            grouped["papers"] = grouped["papers"][:prerank_cap]
            log.info(
                "papers pre-rank cap: %d unscored → %d",
                before, len(grouped["papers"]),
            )

    return grouped
