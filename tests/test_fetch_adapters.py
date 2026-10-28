"""Tests for fetch.py source adapter functions and main() pipeline.

All network calls (httpx) and subprocess calls (gh api) are mocked.
No real network traffic is made.

Coverage targets:
  - fetch_rss()     — httpx + feedparser, entry parsing, bozo handling
  - fetch_arxiv()   — httpx + feedparser, multi-author, title whitespace
  - fetch_hn()      — httpx JSON, score/comments, missing url fallback
  - fetch_reddit()  — httpx Atom feed, top-N limit, permalink fallback
  - fetch_github_releases() — subprocess, JSON parse, missing url skip
  - main()          — sources orchestration, already-fetched guard,
                      per-source error isolation, all-failed abort
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import time

import pytest

import fetch
from fetch import (
    fetch_rss,
    fetch_arxiv,
    fetch_hn,
    fetch_reddit,
    fetch_github_releases,
    Item,
)


# ---------------------------------------------------------------------------
# Helpers to build minimal feedparser-like entries
# ---------------------------------------------------------------------------

def _rss_feed_content(entries: list[dict]) -> bytes:
    """Build minimal RSS XML bytes for feedparser to parse."""
    items_xml = ""
    for e in entries:
        pub = e.get("pubDate", "Thu, 09 Jul 2026 12:00:00 +0000")
        desc = e.get("description", "")
        items_xml += f"""
        <item>
          <title>{e.get('title', 'Test Title')}</title>
          <link>{e.get('link', 'https://example.com/1')}</link>
          <description>{desc}</description>
          <author>{e.get('author', '')}</author>
          <pubDate>{pub}</pubDate>
        </item>"""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    {items_xml}
  </channel>
</rss>"""
    return xml.encode("utf-8")


def _arxiv_feed_content(entries: list[dict]) -> str:
    """Build minimal Atom XML string for feedparser to parse as arXiv feed."""
    entries_xml = ""
    for e in entries:
        authors_xml = "".join(
            f"<author><name>{a}</name></author>"
            for a in e.get("authors", ["Test Author"])
        )
        entries_xml += f"""
  <entry>
    <id>http://arxiv.org/abs/{e.get('arxiv_id', '2401.00001')}v1</id>
    <link href="http://arxiv.org/abs/{e.get('arxiv_id', '2401.00001')}"/>
    <title>{e.get('title', 'Test Paper Title')}</title>
    <summary>{e.get('summary', 'Paper abstract.')}</summary>
    {authors_xml}
    <published>{e.get('published', '2026-07-09T00:00:00Z')}</published>
  </entry>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query</title>
  {entries_xml}
</feed>"""


def _mock_httpx_response(content, *, content_type="text/xml; charset=utf-8",
                          is_json=False, status_code=200):
    """Create a mock httpx Response."""
    resp = MagicMock()
    resp.status_code = status_code
    if is_json:
        resp.content = json.dumps(content).encode()
        resp.json.return_value = content
        resp.text = json.dumps(content)
    else:
        resp.content = content if isinstance(content, bytes) else content.encode()
        resp.text = content if isinstance(content, str) else content.decode()
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# fetch_rss()
# ---------------------------------------------------------------------------

class TestFetchRss:
    def _source(self, url="https://example.com/feed.xml"):
        return {"id": "test-blog", "url": url}

    def test_yields_items_from_feed(self):
        content = _rss_feed_content([
            {"title": "Agent workflow", "link": "https://example.com/1",
             "description": "About agents", "author": "Alice"},
        ])
        mock_resp = _mock_httpx_response(content)

        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx

            items = list(fetch_rss(self._source()))

        assert len(items) == 1
        assert items[0].title == "Agent workflow"
        assert items[0].url == "https://example.com/1"
        assert items[0].source == "rss:test-blog"
        assert items[0].author == "Alice"

    def test_raw_text_from_description(self):
        content = _rss_feed_content([
            {"title": "T", "link": "https://example.com/1",
             "description": "Some summary text"},
        ])
        mock_resp = _mock_httpx_response(content)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_rss(self._source()))
        assert items[0].raw_text is not None
        assert "Some summary text" in items[0].raw_text

    def test_raw_text_truncated_at_4000(self):
        long_desc = "x" * 5000
        content = _rss_feed_content([
            {"title": "T", "link": "https://example.com/1", "description": long_desc},
        ])
        mock_resp = _mock_httpx_response(content)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_rss(self._source()))
        assert len(items[0].raw_text) <= 4000

    def test_entries_missing_link_skipped(self):
        # Entry without link
        content = _rss_feed_content([
            {"title": "No Link Entry", "link": "", "description": "text"},
            {"title": "Has Link", "link": "https://example.com/2", "description": ""},
        ])
        mock_resp = _mock_httpx_response(content)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_rss(self._source()))
        # feedparser still parses them - entry without link may or may not pass depending
        # on how feedparser handles the empty string; test the non-empty one is there
        titles = [it.title for it in items]
        assert "Has Link" in titles

    def test_bozo_feed_with_no_entries_raises(self):
        """feedparser bozo with no entries → RuntimeError."""
        import feedparser

        bozo_result = MagicMock()
        bozo_result.bozo = True
        bozo_result.entries = []
        bozo_result.bozo_exception = Exception("bad feed")

        content = b"this is not valid xml"
        mock_resp = _mock_httpx_response(content)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            with patch("feedparser.parse", return_value=bozo_result):
                with pytest.raises(RuntimeError, match="bozo"):
                    list(fetch_rss(self._source()))

    def test_http_error_propagates(self):
        """HTTP error from raise_for_status() propagates as-is."""
        import httpx
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            with pytest.raises(httpx.HTTPStatusError):
                list(fetch_rss(self._source()))

    def test_empty_feed_yields_nothing(self):
        content = _rss_feed_content([])
        mock_resp = _mock_httpx_response(content)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_rss(self._source()))
        assert items == []


FETCHERS_WITH_HTTP_CLIENT = [
    pytest.param(
        fetch_rss,
        {"id": "test-blog", "url": "https://example.com/feed.xml"},
        id="rss",
    ),
    pytest.param(
        fetch_arxiv,
        {"id": "cs-ai", "query": "cat:cs.AI"},
        id="arxiv",
    ),
    pytest.param(
        fetch_hn,
        {"id": "agents", "query": "agents"},
        id="hacker-news",
    ),
    pytest.param(
        fetch_reddit,
        {"id": "localllama", "url": "https://reddit.com/r/LocalLLaMA/.json"},
        id="reddit",
    ),
]


@pytest.mark.parametrize(("fetcher", "source"), FETCHERS_WITH_HTTP_CLIENT)
def test_fetcher_closes_created_http_client_when_request_fails(
    fetcher, source, monkeypatch
):
    client = MagicMock()
    client.get.side_effect = RuntimeError("request failed")
    monkeypatch.setattr("fetch._make_http_client", lambda: client)

    with pytest.raises(RuntimeError, match="request failed"):
        list(fetcher(source))

    client.close.assert_called_once_with()


@pytest.mark.parametrize(("fetcher", "source"), FETCHERS_WITH_HTTP_CLIENT)
def test_fetcher_keeps_caller_owned_http_client_open_when_request_fails(
    fetcher, source
):
    client = MagicMock()
    client.get.side_effect = RuntimeError("request failed")

    with pytest.raises(RuntimeError, match="request failed"):
        list(fetcher(source, client=client))

    client.close.assert_not_called()


def _happy_response_for(fetcher):
    """Minimal successful response body each fetcher's happy path accepts."""
    if fetcher is fetch_rss:
        return _mock_httpx_response(_rss_feed_content([]))
    if fetcher is fetch_arxiv:
        return _mock_httpx_response(_arxiv_feed_content([]))
    if fetcher is fetch_hn:
        return _mock_httpx_response({"hits": []}, is_json=True)
    if fetcher is fetch_reddit:
        return _mock_httpx_response(_reddit_atom([]))
    raise AssertionError(f"no happy-path fixture for {fetcher!r}")


@pytest.mark.parametrize(("fetcher", "source"), FETCHERS_WITH_HTTP_CLIENT)
def test_fetcher_keeps_caller_owned_http_client_open_on_success(fetcher, source):
    """The success path's `if client is None: _client.close()` guard must
    also be exercised with client is NOT None (every existing success-path
    test for these four fetchers goes through `patch("httpx.Client")`,
    i.e. client=None internally — the caller-owned branch of that same
    line was only ever exercised via the request-failure test above)."""
    client = MagicMock()
    client.get.return_value = _happy_response_for(fetcher)

    items = list(fetcher(source, client=client))

    assert items == []
    client.close.assert_not_called()


# ---------------------------------------------------------------------------
# fetch_arxiv()
# ---------------------------------------------------------------------------

class TestFetchArxiv:
    def _source(self, query="cat:cs.AI", max_results=10):
        return {"id": "cs-ai", "query": query, "max_results": max_results}

    def test_yields_items_from_feed(self):
        content = _arxiv_feed_content([
            {"title": "LLM Agent Planning", "arxiv_id": "2401.00001",
             "summary": "We study agents.", "authors": ["Alice Smith", "Bob Jones"]},
        ])
        mock_resp = _mock_httpx_response(content)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_arxiv(self._source()))

        assert len(items) == 1
        assert "LLM Agent Planning" in items[0].title
        assert items[0].source == "arxiv:cs-ai"
        assert items[0].author == "Alice Smith, Bob Jones"
        assert items[0].raw_text == "We study agents."

    def test_title_newlines_stripped(self):
        content = _arxiv_feed_content([
            {"title": "Multi-line\nTitle\nHere", "arxiv_id": "2401.00002",
             "summary": "Abstract."},
        ])
        mock_resp = _mock_httpx_response(content)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_arxiv(self._source()))
        assert "\n" not in items[0].title

    def test_no_authors_returns_none(self):
        content = _arxiv_feed_content([
            {"title": "Anon Paper", "arxiv_id": "2401.00003",
             "summary": "text", "authors": []},
        ])
        mock_resp = _mock_httpx_response(content)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_arxiv(self._source()))
        assert items[0].author is None

    def test_summary_truncated_at_4000(self):
        long_summary = "word " * 1000  # > 4000 chars
        content = _arxiv_feed_content([
            {"title": "Paper", "arxiv_id": "2401.00004",
             "summary": long_summary},
        ])
        mock_resp = _mock_httpx_response(content)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_arxiv(self._source()))
        assert len(items[0].raw_text) <= 4000

    def test_empty_feed_yields_nothing(self):
        content = _arxiv_feed_content([])
        mock_resp = _mock_httpx_response(content)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_arxiv(self._source()))
        assert items == []

    def test_uses_correct_api_params(self):
        content = _arxiv_feed_content([])
        mock_resp = _mock_httpx_response(content)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            list(fetch_arxiv({"id": "x", "query": "cat:cs.AI", "max_results": 25}))
        call_kwargs = mock_ctx.get.call_args
        params = call_kwargs[1].get("params", {}) or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {}
        # The URL should be the arxiv export API
        called_url = mock_ctx.get.call_args[0][0]
        assert "arxiv.org" in called_url

    def test_entry_without_link_skipped(self):
        """Arxiv entries with no link are silently skipped (line 113)."""
        # feedparser uses <id> as link when <link> is absent in Atom feeds.
        # To produce an entry with entry.get("link") == None, we need an
        # entry with no <id> and no <link> — achieved by omitting both.
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query</title>
  <entry>
    <title>Paper Without Link Or Id</title>
    <summary>No link or id here.</summary>
    <author><name>Alice</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v1</id>
    <link href="http://arxiv.org/abs/2401.00002"/>
    <title>Paper With Link</title>
    <summary>Has a link.</summary>
    <author><name>Bob</name></author>
  </entry>
</feed>"""
        mock_resp = _mock_httpx_response(xml.encode())
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_arxiv({"id": "x", "query": "cat:cs.AI"}))
        # Only the entry with a link should be yielded
        assert len(items) == 1
        assert "With Link" in items[0].title


# ---------------------------------------------------------------------------
# fetch_hn()
# ---------------------------------------------------------------------------

class TestFetchHn:
    def _source(self, query="llm agents", hours_back=48, min_points=50):
        return {"id": "hn-agents", "query": query,
                "hours_back": hours_back, "min_points": min_points}

    def _hn_response(self, hits):
        return {"hits": hits, "nbHits": len(hits)}

    def test_yields_items_from_hits(self):
        hits = [{
            "objectID": "12345",
            "url": "https://example.com/post",
            "title": "LLM agents in production",
            "points": 150,
            "num_comments": 30,
            "author": "johndoe",
            "created_at": "2026-07-09T10:00:00.000Z",
            "story_text": "",
        }]
        mock_resp = _mock_httpx_response(self._hn_response(hits), is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_hn(self._source()))

        assert len(items) == 1
        assert items[0].title == "LLM agents in production"
        assert items[0].url == "https://example.com/post"
        assert items[0].source == "hn:hn-agents"
        assert items[0].author == "johndoe"

    def test_missing_url_falls_back_to_hn_item_link(self):
        """When hit has no url, use the HN comments page URL."""
        hits = [{
            "objectID": "99999",
            "url": None,
            "title": "Ask HN: about LLM agents",
            "points": 80,
            "num_comments": 15,
            "author": "alice",
            "created_at": "2026-07-09T10:00:00.000Z",
            "story_text": "Discussion text",
        }]
        mock_resp = _mock_httpx_response(self._hn_response(hits), is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_hn(self._source()))
        assert "news.ycombinator.com/item?id=99999" in items[0].url

    def test_missing_title_skipped(self):
        hits = [
            {"objectID": "1", "url": "https://a.com/1", "title": None,
             "points": 100, "num_comments": 5, "author": "x",
             "created_at": "2026-07-09T10:00:00.000Z", "story_text": ""},
            {"objectID": "2", "url": "https://a.com/2", "title": "Valid Title",
             "points": 80, "num_comments": 3, "author": "y",
             "created_at": "2026-07-09T10:00:00.000Z", "story_text": ""},
        ]
        mock_resp = _mock_httpx_response(self._hn_response(hits), is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_hn(self._source()))
        assert len(items) == 1
        assert items[0].title == "Valid Title"

    def test_raw_text_includes_points_and_comments(self):
        hits = [{
            "objectID": "1", "url": "https://a.com/1", "title": "T",
            "points": 200, "num_comments": 50, "author": "a",
            "created_at": "2026-07-09T10:00:00.000Z", "story_text": "",
        }]
        mock_resp = _mock_httpx_response(self._hn_response(hits), is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_hn(self._source()))
        assert "200 points" in items[0].raw_text
        assert "50 comments" in items[0].raw_text

    def test_empty_hits_yields_nothing(self):
        mock_resp = _mock_httpx_response({"hits": []}, is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_hn(self._source()))
        assert items == []

    @pytest.mark.parametrize("exclude, expected_tags", [
        (None, "story"),
        (False, "story"),
        (True, "story,-show_hn,-launch_hn"),
    ])
    def test_exclude_launches_negates_show_and_launch_tags(self, exclude, expected_tags):
        source = self._source()
        if exclude is not None:
            source["exclude_launches"] = exclude
        client = MagicMock()
        client.get.return_value = _mock_httpx_response({"hits": []}, is_json=True)
        list(fetch_hn(source, client=client))
        assert client.get.call_args.kwargs["params"]["tags"] == expected_tags


# ---------------------------------------------------------------------------
# fetch_reddit()
# ---------------------------------------------------------------------------

def _reddit_atom(entries: list[dict]) -> bytes:
    """Minimal Reddit-style Atom feed (summary is escaped HTML, as Reddit sends it)."""
    from xml.sax.saxutils import escape
    body = ""
    for e in entries:
        link_html = f'<a href="{e["href"]}">[link]</a>' if e.get("href") else ""
        summary = (
            f'<table><tr><td><div class="md"><p>{e.get("text", "")}</p></div>'
            f'&#32; submitted by &#32; <a href="https://www.reddit.com/user/{e.get("user", "u1")}"> '
            f'/u/{e.get("user", "u1")} </a> <br/> <span>{link_html}</span> '
            f'<span><a href="{e["permalink"]}">[comments]</a></span></td></tr></table>'
        )
        title = f"<title>{escape(e['title'])}</title>" if e.get("title") else ""
        author = f"<author><name>/u/{e['user']}</name></author>" if e.get("user") else ""
        body += f"""
        <entry>
          {author}
          <content type="html">{escape(summary)}</content>
          <id>t3_{e.get('id', 'x')}</id>
          <link href="{e['permalink']}" />
          <updated>2026-10-27T09:00:00+00:00</updated>
          <published>2026-10-27T08:00:00+00:00</published>
          {title}
        </entry>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>top</title>{body}</feed>""".encode("utf-8")


def _reddit_client(content: bytes):
    resp = MagicMock()
    resp.content = content
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get.return_value = resp
    return client


class TestFetchReddit:
    def _source(self, url="https://www.reddit.com/r/MachineLearning/top.json?t=day", **extra):
        return {"id": "ml-reddit", "url": url, **extra}

    def test_link_post_uses_external_url(self):
        client = _reddit_client(_reddit_atom([{
            "title": "New LLM agent paper", "user": "redditor1", "id": "a",
            "href": "https://example.com/paper?a=1&b=2",
            "permalink": "https://www.reddit.com/r/MachineLearning/comments/abc/x/",
            "text": "hello &amp; welcome",
        }]))
        items = list(fetch_reddit(self._source(), client=client))
        assert len(items) == 1
        it = items[0]
        assert it.title == "New LLM agent paper"
        assert it.url == "https://example.com/paper?a=1&b=2"
        assert it.source == "reddit:ml-reddit"
        assert it.author == "redditor1"
        assert it.published_at.startswith("2026-10-27T08:00:00")

    def test_self_post_uses_permalink(self):
        client = _reddit_client(_reddit_atom([{
            "title": "Discussion", "user": "z", "id": "b", "text": "some text",
            "permalink": "https://www.reddit.com/r/ML/comments/xyz/d/",
        }]))
        # no [link] anchor: the permalink is the entry link
        items = list(fetch_reddit(self._source(), client=client))
        assert items[0].url == "https://www.reddit.com/r/ML/comments/xyz/d/"

    def test_raw_text_is_stripped_and_prefixed(self):
        client = _reddit_client(_reddit_atom([{
            "title": "T", "user": "a", "id": "c", "href": "https://a.com/1",
            "text": "body text", "permalink": "https://www.reddit.com/r/ML/comments/1/t/",
        }]))
        raw = list(fetch_reddit(self._source(), client=client))[0].raw_text
        assert raw.startswith("r/MachineLearning: ")
        assert "body text" in raw
        assert "<" not in raw and "[link]" not in raw and "[comments]" not in raw
        assert "submitted by" not in raw

    def test_missing_author_is_none(self):
        client = _reddit_client(_reddit_atom([{
            "title": "T", "id": "d", "href": "https://a.com/1",
            "permalink": "https://www.reddit.com/r/ML/comments/1/t/",
        }]))
        assert list(fetch_reddit(self._source(), client=client))[0].author is None

    def test_missing_title_skipped(self):
        client = _reddit_client(_reddit_atom([{
            "id": "e", "href": "https://a.com/1", "user": "a",
            "permalink": "https://www.reddit.com/r/ML/comments/1/t/",
        }]))
        assert list(fetch_reddit(self._source(), client=client)) == []

    def test_empty_feed_yields_nothing(self):
        assert list(fetch_reddit(self._source(), client=_reddit_client(_reddit_atom([])))) == []

    def test_bozo_feed_without_entries_raises(self):
        with pytest.raises(RuntimeError, match="bozo"):
            list(fetch_reddit(self._source(), client=_reddit_client(b"<html><body>nope")))

    def test_http_error_propagates(self):
        client = _reddit_client(b"")
        client.get.return_value.raise_for_status.side_effect = RuntimeError("403")
        with pytest.raises(RuntimeError, match="403"):
            list(fetch_reddit(self._source(), client=client))

    def test_requests_rss_url_with_default_limit(self):
        client = _reddit_client(_reddit_atom([]))
        list(fetch_reddit(self._source(), client=client))
        client.get.assert_called_once_with(
            "https://www.reddit.com/r/MachineLearning/top/.rss?t=day&limit=10"
        )

    def test_limit_override_in_url_and_caps_entries(self):
        entries = [
            {"title": f"P{i}", "id": str(i), "href": f"https://a.com/{i}", "user": "u",
             "permalink": f"https://www.reddit.com/r/ML/comments/{i}/t/"}
            for i in range(5)
        ]
        client = _reddit_client(_reddit_atom(entries))
        items = list(fetch_reddit(self._source(limit=3), client=client))
        assert "limit=3" in client.get.call_args.args[0]
        assert [i.title for i in items] == ["P0", "P1", "P2"]

    def test_min_score_is_ignored(self):
        client = _reddit_client(_reddit_atom([{
            "title": "T", "id": "f", "href": "https://a.com/1", "user": "a",
            "permalink": "https://www.reddit.com/r/ML/comments/1/t/",
        }]))
        assert len(list(fetch_reddit(self._source(min_score=100000), client=client))) == 1


class TestRedditRssUrl:
    def test_rewrites_json_url_and_keeps_query(self):
        assert fetch._reddit_rss_url(
            "https://www.reddit.com/r/LocalLLaMA/top.json?t=day", 7
        ) == "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day&limit=7"

    def test_url_without_query(self):
        assert fetch._reddit_rss_url(
            "https://www.reddit.com/r/x/top.json", 5
        ) == "https://www.reddit.com/r/x/top/.rss?limit=5"

    def test_existing_rss_url_only_gets_limit(self):
        assert fetch._reddit_rss_url(
            "https://www.reddit.com/r/x/top/.rss?t=week", 4
        ) == "https://www.reddit.com/r/x/top/.rss?t=week&limit=4"

    def test_existing_limit_is_replaced(self):
        assert fetch._reddit_rss_url(
            "https://www.reddit.com/r/x/top.json?t=day&limit=99", 4
        ).endswith("limit=4")


class TestValidateRedditLimit:
    @pytest.mark.parametrize("value", [None])
    def test_default(self, value):
        assert fetch._validate_reddit_limit(value, "s") == fetch.REDDIT_DEFAULT_LIMIT

    def test_valid(self):
        assert fetch._validate_reddit_limit(3, "s") == 3

    @pytest.mark.parametrize("value", [0, -1, True, "5", 2.5])
    def test_invalid_falls_back(self, value, caplog):
        with caplog.at_level("WARNING", logger="fetch"):
            assert fetch._validate_reddit_limit(value, "s") == fetch.REDDIT_DEFAULT_LIMIT
        assert "invalid limit" in caplog.text


# ---------------------------------------------------------------------------
# fetch_github_releases()
# ---------------------------------------------------------------------------

class TestFetchGithubReleases:
    def _watchlist(self, owner="openai", repo="openai-python"):
        return [{"owner": owner, "repo": repo}]

    def test_yields_items_from_releases(self):
        releases = [{"html_url": "https://github.com/openai/openai-python/releases/v1.0",
                     "tag_name": "v1.0", "name": "v1.0",
                     "published_at": "2026-07-09T12:00:00Z",
                     "body": "Release notes here"}]
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(releases)

        with patch("subprocess.run", return_value=mock_result):
            items = list(fetch_github_releases(self._watchlist()))

        assert len(items) == 1
        assert items[0].source == "gh:openai/openai-python"
        assert items[0].url == "https://github.com/openai/openai-python/releases/v1.0"
        assert "v1.0" in items[0].title
        assert items[0].raw_text == "Release notes here"
        assert items[0].author == "openai"

    def test_release_missing_url_skipped(self):
        releases = [
            {"html_url": None, "tag_name": "v0.9", "body": ""},
            {"html_url": "https://github.com/x/y/releases/v1.0",
             "tag_name": "v1.0", "body": "notes"},
        ]
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(releases)
        with patch("subprocess.run", return_value=mock_result):
            items = list(fetch_github_releases(self._watchlist("x", "y")))
        assert len(items) == 1
        assert "v1.0" in items[0].url

    def test_subprocess_error_logs_warning_and_continues(self):
        """CalledProcessError → log warning, skip this repo."""
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "gh")):
            items = list(fetch_github_releases(self._watchlist()))
        assert items == []

    def test_subprocess_filenotfound_logs_warning(self):
        """FileNotFoundError (gh not installed) → log warning, skip."""
        with patch("subprocess.run", side_effect=FileNotFoundError("gh not found")):
            items = list(fetch_github_releases(self._watchlist()))
        assert items == []

    def test_subprocess_timeout_logs_warning(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 30)):
            items = list(fetch_github_releases(self._watchlist()))
        assert items == []

    def test_invalid_json_logs_warning_and_continues(self):
        mock_result = MagicMock()
        mock_result.stdout = "not valid json"
        with patch("subprocess.run", return_value=mock_result):
            items = list(fetch_github_releases(self._watchlist()))
        assert items == []

    def test_body_truncated_at_4000(self):
        long_body = "x" * 5000
        releases = [{"html_url": "https://github.com/a/b/releases/v1",
                     "tag_name": "v1", "body": long_body, "published_at": None}]
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(releases)
        with patch("subprocess.run", return_value=mock_result):
            items = list(fetch_github_releases(self._watchlist("a", "b")))
        assert len(items[0].raw_text) <= 4000

    def test_multiple_repos_in_watchlist(self):
        def mock_run(cmd, **kwargs):
            repo_part = cmd[2]
            owner = repo_part.split("/")[1]
            repo = repo_part.split("/")[2].split("?")[0]
            result = MagicMock()
            result.stdout = json.dumps([{
                "html_url": f"https://github.com/{owner}/{repo}/releases/v1",
                "tag_name": "v1", "body": "",
            }])
            return result

        watchlist = [
            {"owner": "openai", "repo": "openai-python"},
            {"owner": "anthropics", "repo": "anthropic-sdk-python"},
        ]
        with patch("subprocess.run", side_effect=mock_run):
            items = list(fetch_github_releases(watchlist))
        assert len(items) == 2
        sources = {it.source for it in items}
        assert "gh:openai/openai-python" in sources
        assert "gh:anthropics/anthropic-sdk-python" in sources


# ---------------------------------------------------------------------------
# main() pipeline
# ---------------------------------------------------------------------------

class TestFetchMain:
    @pytest.fixture()
    def db_path(self, tmp_path):
        import db as db_mod
        p = tmp_path / "state.db"
        db_mod.init_db(p)
        return p

    @pytest.fixture()
    def sources_path(self, tmp_path):
        p = tmp_path / "sources.yaml"
        p.write_text("rss: []\narxiv: []\nhn: []\nreddit: []\n")
        return p

    def _run_main(self, db_path, sources_path, monkeypatch):
        import fetch as fetch_mod
        import db as db_mod
        monkeypatch.setattr(fetch_mod, "SOURCES_PATH", sources_path)
        monkeypatch.setattr(fetch_mod, "connect", lambda: db_mod.connect(db_path))
        monkeypatch.setattr(fetch_mod, "init_db", lambda: db_mod.init_db(db_path))
        return fetch_mod.main()

    def test_returns_0_on_empty_sources(self, db_path, sources_path, monkeypatch):
        result = self._run_main(db_path, sources_path, monkeypatch)
        assert result == 0

    def test_skips_when_already_fetched_today(self, db_path, sources_path, monkeypatch):
        """When items exist with last_seen_date=today, skip and return 0."""
        import db as db_mod
        from datetime import date
        today = date.today().isoformat()
        conn = db_mod.connect(db_path)
        conn.execute(
            """INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
               status, first_seen_date, last_seen_date, appearances)
               VALUES ('x','rss:t','https://a.com','https://a.com','T',
               '2026-07-09T00:00:00Z','new','{}','{}',1)""".format(today, today)
        )
        db_mod.set_meta(conn, "fetch_completed_date", today)
        conn.commit()
        conn.close()

        result = self._run_main(db_path, sources_path, monkeypatch)
        assert result == 0

    def test_returns_0_with_successful_sources(self, db_path, sources_path, monkeypatch):
        import fetch as fetch_mod
        sources_path.write_text("""
rss:
  - id: test-blog
    url: https://example.com/feed.xml
""")
        # Mock fetch_rss to return a single item
        mock_item = Item(
            source="rss:test-blog",
            url="https://example.com/1",
            title="Test Post",
            author=None,
            published_at=None,
            raw_text="body",
        )
        monkeypatch.setattr(fetch_mod, "fetch_rss", lambda src, **kwargs: [mock_item])
        result = self._run_main(db_path, sources_path, monkeypatch)
        assert result == 0

    def test_per_source_error_does_not_abort_run(self, db_path, sources_path, monkeypatch):
        """Per-source errors are logged; run continues with other sources.

        In fetch.py, run_collector catches exceptions from list(gen). The mock
        must raise during ITERATION (not during call) so the exception is caught
        by run_collector's try/except. Use a generator function that raises.
        """
        import fetch as fetch_mod
        sources_path.write_text("""
rss:
  - id: bad-feed
    url: https://example.com/bad.xml
  - id: good-feed
    url: https://example.com/good.xml
""")
        call_count = [0]

        def mock_fetch_rss(src, **kwargs):
            call_count[0] += 1
            if src["id"] == "bad-feed":
                # Use a generator that raises on iteration (not on call)
                def _raising():
                    raise RuntimeError("network error")
                    yield  # pragma: no cover
                return _raising()
            return [Item("rss:good-feed", "https://good.com/1", "Good Post",
                         None, None, None)]

        monkeypatch.setattr(fetch_mod, "fetch_rss", mock_fetch_rss)
        result = self._run_main(db_path, sources_path, monkeypatch)
        # Run should succeed (total_seen > 0 from good source)
        assert result == 0
        assert call_count[0] == 2  # Both sources attempted

    def test_returns_1_when_all_sources_fail(self, db_path, sources_path, monkeypatch):
        """When ALL collectors fail and total_seen==0, main() returns 1."""
        import fetch as fetch_mod
        sources_path.write_text("""
rss:
  - id: bad-feed
    url: https://example.com/bad.xml
""")

        def _raising_rss(src, **kwargs):
            raise RuntimeError("all failed")
            yield  # pragma: no cover

        monkeypatch.setattr(fetch_mod, "fetch_rss", _raising_rss)
        result = self._run_main(db_path, sources_path, monkeypatch)
        assert result == 1

    def test_arxiv_sources_called_in_main(self, db_path, sources_path, monkeypatch):
        """main() calls fetch_arxiv for each arxiv source."""
        import fetch as fetch_mod
        sources_path.write_text("""
arxiv:
  - id: cs-ai
    query: cat:cs.AI
    max_results: 5
""")
        called = []
        def mock_fetch_arxiv(src, **kwargs):
            called.append(src["id"])
            return []  # no items
        monkeypatch.setattr(fetch_mod, "fetch_arxiv", mock_fetch_arxiv)
        with patch("time.sleep"):  # suppress inter-arxiv sleep
            result = self._run_main(db_path, sources_path, monkeypatch)
        assert result == 0
        assert "cs-ai" in called

    def test_hn_sources_called_in_main(self, db_path, sources_path, monkeypatch):
        import fetch as fetch_mod
        sources_path.write_text("""
hn:
  - id: hn-agents
    query: LLM agents
""")
        called = []
        def mock_fetch_hn(src, **kwargs):
            called.append(src["id"])
            return []
        monkeypatch.setattr(fetch_mod, "fetch_hn", mock_fetch_hn)
        result = self._run_main(db_path, sources_path, monkeypatch)
        assert result == 0
        assert "hn-agents" in called

    def test_reddit_sources_called_in_main(self, db_path, sources_path, monkeypatch):
        import fetch as fetch_mod
        sources_path.write_text("""
reddit:
  - id: ml-reddit
    url: https://reddit.com/r/MachineLearning/.json
    min_score: 100
""")
        called = []
        def mock_fetch_reddit(src, **kwargs):
            called.append(src["id"])
            return []
        monkeypatch.setattr(fetch_mod, "fetch_reddit", mock_fetch_reddit)
        result = self._run_main(db_path, sources_path, monkeypatch)
        assert result == 0
        assert "ml-reddit" in called

    def test_html_sources_called_with_shared_client_and_overrides(
        self, db_path, sources_path, monkeypatch
    ):
        import fetch as fetch_mod
        import db as db_mod

        sources_path.write_text("""
html:
  - id: vendor-news
    url: https://example.com/news
    link_selector: a.article
    section: blogs
    keyword_gate_bypass: true
    recency_days: 14
""")
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        monkeypatch.setattr(fetch_mod, "_make_http_client", lambda: client)
        calls = []

        def mock_fetch_html(source, **kwargs):
            calls.append((source, kwargs["client"]))
            return [Item(
                source="html:vendor-news",
                url="https://example.com/news/article",
                title="Vendor announcement",
                author=None,
                published_at=None,
                raw_text="Announcement body",
            )]

        monkeypatch.setattr(fetch_mod, "fetch_html", mock_fetch_html)

        assert self._run_main(db_path, sources_path, monkeypatch) == 0

        assert len(calls) == 1
        source, passed_client = calls[0]
        assert source["id"] == "vendor-news"
        assert passed_client is client
        conn = db_mod.connect(db_path)
        row = conn.execute(
            "SELECT section_override, keyword_gate_bypass, recency_days_override "
            "FROM items WHERE url = ?",
            ("https://example.com/news/article",),
        ).fetchone()
        conn.close()
        assert tuple(row) == ("blogs", 1, 14)

    def test_github_releases_called_in_main(self, db_path, sources_path, monkeypatch):
        import fetch as fetch_mod
        sources_path.write_text("""
github_releases:
  - owner: openai
    repo: openai-python
""")
        called = []
        def mock_fetch_github(watchlist):
            called.extend(f"{e['owner']}/{e['repo']}" for e in watchlist)
            return []
        monkeypatch.setattr(fetch_mod, "fetch_github_releases", mock_fetch_github)
        result = self._run_main(db_path, sources_path, monkeypatch)
        assert result == 0
        assert "openai/openai-python" in called

    def test_reddit_sleeps_between_subreddits(self, db_path, sources_path, monkeypatch):
        """main() pauses between reddit sources so Reddit doesn't 429 us."""
        import fetch as fetch_mod
        sources_path.write_text("""
reddit:
  - id: r-a
    url: https://www.reddit.com/r/a/top.json?t=day
  - id: r-b
    url: https://www.reddit.com/r/b/top.json?t=day
  - id: r-c
    url: https://www.reddit.com/r/c/top.json?t=day
""")
        monkeypatch.setattr(fetch_mod, "fetch_reddit", lambda src, **kwargs: [])
        sleep_calls = []
        with patch("time.sleep", side_effect=sleep_calls.append):
            self._run_main(db_path, sources_path, monkeypatch)
        assert sleep_calls == [fetch_mod.REDDIT_REQUEST_DELAY] * 2

    def test_arxiv_sleep_called_between_multiple_sources(self, db_path, sources_path, monkeypatch):
        """main() sleeps between arxiv sources (rate-limiting)."""
        import fetch as fetch_mod
        sources_path.write_text("""
arxiv:
  - id: cs-ai
    query: cat:cs.AI
  - id: cs-lg
    query: cat:cs.LG
""")
        monkeypatch.setattr(fetch_mod, "fetch_arxiv", lambda src, **kwargs: [])
        sleep_calls = []
        with patch("time.sleep", side_effect=sleep_calls.append):
            self._run_main(db_path, sources_path, monkeypatch)
        # Should have slept once between the two arxiv sources
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 3.0
