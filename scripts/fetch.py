"""Collect raw items from configured sources into state.db.

No LLM calls. Per-source errors are logged and don't abort the run.
"""
from __future__ import annotations

import html
import json
import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

import feedparser
import httpx
import yaml

import edition
import logsetup
import net
import urls
from models import MetaKey
from db import (
    canonicalize_url, connect, get_meta, init_db, is_allowed_url, prune_source_fetches,
    record_source_fetch, run_date, set_meta, upsert_item, url_id,
)
from db import count_items_seen_on
from models import (
    ARXIV_REQUEST_DELAY_S, REDDIT_DEFAULT_LIMIT, SOURCE_FAMILY_INT_KEYS, ExitCode, Section,
    SourceFamily, is_positive_int, item_source, parse_instant, source_label,
)

log = logging.getLogger("fetch")


# The HTTP client lives in net.py (shared with new_edition.py). These names
# stay as fetch-module attributes because tests and check_edition patch or
# call fetch._make_http_client.
_NoALPNContext = net.NoALPNContext
_make_ssl_context = net.make_ssl_context
_reject_non_public_target = net.reject_non_public_target
_make_http_client = net.make_http_client


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
    return None  # pragma: no cover — feedparser only yields str or struct_time


# ---------- Source adapters ----------

def fetch_rss(source: dict, *, client: httpx.Client | None = None) -> Iterable[Item]:
    url = source["url"]
    log.info("rss: fetching %s (%s)", source["id"], url)
    # Use shared client for connection reuse; fall back to a one-shot client
    # for backward compatibility when called without one.
    _client = client or _make_http_client()
    try:
        resp = _client.get(url)
        resp.raise_for_status()
    finally:
        if client is None:
            _client.close()
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
            source=item_source(SourceFamily.RSS, source),
            url=link,
            title=title.strip(),
            author=author,
            published_at=published,
            raw_text=summary[:4000] if summary else None,
        )


def _family_int(family: SourceFamily, source: dict, key: str) -> int | None:
    """``source[key]`` if it is a positive int, else the models default.

    The family-specific numeric keys (``models.SOURCE_FAMILY_INT_KEYS``) are
    read only through here. A bad value (``"48"``, ``0``, ``-5``, ``true``)
    logs a warning and falls back, as ``limit`` / ``recency_days`` do, so it
    neither reaches the API nor crashes the collector; check_edition rejects it.
    """
    default = SOURCE_FAMILY_INT_KEYS[family][key]
    value = source.get(key)
    if value is None or is_positive_int(value):
        return default if value is None else value
    log.warning("source %s: invalid %s %r (must be positive int); using %s",
                source.get("id", "?"), key, value, default)
    return default


def fetch_arxiv(source: dict, *, client: httpx.Client | None = None) -> Iterable[Item]:
    query = source["query"]
    max_results = _family_int(SourceFamily.ARXIV, source, "max_results")
    log.info("arxiv: fetching %s", source["id"])
    _client = client or _make_http_client()
    try:
        resp = _client.get(urls.ARXIV_API_URL, params=urls.arxiv_query_params(query, max_results))
        resp.raise_for_status()
    finally:
        if client is None:
            _client.close()
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
            source=item_source(SourceFamily.ARXIV, source),
            url=link,
            title=title.strip().replace("\n", " "),
            author=authors or None,
            published_at=published,
            raw_text=summary[:4000] if summary else None,
        )


def fetch_hn(source: dict, *, client: httpx.Client | None = None) -> Iterable[Item]:
    import time
    query = source["query"]
    hours_back = _family_int(SourceFamily.HN, source, "hours_back")
    min_points = _family_int(SourceFamily.HN, source, "min_points")
    cutoff = int(time.time()) - hours_back * 3600
    # `exclude_launches: true` drops Show HN / Launch HN posts at the API.
    # Algolia tags are AND-ed, and a leading `-` negates one.
    tags = "story,-show_hn,-launch_hn" if source.get("exclude_launches") else "story"
    log.info("hn: fetching %s (q=%r)", source["id"], query)
    _client = client or _make_http_client()
    try:
        resp = _client.get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "query": query,
                "tags": tags,
                "numericFilters": f"created_at_i>{cutoff},points>={min_points}",
                "hitsPerPage": 50,
            },
        )
        resp.raise_for_status()
    finally:
        if client is None:
            _client.close()
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
            source=item_source(SourceFamily.HN, source),
            url=link,
            title=title.strip(),
            author=hit.get("author"),
            published_at=published,
            raw_text=text[:4000],
        )


def fetch_html(source: dict, *, client: httpx.Client | None = None) -> Iterable[Item]:
    """Generic HTML index-page scraper for sources without RSS (issue #10).

    Config (per source in sources.yaml):
      id, name, url            — required
      link_selector            — CSS selector matching each post's <a> element.
                                 Must return anchor elements or elements
                                 containing an anchor (we fall back to the
                                 first descendant <a>).
      title_selector           — optional; CSS selector applied *relative to
                                 the link element* to extract the title text.
                                 Default: the link's own text content.
      date_selector            — optional; CSS selector for a published date.
                                 The first `datetime` attribute is preferred;
                                 otherwise the text content is parsed as ISO.
      excerpt_selector         — optional; CSS selector for a summary blurb.
      max_items                — optional positive int; cap emitted items
                                 per fetch (a bad value: warning, no cap).

    Failure modes are defensive per the issue:
      - No matches for link_selector → log a warning, yield nothing.
      - httpx / TLS errors bubble up to the run_collector wrapper (same
        pattern as RSS), so one broken source can't break the whole run.
      - Missing / unparseable date_selector → published_at=None (prefilter
        falls back to fetched_at).
    """
    from urllib.parse import urljoin

    try:
        # Use the lexbor backend explicitly rather than the default
        # `selectolax.parser.HTMLParser` (Modest backend). Modest was
        # removed as an importable backend in selectolax 1.0 — importing
        # `selectolax.parser` there raises ImportError with a deprecation
        # message instead of returning a parser — which broke every HTML
        # source outright under the declared `selectolax>=0.3.21` range
        # the moment pip resolved to 1.0+. The lexbor backend has shipped
        # since 0.3.21 (our floor) and exposes the same Node API used
        # below (`.css`, `.css_first`, `.attributes`, `.text()`, `.tag`),
        # so pinning to it keeps this scraper working across the entire
        # declared range instead of just the one version in uv.lock.
        from selectolax.lexbor import LexborHTMLParser as HTMLParser  # type: ignore
    except ImportError as e:  # pragma: no cover — dependency gate
        log.error("html: selectolax not installed; source %s skipped: %s",
                  source.get("id"), e)
        return

    sid = source["id"]
    url = source["url"]
    link_sel = source.get("link_selector")
    if not link_sel:
        log.warning("html: %s missing link_selector; skipping", sid)
        return

    title_sel = source.get("title_selector")
    date_sel = source.get("date_selector")
    excerpt_sel = source.get("excerpt_selector")
    max_items = _family_int(SourceFamily.HTML, source, "max_items")

    log.info("html: fetching %s (%s)", sid, url)
    _client = client or _make_http_client()
    try:
        resp = _client.get(url)
        resp.raise_for_status()
        body = resp.text
    finally:
        if client is None:
            _client.close()

    tree = HTMLParser(body)
    matches = tree.css(link_sel)
    if not matches:
        log.warning("html: %s — link_selector %r matched 0 elements "
                    "(page structure may have changed)", sid, link_sel)
        return

    seen_urls: set[str] = set()
    emitted = 0
    for element in matches:
        if max_items is not None and emitted >= max_items:
            break

        # link_selector may match the anchor directly or a wrapping element.
        anchor = element if element.tag == "a" else element.css_first("a")
        if anchor is None:
            continue
        href = anchor.attributes.get("href")
        if not href:
            continue
        abs_url = urljoin(url, href)
        if abs_url in seen_urls:
            continue
        seen_urls.add(abs_url)

        if title_sel:
            t_node = element.css_first(title_sel)
            title = (t_node.text(strip=True) if t_node else "").strip()
        else:
            title = anchor.text(strip=True) or ""
        if not title:
            continue

        published_at: str | None = None
        if date_sel:
            d_node = element.css_first(date_sel)
            if d_node is not None:
                # Prefer the machine-readable <time datetime="..."> attribute.
                raw = (d_node.attributes.get("datetime")
                       or d_node.text(strip=True) or "").strip()
                published_at = _normalize_date(raw)

        excerpt = None
        if excerpt_sel:
            e_node = element.css_first(excerpt_sel)
            if e_node is not None:
                excerpt = e_node.text(strip=True) or None

        yield Item(
            source=item_source(SourceFamily.HTML, source),
            url=abs_url,
            title=title,
            author=None,
            published_at=published_at,
            raw_text=(excerpt or "")[:4000] or None,
        )
        emitted += 1

    if emitted == 0:
        log.warning("html: %s produced 0 items (had %d selector matches)",
                    sid, len(matches))


def _normalize_date(raw: str) -> str | None:
    """Best-effort ISO-8601 parse of a date-like string.

    HTML dates come in every shape (`2026-07-13`, `2026-07-13T09:00:00Z`,
    `July 13, 2026`, ...). We handle the common ISO variants directly
    and defer everything else to python-dateutil if available; failing
    that we return None (prefilter falls back to fetched_at).
    """
    if not raw:
        return None
    # Fast path: already looks like ISO.
    try:
        return parse_instant(raw).isoformat()
    except ValueError:
        pass
    try:
        from dateutil import parser as dateparser  # type: ignore
        dt = dateparser.parse(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return None


REDDIT_REQUEST_DELAY = 2.0  # seconds between subreddit requests (429 avoidance)
_REDDIT_LINK_RE = re.compile(r'<a\s+href="([^"]+)"[^>]*>\s*\[link\]\s*</a>', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
# GitHub owner/repo names: letters, digits, '-', '_', '.'; used to build an API
# path, so anything else (e.g. '/', '?', '..') must never reach `gh api`.
_GH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REDDIT_SUBMITTED_BY_RE = re.compile(r"submitted by\s+/u/\S+")


# Shared with new_edition.check_subreddit so its probe hits the same feed.
_reddit_rss_url = urls.reddit_rss_url


def _validate_reddit_limit(value, source_id: str) -> int:
    if value is None:
        return REDDIT_DEFAULT_LIMIT
    if is_positive_int(value):
        return value
    log.warning("source %s: invalid limit %r (must be positive int); using %d",
                source_id, value, REDDIT_DEFAULT_LIMIT)
    return REDDIT_DEFAULT_LIMIT


def fetch_reddit(source: dict, *, client: httpx.Client | None = None) -> Iterable[Item]:
    """Top-N posts of a subreddit via its unauthenticated Atom feed.

    There is no score or comment count in the feed, so the per-source ``limit``
    (default 10) is the gate; ``min_score`` is ignored. Every item still goes
    through the keyword gate and the LLM ranker.
    """
    limit = _validate_reddit_limit(source.get("limit"), source["id"])
    url = _reddit_rss_url(source["url"], limit)
    sub_match = re.search(r"/r/([^/]+)/", urlsplit(url).path)
    sub = sub_match.group(1) if sub_match else source["id"]
    log.info("reddit: fetching %s (%s)", source["id"], url)
    _client = client or _make_http_client()
    try:
        resp = _client.get(url)
        resp.raise_for_status()
    finally:
        if client is None:
            _client.close()
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"feedparser bozo, no entries: {parsed.bozo_exception}")
    for entry in list(parsed.entries)[:limit]:
        title = entry.get("title")
        summary = entry.get("summary") or ""
        m = _REDDIT_LINK_RE.search(summary)
        link = (m.group(1) if m else None) or entry.get("link")
        if not title or not link:
            continue
        link = html.unescape(link)
        body = html.unescape(_TAG_RE.sub(" ", summary))
        body = _REDDIT_SUBMITTED_BY_RE.sub(" ", body).replace("[link]", " ").replace("[comments]", " ")
        body = " ".join(body.split())
        author = entry.get("author")
        if author:
            author = author.removeprefix("/u/")
        published = _to_iso(entry.get("published_parsed") or entry.get("updated_parsed"))
        yield Item(
            source=item_source(SourceFamily.REDDIT, source),
            url=link,
            title=title.strip(),
            author=author or None,
            published_at=published,
            raw_text=f"r/{sub}: {body}"[:4000],
        )


def fetch_github_releases(watchlist: list[dict]) -> Iterable[Item]:
    for entry in watchlist:
        owner, repo = entry["owner"], entry["repo"]
        if (
            not isinstance(owner, str) or not isinstance(repo, str)
            or not _GH_NAME_RE.match(owner) or not _GH_NAME_RE.match(repo)
            or repo in (".", "..")
        ):
            log.warning("gh: skipping invalid owner/repo %r/%r", owner, repo)
            continue
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
                source=item_source(SourceFamily.GITHUB_RELEASES, entry),
                url=url,
                title=f"{owner}/{repo} {tag}".strip(),
                author=owner,
                published_at=rel.get("published_at"),
                raw_text=(rel.get("body") or "")[:4000],
            )


# ---------- Persistence ----------

def upsert_items(
    conn, items: Iterable[Item], now: datetime | None = None
) -> tuple[int, int]:
    """Dedup/insert items into state.db.

    ``now`` stamps ``fetched_at`` on new rows; it defaults to the current UTC
    time. Pass a fixed instant from tests or backfills instead of patching
    ``datetime``.

    Rejects items whose URL scheme isn't http(s) — javascript:, data:,
    file:, etc. — here, at the single choke point every source adapter
    (RSS, arXiv, HN, HTML, reddit, GitHub releases) funnels through, so
    a malicious/compromised feed entry never reaches state.db (and from
    there the rendered site or RSS feed). See issue #248.
    """
    seen, inserted = 0, 0
    today = run_date()
    fetched_at = (now or datetime.now(timezone.utc)).isoformat()
    for it in items:
        seen += 1
        if not is_allowed_url(it.url):
            log.warning("upsert_items: rejecting item with disallowed URL scheme: %r", it.url)
            continue
        canonical = canonicalize_url(it.url)
        item_id = url_id(it.url)
        if upsert_item(  # pragma: no cover — ON CONFLICT DO UPDATE always reports a row
            conn,
            item_id=item_id, source=it.source, url=it.url, canonical_url=canonical,
            title=it.title, author=it.author, published_at=it.published_at,
            fetched_at=fetched_at, raw_text=it.raw_text,
            section_override=it.section_override,
            keyword_gate_bypass=it.keyword_gate_bypass,
            recency_days_override=it.recency_days_override,
            seen_date=today,
        ):
            inserted += 1
    conn.commit()
    return seen, inserted


# ---------- Driver ----------

def sources_path() -> Path:
    """The selected edition's ``sources.yaml``, resolved from ``EDITION`` at call time.

    Not bound at import, so ``import fetch`` works with no edition selected and
    check_edition can import it without patching the environment (#446).
    """
    return edition.current().paths().sources


def load_sources(path: Path | None = None) -> dict:
    with open(path or sources_path()) as f:
        return yaml.safe_load(f)


# Section names come from models.Section so a sources.yaml `section:` override
# accepted by check_edition is never rejected here (and vice versa).
VALID_SECTIONS = frozenset(s.value for s in Section)


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
    if is_positive_int(value):
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


# meta key: the run date for which a fetch completed. reset_day --refetch
# clears it. The skip check keys off this marker, not off item dates, because
# fetch bumps last_seen_date on re-surfaced older items, so item dates can't
# tell "fetched today" from "re-surfaced today" (#321).
FETCH_DONE_KEY = MetaKey.FETCH_DONE


def _already_fetched_today(conn) -> int:
    """Items seen today, or 0 when fetch has not completed for the run date.

    Both the completion marker and rows with last_seen_date = today are
    required: the marker is cleared by ``reset_day.py --refetch``; the rows
    check keeps "delete today's rows to force" working.
    """
    today = run_date()
    if get_meta(conn, FETCH_DONE_KEY) != today:
        return 0
    return count_items_seen_on(conn, today)


def iter_collectors(sources: dict, http_client: httpx.Client):
    """Yield ``(label, items)`` for every configured source, overrides applied.

    *items* is a lazy generator: nothing is fetched until the caller consumes
    it, so the caller owns per-source error isolation. Shared by :func:`main`
    and scripts/check_edition.py so both walk the sources identically.
    """
    def with_override(src: dict, gen):
        sid = src.get("id", "?")
        section_override = _validate_section(src.get("section"), sid)
        kgb = _validate_keyword_gate_bypass(src.get("keyword_gate_bypass"), sid)
        rdo = _validate_recency_days(src.get("recency_days"), sid)
        return _stamp_overrides(gen, section_override, kgb, rdo)

    for src in sources.get(SourceFamily.RSS) or []:
        yield source_label(SourceFamily.RSS, src), with_override(
            src, fetch_rss(src, client=http_client))

    # arXiv asks for >=3 seconds between requests. We have multiple arxiv
    # collectors; sleep between them to avoid 429s.
    arxiv_sources = sources.get(SourceFamily.ARXIV) or []
    for i, src in enumerate(arxiv_sources):
        if i > 0:
            time.sleep(ARXIV_REQUEST_DELAY_S)
        yield source_label(SourceFamily.ARXIV, src), with_override(
            src, fetch_arxiv(src, client=http_client))
    for src in sources.get(SourceFamily.HN) or []:
        yield source_label(SourceFamily.HN, src), with_override(
            src, fetch_hn(src, client=http_client))
    for i, src in enumerate(sources.get(SourceFamily.REDDIT) or []):
        if i > 0:
            time.sleep(REDDIT_REQUEST_DELAY)
        yield source_label(SourceFamily.REDDIT, src), with_override(
            src, fetch_reddit(src, client=http_client))
    # HTML scraper family (issue #10). Vendor pages that don't offer
    # RSS (Anthropic /news, etc.) go here.
    for src in sources.get(SourceFamily.HTML) or []:
        yield source_label(SourceFamily.HTML, src), with_override(
            src, fetch_html(src, client=http_client))
    if sources.get(SourceFamily.GITHUB_RELEASES):
        # GitHub release entries can each carry their own `section:`. Run them
        # one at a time so per-repo overrides take effect.
        for entry in sources[SourceFamily.GITHUB_RELEASES]:
            yield source_label(SourceFamily.GITHUB_RELEASES, entry), with_override(
                entry, fetch_github_releases([entry]))


def main(now: datetime | None = None) -> int:
    """Run the fetch stage.

    ``now`` stamps ``fetched_at`` on every row inserted this run. It defaults
    to the current UTC time; tests and backfills pass a fixed instant instead
    of patching ``datetime`` (same contract as ``prefilter.main``).
    """
    init_db()
    if now is None:
        now = datetime.now(timezone.utc)
    sources = load_sources()
    conn = connect()

    existing = _already_fetched_today(conn)
    if existing > 0:
        log.info(
            "fetch: skip — today already has %d items in DB "
            "(re-running fetch is safe but wasteful; use run.sh --refetch to force)",
            existing,
        )
        conn.close()
        return ExitCode.OK

    total_seen = total_inserted = 0
    errors = 0

    def run_collector(label: str, gen):
        nonlocal total_seen, total_inserted, errors
        try:
            items = list(gen)
        except Exception as e:
            log.exception("%s: collector failed: %s", label, e)
            errors += 1
            record_source_fetch(
                conn, run_date(), label, items_seen=0, items_new=0,
                error=f"{type(e).__name__}: {e}"[:500], fetched_at=now.isoformat(),
            )
            return
        seen, inserted = upsert_items(conn, items, now=now)
        log.info("%s: seen=%d new=%d", label, seen, inserted)
        record_source_fetch(
            conn, run_date(), label, items_seen=seen, items_new=inserted,
            error=None, fetched_at=now.isoformat(),
        )
        total_seen += seen
        total_inserted += inserted

    # Use a single shared HTTP client for all source adapters. This reuses
    # TCP connections across requests to the same host (e.g. multiple RSS
    # feeds on the same domain, or the arXiv API across multiple queries),
    # eliminating redundant TLS handshakes and connection setup.
    with _make_http_client() as http_client:
        for label, gen in iter_collectors(sources, http_client):
            run_collector(label, gen)

    prune_source_fetches(conn, run_date())
    log.info("DONE: seen=%d new=%d errors=%d", total_seen, total_inserted, errors)
    if not (total_seen == 0 and errors > 0):
        set_meta(conn, FETCH_DONE_KEY, run_date())
    conn.close()
    # Per-source errors are logged but don't abort the run, as long as at
    # least one source produced data. This matches the docstring's contract
    # and avoids the common case where one rate-limited collector (arxiv 429)
    # taints an otherwise-successful 400+-item fetch.
    if total_seen == 0 and errors > 0:
        log.error("fetch: ALL collectors failed; aborting")
        return ExitCode.NO_DATA
    return ExitCode.OK


if __name__ == "__main__":
    logsetup.configure()
    sys.exit(main())
