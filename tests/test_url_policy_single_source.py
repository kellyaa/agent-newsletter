"""URL policy has one home: scripts/urls.py.

new_edition.py used to re-derive the reddit Atom URL itself and drifted from
fetch.py (it appended ``limit=1`` to a URL that already had ``limit=``, so its
live check probed a different URL than the pipeline reads), and it compared
``urlparse(...).scheme`` against an inline tuple instead of using the ingest
allowlist. These tests pin the shared helpers and fail if a script grows its
own copy again.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import db
import fetch
import new_edition
import urls

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _tree(name: str) -> ast.Module:
    return ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))


def test_db_reexports_are_the_urls_objects():
    for name in ("canonicalize_url", "url_id", "is_allowed_url",
                 "ALLOWED_URL_SCHEMES", "TRACKING_PARAMS", "TRACKING_PARAM_PREFIXES"):
        assert getattr(db, name) is getattr(urls, name), name


def test_fetch_reddit_url_is_the_shared_helper():
    assert fetch._reddit_rss_url is urls.reddit_rss_url


def test_url_helpers_defined_only_in_urls():
    owned = {"canonicalize_url", "url_id", "is_allowed_url", "reddit_rss_url", "_reddit_rss_url"}
    offenders = []
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name == "urls.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in owned:
                offenders.append(f"{path.name}:{node.lineno} def {node.name}")
    assert not offenders, f"URL helpers re-implemented outside urls.py: {offenders}"


def _scheme_compares(tree: ast.Module) -> list[int]:
    """Line numbers of ``<x>.scheme in/not in (...)`` / ``== "http"`` checks."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute) \
                and node.left.attr == "scheme":
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize("script", sorted(p.name for p in SCRIPTS.glob("*.py") if p.name != "urls.py"))
def test_no_inline_scheme_allowlist(script):
    assert not _scheme_compares(_tree(script)), (
        f"{script} compares a URL scheme inline; use urls.is_allowed_url"
    )


def test_scheme_detector_sees_inline_check():
    tree = ast.parse('if urlparse(u).scheme not in ("http", "https"):\n    pass\n')
    assert _scheme_compares(tree) == [1]


def test_new_edition_does_not_rebuild_reddit_feed_url():
    """No ``.rss`` literal in new_edition: the feed path comes from urls."""
    for node in ast.walk(_tree("new_edition.py")):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "/.rss" not in node.value, node.lineno


class _Recorder:
    def __init__(self):
        self.urls: list[str] = []

    def get(self, url, **_):
        self.urls.append(url)
        raise new_edition.httpx.ConnectError("offline")


@pytest.mark.parametrize("src", [
    "https://www.reddit.com/r/x/top.json?t=day",
    "https://www.reddit.com/r/x/top.json?t=day&limit=99",
    "https://www.reddit.com/r/x/top/.rss?t=week",
    "https://www.reddit.com/r/x/top.json",
])
def test_check_subreddit_probes_the_url_fetch_reads(src):
    rec = _Recorder()
    new_edition.check_subreddit(rec, src)
    assert rec.urls == [fetch._reddit_rss_url(src, 1)]
    assert rec.urls[0].count("limit=") == 1


@pytest.mark.parametrize("url", ["HTTP://Example.com/feed", " https://example.com/feed"])
def test_new_edition_scheme_check_matches_ingest(url):
    """A URL fetch would accept is not rejected by new_edition as non-http(s)."""
    assert db.is_allowed_url(url)
    rec = _Recorder()
    assert new_edition.check_rss(rec, url) != "not an http(s) URL"


def test_new_edition_rejects_what_ingest_rejects():
    rec = _Recorder()
    assert new_edition.check_rss(rec, "javascript:alert(1)") == "not an http(s) URL"
    assert rec.urls == []


def test_reddit_rss_url_replaces_existing_limit():
    assert urls.reddit_rss_url("https://www.reddit.com/r/x/top.json?t=day&limit=99", 1) == (
        "https://www.reddit.com/r/x/top/.rss?t=day&limit=1"
    )
