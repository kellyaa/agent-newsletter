"""Edge cases of the URL policy in ``scripts/urls.py`` that other suites leave unpinned.

The dedup key (``url_id``) is the items primary key, so any change to what
``canonicalize_url`` keeps or drops silently re-ingests or merges items.
"""
import hashlib

import pytest

import urls


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Fragments never reach the server and must not split a dedup key.
        ("https://example.com/p#section-2", "https://example.com/p"),
        ("https://example.com/p?a=1#top", "https://example.com/p?a=1"),
        # Surrounding whitespace is ignored.
        ("  https://example.com/p \n", "https://example.com/p"),
        # Tracking-param matching is case-insensitive on the key, prefix too.
        ("https://example.com/p?UTM_Source=x&Ref=y&GCLID=z&k=v", "https://example.com/p?k=v"),
        ("https://example.com/p?mc_cid=1&mc_eid=2&k=v", "https://example.com/p?k=v"),
        # Only the exact names / prefixes are tracking params.
        ("https://example.com/p?reference=1&sourcecode=2", "https://example.com/p?reference=1&sourcecode=2"),
        # Blank values are dropped from the query.
        ("https://example.com/p?empty=&k=v", "https://example.com/p?k=v"),
        # Repeated keys are preserved in order.
        ("https://example.com/p?a=1&a=2", "https://example.com/p?a=1&a=2"),
        # A query that is entirely tracking leaves no trailing '?'.
        ("https://example.com/p?utm_source=x&source=y", "https://example.com/p"),
        # Query values are re-encoded consistently.
        ("https://example.com/?q=a%20b", "https://example.com/?q=a+b"),
        ("https://example.com/?q=a+b", "https://example.com/?q=a+b"),
        # Userinfo and IPv6 hosts survive the default-port strip.
        ("https://user@example.com:443/p", "https://user@example.com/p"),
        ("https://[::1]:443/x", "https://[::1]/x"),
        # Path case is significant; host case is not.
        ("https://EXAMPLE.com/CaseSensitive", "https://example.com/CaseSensitive"),
        # Only one trailing slash run is collapsed and the root is kept.
        ("https://example.com/a///", "https://example.com/a"),
    ],
)
def test_canonicalize_url_edges(url, expected):
    assert urls.canonicalize_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/abs/2401.12345v2",
        "https://arxiv.org/pdf/2401.12345.pdf",
        "https://arxiv.org/html/2401.12345v3/",
        "https://arxiv.org/abs/2401.12345?utm_source=x#frag",
    ],
)
def test_arxiv_variants_share_one_item_id(url):
    assert urls.url_id(url) == urls.url_id("https://arxiv.org/abs/2401.12345")


@pytest.mark.parametrize(
    "path",
    [
        "/abs/2401.1234",       # too few digits
        "/abs/2401.123456",     # too many digits
        "/abs/hep-th/9901001",  # old-style ids are not rewritten
        "/list/cs.AI/recent",
    ],
)
def test_arxiv_paths_outside_the_id_pattern_are_left_alone(path):
    assert urls.canonicalize_url(f"https://arxiv.org{path}") == f"https://arxiv.org{path}"


def test_arxiv_five_digit_id_with_version_and_pdf_suffix():
    assert (
        urls.canonicalize_url("https://arxiv.org/pdf/2401.12345v10.pdf")
        == "https://arxiv.org/abs/2401.12345"
    )


def test_canonicalize_is_idempotent():
    url = "HTTPS://Example.COM:443/a//?b=2&utm_x=1&a=1&a=2#frag"
    once = urls.canonicalize_url(url)
    assert urls.canonicalize_url(once) == once


def test_url_id_is_sha256_hex_of_canonical_form():
    got = urls.url_id("https://example.com/x?utm_source=a")
    assert got == hashlib.sha256(b"https://example.com/x").hexdigest()
    assert len(got) == 64


@pytest.mark.parametrize(
    "url",
    ["HTTP://example.com", " https://example.com ", "Https://x.test/a?b=c"],
)
def test_is_allowed_url_accepts_case_and_whitespace(url):
    assert urls.is_allowed_url(url) is True


@pytest.mark.parametrize(
    "url",
    ["", "   ", "//example.com/x", "example.com/x", "ftp://example.com", "http://[bad", "mailto:a@b.c"],
)
def test_is_allowed_url_rejects_non_http_inputs(url):
    assert urls.is_allowed_url(url) is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Subreddit root (no /top) still gets the feed suffix.
        ("https://www.reddit.com/r/a/", "https://www.reddit.com/r/a/.rss?limit=5"),
        ("https://www.reddit.com/r/a", "https://www.reddit.com/r/a/.rss?limit=5"),
        # Other query params are kept, in order; limit is replaced in place.
        (
            "https://www.reddit.com/r/a/top.json?limit=99&t=week",
            "https://www.reddit.com/r/a/top/.rss?limit=5&t=week",
        ),
        # Host and scheme pass through untouched.
        ("http://old.reddit.com/r/a/top.json", "http://old.reddit.com/r/a/top/.rss?limit=5"),
    ],
)
def test_reddit_rss_url_edges(url, expected):
    assert urls.reddit_rss_url(url, 5) == expected


def test_reddit_rss_url_is_idempotent():
    once = urls.reddit_rss_url("https://www.reddit.com/r/a/top.json?t=day", 3)
    assert urls.reddit_rss_url(once, 3) == once
