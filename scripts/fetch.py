"""Collect raw items from configured sources into state.db.

No LLM calls. Per-source errors are logged and don't abort the run.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import feedparser
import httpx
import yaml

from db import REPO_ROOT, canonicalize_url, connect, init_db, url_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("fetch")

SOURCES_PATH = REPO_ROOT / "sources.yaml"
USER_AGENT = "agent-newsletter/0.1 (+https://github.com/; bot)"
HTTP_TIMEOUT = 20.0


@dataclass
class Item:
    source: str
    url: str
    title: str
    author: str | None
    published_at: str | None  # ISO8601
    raw_text: str | None
    section_override: str | None = None  # explicit section: from sources.yaml
    keyword_gate_bypass: bool = False  # explicit keyword_gate_bypass: from sources.yaml
    recency_days_override: int | None = None  # explicit recency_days: from sources.yaml


def _to_iso(value) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, "tm_year"):
        try:
            return datetime(*value[:6], tzinfo=timezone.utc).isoformat()
        except Exception:
            return None
    return None


# ---------- Source adapters ----------

def fetch_rss(source: dict) -> Iterable[Item]:
    url = source["url"]
    log.info("rss: fetching %s (%s)", source["id"], url)
    # Pre-fetch with httpx so we get reliable redirect handling and HTTPS upgrades.
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT},
                      follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"feedparser bozo, no entries: {parsed.bozo_exception}")
    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue
        summary = entry.get("summary") or entry.get("description") or ""
        author = entry.get("author")
        published = _to_iso(entry.get("published_parsed") or entry.get("updated_parsed"))
        yield Item(
            source=f"rss:{source['id']}",
            url=link,
            title=title.strip(),
            author=author,
            published_at=published,
            raw_text=summary[:4000] if summary else None,
        )


def fetch_arxiv(source: dict) -> Iterable[Item]:
    query = source["query"]
    max_results = source.get("max_results", 30)
    log.info("arxiv: fetching %s", source["id"])
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT},
                      follow_redirects=True) as client:
        resp = client.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": query,
                "start": 0,
                "max_results": max_results,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
        )
        resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue
        summary = entry.get("summary") or ""
        authors = ", ".join(a.name for a in entry.get("authors", []) if hasattr(a, "name"))
        published = _to_iso(entry.get("published_parsed") or entry.get("updated_parsed"))
        yield Item(
            source=f"arxiv:{source['id']}",
            url=link,
            title=title.strip().replace("\n", " "),
            author=authors or None,
            published_at=published,
            raw_text=summary[:4000] if summary else None,
        )


def fetch_hn(source: dict) -> Iterable[Item]:
    import time
    query = source["query"]
    hours_back = source.get("hours_back", 48)
    min_points = source.get("min_points", 50)
    cutoff = int(time.time()) - hours_back * 3600
    log.info("hn: fetching %s (q=%r)", source["id"], query)
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "query": query,
                "tags": "story",
                "numericFilters": f"created_at_i>{cutoff},points>={min_points}",
                "hitsPerPage": 50,
            },
        )
        resp.raise_for_status()
    data = resp.json()
    for hit in data.get("hits", []):
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
        title = hit.get("title")
        if not title:
            continue
        published = None
        if hit.get("created_at"):
            published = hit["created_at"]
        text = (
            f"HN: {hit.get('points', 0)} points, "
            f"{hit.get('num_comments', 0)} comments. "
            f"{hit.get('story_text') or ''}"
        )
        yield Item(
            source=f"hn:{source['id']}",
            url=link,
            title=title.strip(),
            author=hit.get("author"),
            published_at=published,
            raw_text=text[:4000],
        )


def fetch_reddit(source: dict) -> Iterable[Item]:
    url = source["url"]
    min_score = source.get("min_score", 100)
    log.info("reddit: fetching %s", source["id"])
    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(url)
        resp.raise_for_status()
    data = resp.json()
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        if post.get("score", 0) < min_score:
            continue
        link = post.get("url_overridden_by_dest") or f"https://www.reddit.com{post.get('permalink', '')}"
        title = post.get("title")
        if not title or not link:
            continue
        published = None
        if post.get("created_utc"):
            published = datetime.fromtimestamp(post["created_utc"], tz=timezone.utc).isoformat()
        text = (
            f"r/{post.get('subreddit')}: {post.get('score', 0)} score, "
            f"{post.get('num_comments', 0)} comments. "
            f"{(post.get('selftext') or '')[:2000]}"
        )
        yield Item(
            source=f"reddit:{source['id']}",
            url=link,
            title=title.strip(),
            author=post.get("author"),
            published_at=published,
            raw_text=text[:4000],
        )


def fetch_github_releases(watchlist: list[dict]) -> Iterable[Item]:
    for entry in watchlist:
        owner, repo = entry["owner"], entry["repo"]
        log.info("gh: fetching releases for %s/%s", owner, repo)
        try:
            result = subprocess.run(
                ["gh", "api", f"repos/{owner}/{repo}/releases?per_page=5"],
                capture_output=True, text=True, timeout=30, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            log.warning("gh: failed for %s/%s: %s", owner, repo, e)
            continue
        try:
            releases = json.loads(result.stdout)
        except json.JSONDecodeError:
            log.warning("gh: invalid JSON for %s/%s", owner, repo)
            continue
        for rel in releases:
            url = rel.get("html_url")
            tag = rel.get("tag_name") or rel.get("name") or ""
            if not url:
                continue
            yield Item(
                source=f"gh:{owner}/{repo}",
                url=url,
                title=f"{owner}/{repo} {tag}".strip(),
                author=owner,
                published_at=rel.get("published_at"),
                raw_text=(rel.get("body") or "")[:4000],
            )


# ---------- Persistence ----------

def upsert_items(conn, items: Iterable[Item]) -> tuple[int, int]:
    seen, inserted = 0, 0
    today = datetime.now(timezone.utc).date().isoformat()
    fetched_at = datetime.now(timezone.utc).isoformat()
    for it in items:
        seen += 1
        canonical = canonicalize_url(it.url)
        item_id = url_id(it.url)
        cur = conn.execute(
            """
            INSERT INTO items (id, source, url, canonical_url, title, author,
                               published_at, fetched_at, raw_text, status,
                               section_override, keyword_gate_bypass,
                               recency_days_override,
                               first_seen_date, last_seen_date, appearances)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, 1)
            ON CONFLICT(id) DO UPDATE SET
                last_seen_date = excluded.last_seen_date,
                appearances = items.appearances + 1,
                section_override = COALESCE(excluded.section_override,
                                            items.section_override),
                keyword_gate_bypass = MAX(excluded.keyword_gate_bypass,
                                          items.keyword_gate_bypass),
                recency_days_override = COALESCE(excluded.recency_days_override,
                                                 items.recency_days_override)
            """,
            (
                item_id, it.source, it.url, canonical, it.title, it.author,
                it.published_at, fetched_at, it.raw_text,
                it.section_override, 1 if it.keyword_gate_bypass else 0,
                it.recency_days_override,
                today, today,
            ),
        )
        if cur.rowcount and cur.lastrowid:
            inserted += 1
    conn.commit()
    return seen, inserted


# ---------- Driver ----------

def load_sources() -> dict:
    with open(SOURCES_PATH) as f:
        return yaml.safe_load(f)


VALID_SECTIONS = {"papers", "news", "blogs"}


def _validate_section(value, source_id: str) -> str | None:
    if value is None:
        return None
    if value in VALID_SECTIONS:
        return value
    log.warning("source %s: invalid section %r (allowed: %s); ignoring",
                source_id, value, sorted(VALID_SECTIONS))
    return None


def _validate_keyword_gate_bypass(value, source_id: str) -> bool:
    if value is None or value is False:
        return False
    if value is True:
        return True
    log.warning("source %s: invalid keyword_gate_bypass %r (must be bool); treating as false",
                source_id, value)
    return False


def _validate_recency_days(value, source_id: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass in Python; reject explicitly
        log.warning("source %s: invalid recency_days %r (must be positive int); ignoring",
                    source_id, value)
        return None
    if isinstance(value, int) and value > 0:
        return value
    log.warning("source %s: invalid recency_days %r (must be positive int); ignoring",
                source_id, value)
    return None


def _stamp_overrides(
    items: Iterable[Item],
    section_override: str | None,
    keyword_gate_bypass: bool,
    recency_days_override: int | None,
) -> Iterable[Item]:
    """Set per-source overrides on each item."""
    for it in items:
        if section_override is not None:
            it.section_override = section_override
        if keyword_gate_bypass:
            it.keyword_gate_bypass = True
        if recency_days_override is not None:
            it.recency_days_override = recency_days_override
        yield it


def _already_fetched_today(conn) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) FROM items WHERE last_seen_date = ?", (today,)
    ).fetchone()
    return int(row[0]) if row else 0


def main() -> int:
    init_db()
    sources = load_sources()
    conn = connect()

    existing = _already_fetched_today(conn)
    if existing > 0:
        log.info(
            "fetch: skip — today already has %d items in DB "
            "(re-running fetch is safe but wasteful; delete today's rows to force)",
            existing,
        )
        conn.close()
        return 0

    total_seen = total_inserted = 0
    errors = 0

    def run_collector(label: str, gen):
        nonlocal total_seen, total_inserted, errors
        try:
            items = list(gen)
        except Exception as e:
            log.exception("%s: collector failed: %s", label, e)
            errors += 1
            return
        seen, inserted = upsert_items(conn, items)
        log.info("%s: seen=%d new=%d", label, seen, inserted)
        total_seen += seen
        total_inserted += inserted

    def with_override(src: dict, gen):
        sid = src.get("id", "?")
        section_override = _validate_section(src.get("section"), sid)
        kgb = _validate_keyword_gate_bypass(src.get("keyword_gate_bypass"), sid)
        rdo = _validate_recency_days(src.get("recency_days"), sid)
        return _stamp_overrides(gen, section_override, kgb, rdo)

    for src in sources.get("rss", []):
        run_collector(f"rss/{src['id']}", with_override(src, fetch_rss(src)))

    # arXiv asks for >=3 seconds between requests. We have multiple arxiv
    # collectors; sleep between them to avoid 429s.
    arxiv_sources = sources.get("arxiv", [])
    for i, src in enumerate(arxiv_sources):
        if i > 0:
            time.sleep(3.0)
        run_collector(f"arxiv/{src['id']}", with_override(src, fetch_arxiv(src)))
    for src in sources.get("hn", []):
        run_collector(f"hn/{src['id']}", with_override(src, fetch_hn(src)))
    for src in sources.get("reddit", []):
        run_collector(f"reddit/{src['id']}", with_override(src, fetch_reddit(src)))
    if sources.get("github_releases"):
        # GitHub release entries can each carry their own `section:`. Run them
        # one at a time so per-repo overrides take effect.
        for entry in sources["github_releases"]:
            label = f"gh/{entry['owner']}/{entry['repo']}"
            run_collector(label, with_override(entry, fetch_github_releases([entry])))

    log.info("DONE: seen=%d new=%d errors=%d", total_seen, total_inserted, errors)
    conn.close()
    # Per-source errors are logged but don't abort the run, as long as at
    # least one source produced data. This matches the docstring's contract
    # and avoids the common case where one rate-limited collector (arxiv 429)
    # taints an otherwise-successful 400+-item fetch.
    if total_seen == 0 and errors > 0:
        log.error("fetch: ALL collectors failed; aborting")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
