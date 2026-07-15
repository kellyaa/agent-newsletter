import hashlib

import pytest

from db import canonicalize_url, url_id


def test_canonicalize_url_normalizes_host_only_url() -> None:
    assert canonicalize_url("HTTP://Example.COM") == "http://example.com/"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://arxiv.org/pdf/2401.12345.pdf?utm_source=newsletter",
            "https://arxiv.org/abs/2401.12345",
        ),
        (
            "https://arxiv.org/abs/2401.12345v2",
            "https://arxiv.org/abs/2401.12345",
        ),
        (
            "https://arxiv.org/html/2401.12345v3/",
            "https://arxiv.org/abs/2401.12345",
        ),
    ],
)
def test_canonicalize_url_collapses_arxiv_variants(
    url: str, expected: str
) -> None:
    assert canonicalize_url(url) == expected


def test_canonicalize_url_strips_tracking_params_and_preserves_query_order() -> None:
    url = (
        "HTTPS://Example.COM/path/"
        "?b=2&utm_source=feed&a=1&ref_src=hn&fbclid=abc&source=rss"
    )

    assert canonicalize_url(url) == "https://example.com/path?b=2&a=1"


@pytest.mark.parametrize(
    "bad_url",
    [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "ftp://example.com/foo",
    ],
)
def test_canonicalize_url_rejects_non_http_schemes(bad_url: str) -> None:
    """Non-http(s) URLs from Reddit/RSS/HTML sources must not reach persistence.

    They would land in <a href> on the site and in the RSS feed unchanged
    (see FeaturedItem.astro:46, rss.xml.js renderItemContent) — an XSS
    surface via `url_overridden_by_dest` and untrusted RSS <link>.
    """
    with pytest.raises(ValueError, match="unsupported URL scheme"):
        canonicalize_url(bad_url)


def test_url_id_is_stable_for_same_canonical_url() -> None:
    first = "https://example.com/items/42?utm_source=feed&b=2"
    second = "HTTPS://EXAMPLE.COM/items/42/?b=2&fbclid=abc"
    canonical = "https://example.com/items/42?b=2"

    assert url_id(first) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert url_id(first) == url_id(second)


def test_url_id_differs_across_canonical_inputs() -> None:
    assert url_id("https://example.com/items/42") != url_id(
        "https://example.com/items/43"
    )


def test_db_path_uses_content_root_env(monkeypatch, tmp_path):
    """DB_PATH resolves under CONTENT_ROOT when the env var is set."""
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    import importlib
    import db
    importlib.reload(db)
    assert db.DB_PATH == tmp_path / "state.db"


def test_db_path_defaults_to_repo_root(monkeypatch):
    """DB_PATH falls back to REPO_ROOT when CONTENT_ROOT is unset."""
    monkeypatch.delenv("CONTENT_ROOT", raising=False)
    import importlib
    import db
    importlib.reload(db)
    assert db.DB_PATH == db.REPO_ROOT / "state.db"
