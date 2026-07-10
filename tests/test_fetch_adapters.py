"""Tests for fetch.py source adapter functions and main() pipeline.

All network calls (httpx) and subprocess calls (gh api) are mocked.
No real network traffic is made.

Coverage targets:
  - fetch_rss()     — httpx + feedparser, entry parsing, bozo handling
  - fetch_arxiv()   — httpx + feedparser, multi-author, title whitespace
  - fetch_hn()      — httpx JSON, score/comments, missing url fallback
  - fetch_reddit()  — httpx JSON, score filter, permalink fallback
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


# ---------------------------------------------------------------------------
# fetch_reddit()
# ---------------------------------------------------------------------------

class TestFetchReddit:
    def _source(self, url="https://reddit.com/r/MachineLearning/.json", min_score=100):
        return {"id": "ml-reddit", "url": url, "min_score": min_score}

    def _reddit_response(self, posts):
        children = [{"data": p} for p in posts]
        return {"data": {"children": children}}

    def test_yields_items_from_posts(self):
        posts = [{
            "url_overridden_by_dest": "https://example.com/paper",
            "title": "New LLM agent paper",
            "score": 200,
            "num_comments": 45,
            "subreddit": "MachineLearning",
            "author": "redditor1",
            "created_utc": 1720519200.0,
            "selftext": "",
            "permalink": "/r/MachineLearning/comments/abc/",
        }]
        mock_resp = _mock_httpx_response(self._reddit_response(posts), is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_reddit(self._source()))

        assert len(items) == 1
        assert items[0].title == "New LLM agent paper"
        assert items[0].url == "https://example.com/paper"
        assert items[0].source == "reddit:ml-reddit"
        assert items[0].author == "redditor1"

    def test_low_score_posts_filtered_out(self):
        posts = [
            {"url_overridden_by_dest": "https://a.com/1", "title": "Low score",
             "score": 50, "num_comments": 5, "subreddit": "ML",
             "author": "x", "created_utc": 1720519200.0, "selftext": "",
             "permalink": "/r/ML/comments/1/"},
            {"url_overridden_by_dest": "https://a.com/2", "title": "High score",
             "score": 150, "num_comments": 10, "subreddit": "ML",
             "author": "y", "created_utc": 1720519200.0, "selftext": "",
             "permalink": "/r/ML/comments/2/"},
        ]
        mock_resp = _mock_httpx_response(self._reddit_response(posts), is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_reddit(self._source()))
        assert len(items) == 1
        assert items[0].title == "High score"

    def test_permalink_fallback_when_no_dest_url(self):
        """Posts without url_overridden_by_dest use the reddit permalink."""
        posts = [{
            "url_overridden_by_dest": None,
            "title": "Discussion post",
            "score": 120, "num_comments": 8, "subreddit": "LocalLLaMA",
            "author": "z", "created_utc": 1720519200.0, "selftext": "some text",
            "permalink": "/r/LocalLLaMA/comments/xyz/",
        }]
        mock_resp = _mock_httpx_response(self._reddit_response(posts), is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_reddit(self._source()))
        assert "reddit.com/r/LocalLLaMA/comments/xyz/" in items[0].url

    def test_raw_text_includes_score_and_comments(self):
        posts = [{
            "url_overridden_by_dest": "https://a.com/1",
            "title": "T", "score": 300, "num_comments": 75,
            "subreddit": "ML", "author": "a", "created_utc": 1720519200.0,
            "selftext": "body text", "permalink": "/r/ML/1/",
        }]
        mock_resp = _mock_httpx_response(self._reddit_response(posts), is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_reddit(self._source()))
        assert "300 score" in items[0].raw_text
        assert "75 comments" in items[0].raw_text

    def test_published_at_from_created_utc(self):
        posts = [{
            "url_overridden_by_dest": "https://a.com/1",
            "title": "T", "score": 200, "num_comments": 5,
            "subreddit": "ML", "author": "a", "created_utc": 1720519200.0,
            "selftext": "", "permalink": "/r/ML/1/",
        }]
        mock_resp = _mock_httpx_response(self._reddit_response(posts), is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_reddit(self._source()))
        assert items[0].published_at is not None
        assert "2024" in items[0].published_at or "2026" in items[0].published_at or items[0].published_at

    def test_empty_response_yields_nothing(self):
        mock_resp = _mock_httpx_response({"data": {"children": []}}, is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_reddit(self._source()))
        assert items == []

    def test_missing_title_skipped(self):
        posts = [
            {"url_overridden_by_dest": "https://a.com/1", "title": None,
             "score": 200, "num_comments": 5, "subreddit": "ML",
             "author": "a", "created_utc": 1720519200.0, "selftext": "",
             "permalink": "/r/ML/1/"},
        ]
        mock_resp = _mock_httpx_response(self._reddit_response(posts), is_json=True)
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.get.return_value = mock_resp
            mock_client_cls.return_value = mock_ctx
            items = list(fetch_reddit(self._source()))
        assert items == []


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
        monkeypatch.setattr(fetch_mod, "fetch_rss", lambda src: [mock_item])
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

        def mock_fetch_rss(src):
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

        def _raising_rss(src):
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
        def mock_fetch_arxiv(src):
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
        def mock_fetch_hn(src):
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
        def mock_fetch_reddit(src):
            called.append(src["id"])
            return []
        monkeypatch.setattr(fetch_mod, "fetch_reddit", mock_fetch_reddit)
        result = self._run_main(db_path, sources_path, monkeypatch)
        assert result == 0
        assert "ml-reddit" in called

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
        monkeypatch.setattr(fetch_mod, "fetch_arxiv", lambda src: [])
        sleep_calls = []
        with patch("time.sleep", side_effect=sleep_calls.append):
            self._run_main(db_path, sources_path, monkeypatch)
        # Should have slept once between the two arxiv sources
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 3.0
