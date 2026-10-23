import hashlib

import pytest

from db import canonicalize_url, is_allowed_url, url_id


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
    assert db.DB_PATH == tmp_path / "editions" / "agents" / "state.db"


def test_db_path_defaults_to_repo_root(monkeypatch):
    """DB_PATH falls back to REPO_ROOT when CONTENT_ROOT is unset."""
    monkeypatch.delenv("CONTENT_ROOT", raising=False)
    import importlib
    import db
    importlib.reload(db)
    assert db.DB_PATH == db.REPO_ROOT / "editions" / "agents" / "state.db"


# ---------------------------------------------------------------------------
# is_allowed_url — scheme allowlist (issue #248)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article",
        "http://example.com/article",
        "HTTPS://Example.COM/Article",
        "  https://example.com/padded  ",
    ],
)
def test_is_allowed_url_accepts_http_and_https(url: str) -> None:
    assert is_allowed_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
        "ftp://example.com/file",
        "",
        "not a url at all",
    ],
)
def test_is_allowed_url_rejects_non_http_schemes(url: str) -> None:
    assert is_allowed_url(url) is False


def test_is_allowed_url_rejects_non_string_input() -> None:
    assert is_allowed_url(None) is False  # type: ignore[arg-type]
