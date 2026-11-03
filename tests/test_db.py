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


def test_canonicalize_url_collapses_arxiv_subdomain_variants() -> None:
    assert (
        canonicalize_url("https://export.arxiv.org/pdf/2401.12345v2")
        == "https://export.arxiv.org/abs/2401.12345"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://notarxiv.org/pdf/2401.12345.pdf",
        "https://fakearxiv.org/abs/2401.12345v2",
        "https://arxiv.org.evil.example/pdf/2401.12345",
    ],
)
def test_canonicalize_url_leaves_lookalike_arxiv_hosts_alone(url: str) -> None:
    """Only arxiv.org and its subdomains get the abs-form rewrite; a host that
    merely ends in the same letters must keep its own path, or distinct pages
    on it would collapse into one dedup key."""
    assert canonicalize_url(url) == url


def test_canonicalize_url_keeps_distinct_pages_on_lookalike_host_distinct() -> None:
    assert canonicalize_url("https://notarxiv.org/pdf/2401.12345") != canonicalize_url(
        "https://notarxiv.org/abs/2401.12345"
    )


def test_canonicalize_url_strips_tracking_params_and_preserves_query_order() -> None:
    url = (
        "HTTPS://Example.COM/path/"
        "?b=2&utm_source=feed&a=1&ref_src=hn&fbclid=abc&source=rss"
    )

    assert canonicalize_url(url) == "https://example.com/path?b=2&a=1"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://Example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        # A port that is not the scheme's default is significant.
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        ("http://example.com:443/a", "http://example.com:443/a"),
        ("https://example.com:80/a", "https://example.com:80/a"),
    ],
)
def test_canonicalize_url_drops_only_the_default_port(url: str, expected: str) -> None:
    assert canonicalize_url(url) == expected


def test_canonicalize_url_collapses_arxiv_variant_with_default_port() -> None:
    assert (
        canonicalize_url("https://arxiv.org:443/pdf/2401.12345v2")
        == "https://arxiv.org/abs/2401.12345"
    )


def test_canonicalize_url_slash_only_path_stays_root() -> None:
    assert canonicalize_url("https://example.com//") == "https://example.com/"
    assert canonicalize_url("https://example.com//") == canonicalize_url(
        "https://example.com/"
    )


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


def test_run_date_reads_run_date_env(monkeypatch):
    from db import run_date

    monkeypatch.setenv("RUN_DATE", "2026-10-24")
    assert run_date() == "2026-10-24"


def test_run_date_defaults_to_local_today(monkeypatch):
    from datetime import date

    from db import run_date

    monkeypatch.delenv("RUN_DATE", raising=False)
    assert run_date() == date.today().isoformat()


def test_db_path_resolves_at_call_time(monkeypatch, tmp_path):
    """db_path() follows the current env; no module reload is needed."""
    import db
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("EDITION", "agents")
    assert db.db_path() == tmp_path / "editions" / "agents" / "state.db"
    monkeypatch.setenv("EDITION", "engineering-with-ai")
    assert db.db_path() == (
        tmp_path / "editions" / "engineering-with-ai" / "state.db"
    )
    monkeypatch.delenv("CONTENT_ROOT")
    assert db.db_path() == db.REPO_ROOT / "editions" / "engineering-with-ai" / "state.db"


def test_connect_and_init_db_default_to_db_path(monkeypatch, tmp_path):
    import db
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("EDITION", "agents")
    db.init_db()
    assert (tmp_path / "editions" / "agents" / "state.db").exists()
    conn = db.connect()
    conn.close()


def test_db_path_alias_is_lazy_and_unknown_attrs_raise(monkeypatch, tmp_path):
    import db
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("EDITION", "agents")
    assert db.DB_PATH == db.db_path()
    with pytest.raises(AttributeError):
        db.NOPE


def test_import_db_without_edition_selected():
    """`import db` must not need EDITION (issue #446)."""
    import subprocess, sys
    from pathlib import Path
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    env = {k: v for k, v in __import__("os").environ.items() if k != "EDITION"}
    r = subprocess.run(
        [sys.executable, "-c", "import db; print(db.run_date())"],
        cwd=scripts, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
