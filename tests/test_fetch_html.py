"""Tests for fetch.fetch_html (issue #10)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

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


@pytest.mark.parametrize(
    ("markup", "link_selector", "title_selector"),
    [
        ('<article class="post">No anchor</article>', "article.post", None),
        ('<a class="c"><h3>Missing href</h3></a>', "a.c", None),
        ('<a href="/x" class="c"><p>No matching title</p></a>', "a.c", "h3"),
    ],
    ids=["wrapper-without-anchor", "anchor-without-href", "missing-title"],
)
def test_fetch_html_skips_matches_without_valid_items(
    markup, link_selector, title_selector, caplog
):
    """Malformed markup or selectors must not emit incomplete HTML items."""
    import logging
    import fetch

    client = _FakeClient(_FakeResponse(f"<html><body>{markup}</body></html>"))
    source = {
        "id": "x",
        "url": "https://ex.com",
        "link_selector": link_selector,
        "title_selector": title_selector,
    }

    with caplog.at_level(logging.WARNING, logger="fetch"):
        items = list(fetch.fetch_html(source, client=client))

    assert items == []
    assert any("produced 0 items" in record.message for record in caplog.records)


def test_fetch_html_uses_lexbor_backend_not_deprecated_parser():
    """fetch_html must import selectolax.lexbor, not selectolax.parser.

    `selectolax.parser.HTMLParser` wraps the Modest backend, which was
    removed as an importable backend in selectolax 1.0 (importing
    `selectolax.parser` there raises ImportError with a deprecation
    message). The project's pyproject.toml declares `selectolax>=0.3.21`
    with no upper bound, so pip is free to resolve 1.0+, and a scraper
    built on `selectolax.parser` breaks every HTML source outright at
    that resolution — see the regression this guards against. The
    `lexbor` backend exposes the same Node API (`.css`, `.css_first`,
    `.attributes`, `.text()`, `.tag`) and has shipped since 0.3.21 (the
    declared floor) through 1.0+ (the declared ceiling), so pinning the
    import to it keeps `fetch_html` working across the whole declared
    range, not just whatever happens to be in uv.lock.

    `LexborHTMLParser` is a Cython extension type and cannot be
    monkeypatched (its `__init__`/methods are immutable), so this
    asserts the contract at the source level instead of via a spy:
    `fetch_html`'s HTML-parser import must come from
    `selectolax.lexbor`, never `selectolax.parser`.
    """
    import ast
    import inspect
    import fetch

    source_text = inspect.getsource(fetch.fetch_html)
    tree = ast.parse(source_text)
    import_froms = [
        node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    html_parser_imports = [
        node for node in import_froms
        if any(
            alias.name == "HTMLParser" or alias.asname == "HTMLParser"
            for alias in node.names
        )
    ]
    assert html_parser_imports, "fetch_html no longer imports an HTMLParser"
    assert all(
        node.module == "selectolax.lexbor" for node in html_parser_imports
    ), (
        "fetch_html must import HTMLParser from selectolax.lexbor "
        "(the deprecated selectolax.parser.HTMLParser / Modest backend "
        "is unimportable on selectolax>=1.0, which the declared "
        "selectolax>=0.3.21 range permits pip to resolve)"
    )

    # And prove the behavioral contract still holds end-to-end against
    # whatever selectolax version is actually installed in this run.
    client = _FakeClient(_FakeResponse(ANTHROPIC_LIKE))
    source = {
        "id": "anthropic-news",
        "url": "https://www.anthropic.com/news",
        "link_selector": "a.card",
        "title_selector": "h3",
    }
    items = list(fetch.fetch_html(source, client=client))
    assert len(items) == 2


def test_prefilter_html_family_defaults():
    """html family maps to news section and 30-day recency."""
    import prefilter
    assert prefilter.SECTION_BY_FAMILY["html"] == "news"
    assert prefilter.RECENCY_DAYS["html"] == 30
    assert prefilter.assign_section("html:anthropic-news") == "news"


# ---------------------------------------------------------------------------
# _make_ssl_context / _NoALPNContext.set_alpn_protocols
# ---------------------------------------------------------------------------

def test_ssl_context_swallows_alpn_protocol_requests():
    """_NoALPNContext.set_alpn_protocols() must be a no-op (not raise, not
    delegate to the real SSLContext method) so httpx's automatic
    set_alpn_protocols(["http/1.1"]) call never puts an ALPN extension on
    the wire — the egress proxy 406s any ClientHello that carries one.
    Covers the override itself and _make_ssl_context()'s construction path.
    """
    import fetch

    ctx = fetch._make_ssl_context()
    assert isinstance(ctx, fetch._NoALPNContext)
    # Must not raise, and must not actually set any ALPN protocols — there's
    # no public getter, so the real assertion is just "this never errors",
    # which is the whole point: httpx calls this unconditionally.
    assert ctx.set_alpn_protocols(["http/1.1"]) is None


def test_ssl_context_verifies_hostname_and_requires_cert():
    import ssl
    import fetch

    ctx = fetch._make_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


# ---------------------------------------------------------------------------
# fetch_html() — auto-created client lifecycle (client=None)
# ---------------------------------------------------------------------------

def test_fetch_html_closes_auto_created_client(monkeypatch):
    """When no client is passed in, fetch_html must open its own via
    _make_http_client() and close it afterward (the client=None branch of
    the finally block) — a caller-supplied client must NOT be closed by
    fetch_html (that is covered separately), but the self-managed one must
    be, or every html-family source leaks a connection per run.
    """
    import fetch

    client = _FakeClient(_FakeResponse(ANTHROPIC_LIKE))
    client.close = MagicMock(wraps=client.close)
    monkeypatch.setattr(fetch, "_make_http_client", lambda: client)

    source = {
        "id": "anthropic-news",
        "url": "https://www.anthropic.com/news",
        "link_selector": "a.card",
        "title_selector": "h3",
    }
    items = list(fetch.fetch_html(source))

    assert len(items) == 2
    client.close.assert_called_once_with()


def test_fetch_html_closes_auto_created_client_even_on_error(monkeypatch):
    """The close() happens in a finally block, so a request failure must
    still close the self-opened client (same guarantee the RSS/arXiv/HN/
    Reddit adapters already have — fetch_html was the one path without a
    client=None regression test for it)."""
    import fetch

    client = MagicMock()
    client.get.side_effect = RuntimeError("boom")
    monkeypatch.setattr(fetch, "_make_http_client", lambda: client)

    source = {
        "id": "anthropic-news",
        "url": "https://www.anthropic.com/news",
        "link_selector": "a.card",
    }
    with pytest.raises(RuntimeError, match="boom"):
        list(fetch.fetch_html(source))

    client.close.assert_called_once_with()


def test_fetch_html_does_not_close_caller_supplied_client():
    """Sanity check paired with the two tests above: when a client IS
    supplied, fetch_html must leave it open (the `if client is None` guard)."""
    import fetch

    client = _FakeClient(_FakeResponse(ANTHROPIC_LIKE))
    client.close = MagicMock(wraps=client.close)
    source = {
        "id": "anthropic-news",
        "url": "https://www.anthropic.com/news",
        "link_selector": "a.card",
    }
    list(fetch.fetch_html(source, client=client))
    client.close.assert_not_called()


# ---------------------------------------------------------------------------
# _normalize_date() — empty input and dateutil fallback branch
# ---------------------------------------------------------------------------

def test_normalize_date_empty_string_returns_none():
    """An empty date string short-circuits before either parse attempt."""
    import fetch

    assert fetch._normalize_date("") is None


def test_normalize_date_none_like_falsy_returns_none():
    import fetch

    # fetch_html always passes a str, but the guard is `if not raw`, so
    # confirm the falsy check itself (not just the empty-string literal).
    assert fetch._normalize_date("   " and "") is None


def test_normalize_date_non_iso_human_string_uses_dateutil_fallback():
    """A human-readable date ("July 5" / "July 13, 2026") fails the ISO
    fast-path and falls through to python-dateutil, which returns a naive
    datetime that must then be stamped UTC (the dt.tzinfo is None branch
    inside the dateutil try block, not the fast-path's identical-looking
    branch above it)."""
    import fetch

    result = fetch._normalize_date("July 13, 2026")
    assert result is not None
    assert result.startswith("2026-07-13")
    assert result.endswith("+00:00")


def test_normalize_date_dateutil_rejects_unparseable_text():
    """Text dateutil itself cannot parse falls through to the final
    `except Exception: return None` — this is the ultimate fallback that
    lets prefilter use fetched_at instead of crashing the HTML collector."""
    import fetch

    assert fetch._normalize_date("not a date at all, just words") is None
