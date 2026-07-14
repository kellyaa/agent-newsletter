"""Tests for fetch.fetch_html (issue #10)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


class _FakeResponse:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self._status = status
        self.content = text.encode()

    def raise_for_status(self):
        if self._status >= 400:
            import httpx
            raise httpx.HTTPStatusError("bad", request=None, response=None)


class _FakeClient:
    """Minimal httpx.Client stand-in for tests."""

    def __init__(self, response: _FakeResponse):
        self._response = response
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append((url, kwargs))
        return self._response

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


ANTHROPIC_LIKE = """
<html><body>
  <main>
    <a href="/news/claude-4-release" class="card">
      <h3>Claude 4 is out</h3>
      <time datetime="2026-07-10">July 10, 2026</time>
      <p class="excerpt">Better tool use and longer context.</p>
    </a>
    <a href="/news/model-context-protocol" class="card">
      <h3>MCP 1.0</h3>
      <time datetime="2026-07-05">July 5</time>
      <p class="excerpt">Standardizing the tool-use interface.</p>
    </a>
  </main>
</body></html>
"""


def test_fetch_html_happy_path():
    import fetch
    client = _FakeClient(_FakeResponse(ANTHROPIC_LIKE))
    source = {
        "id": "anthropic-news",
        "name": "Anthropic News",
        "url": "https://www.anthropic.com/news",
        "link_selector": "a.card",
        "title_selector": "h3",
        "date_selector": "time",
        "excerpt_selector": ".excerpt",
    }
    items = list(fetch.fetch_html(source, client=client))
    assert len(items) == 2
    urls = [it.url for it in items]
    assert "https://www.anthropic.com/news/claude-4-release" in urls
    assert "https://www.anthropic.com/news/model-context-protocol" in urls
    it = items[0]
    assert it.source == "html:anthropic-news"
    assert it.title == "Claude 4 is out"
    assert it.published_at is not None
    assert "2026-07-10" in it.published_at
    assert "Better tool use" in it.raw_text


def test_fetch_html_missing_link_selector_returns_empty(caplog):
    import fetch
    client = _FakeClient(_FakeResponse(ANTHROPIC_LIKE))
    items = list(fetch.fetch_html(
        {"id": "x", "url": "https://ex.com"}, client=client))
    assert items == []


def test_fetch_html_no_matches_logs_warning(caplog):
    import fetch
    import logging
    caplog.set_level(logging.WARNING)
    client = _FakeClient(_FakeResponse("<html><body>nothing</body></html>"))
    items = list(fetch.fetch_html(
        {"id": "x", "url": "https://ex.com", "link_selector": "a.card"},
        client=client))
    assert items == []
    assert any("matched 0 elements" in r.message for r in caplog.records)


def test_fetch_html_dedups_same_url():
    """Repeated identical hrefs in the DOM shouldn't produce duplicate items."""
    import fetch
    body = """
    <a href="/a" class="c"><h3>t1</h3></a>
    <a href="/a" class="c"><h3>t1 dupe</h3></a>
    <a href="/b" class="c"><h3>t2</h3></a>
    """
    client = _FakeClient(_FakeResponse(f"<html><body>{body}</body></html>"))
    items = list(fetch.fetch_html(
        {"id": "x", "url": "https://ex.com",
         "link_selector": "a.c", "title_selector": "h3"},
        client=client))
    urls = {it.url for it in items}
    assert urls == {"https://ex.com/a", "https://ex.com/b"}


def test_fetch_html_max_items_caps_output():
    import fetch
    body = "".join(f'<a href="/p{i}" class="c"><h3>t{i}</h3></a>' for i in range(10))
    client = _FakeClient(_FakeResponse(f"<html><body>{body}</body></html>"))
    items = list(fetch.fetch_html(
        {"id": "x", "url": "https://ex.com", "link_selector": "a.c",
         "max_items": 3, "title_selector": "h3"},
        client=client))
    assert len(items) == 3


def test_fetch_html_missing_title_falls_back_to_anchor_text():
    import fetch
    body = '<a href="/x" class="c">Just the anchor text</a>'
    client = _FakeClient(_FakeResponse(f"<html><body>{body}</body></html>"))
    items = list(fetch.fetch_html(
        {"id": "x", "url": "https://ex.com", "link_selector": "a.c"},
        client=client))
    assert items[0].title == "Just the anchor text"


def test_fetch_html_missing_date_yields_none():
    import fetch
    body = '<a href="/x" class="c"><h3>t</h3></a>'
    client = _FakeClient(_FakeResponse(f"<html><body>{body}</body></html>"))
    items = list(fetch.fetch_html(
        {"id": "x", "url": "https://ex.com", "link_selector": "a.c",
         "title_selector": "h3", "date_selector": "time"},
        client=client))
    assert items[0].published_at is None


def test_fetch_html_unparseable_date_falls_back_to_none():
    import fetch
    body = '<a href="/x" class="c"><h3>t</h3><time>not a date</time></a>'
    client = _FakeClient(_FakeResponse(f"<html><body>{body}</body></html>"))
    items = list(fetch.fetch_html(
        {"id": "x", "url": "https://ex.com", "link_selector": "a.c",
         "title_selector": "h3", "date_selector": "time"},
        client=client))
    assert items[0].published_at is None


def test_fetch_html_relative_hrefs_absolutized():
    import fetch
    body = '<a href="/rel/x" class="c"><h3>t</h3></a>'
    client = _FakeClient(_FakeResponse(f"<html><body>{body}</body></html>"))
    items = list(fetch.fetch_html(
        {"id": "x", "url": "https://ex.com/sub/", "link_selector": "a.c",
         "title_selector": "h3"},
        client=client))
    assert items[0].url == "https://ex.com/rel/x"


def test_fetch_html_matches_wrapping_element_with_anchor():
    """link_selector may match a wrapper; we take its first <a>."""
    import fetch
    body = """
    <article class="post">
      <a href="/deep-link"><h3>the title</h3></a>
    </article>
    """
    client = _FakeClient(_FakeResponse(f"<html><body>{body}</body></html>"))
    items = list(fetch.fetch_html(
        {"id": "x", "url": "https://ex.com", "link_selector": "article.post",
         "title_selector": "h3"},
        client=client))
    assert items[0].url == "https://ex.com/deep-link"
    assert items[0].title == "the title"


def test_prefilter_html_family_defaults():
    """html family maps to news section and 30-day recency."""
    import prefilter
    assert prefilter.SECTION_BY_FAMILY["html"] == "news"
    assert prefilter.RECENCY_DAYS["html"] == 30
    assert prefilter.assign_section("html:anthropic-news") == "news"
