"""End-to-end test: run ``scripts/check_edition.py`` as a real subprocess.

Unlike the unit tests, nothing is imported or monkeypatched. The CLI is
launched the way a contributor launches it (``python scripts/check_edition.py
<slug>``) against a throwaway copy of the pipeline, and its only network
dependency is an RSS feed served from a local HTTP server. That exercises
edition lint, prompt rendering, the real HTTP fetch, feed parsing, the SQLite
upsert and the recency and keyword gates together, with no LLM and no internet.
"""
from __future__ import annotations

import http.server
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SLUG = "e2e-demo"


def _feed() -> bytes:
    now = datetime.now(timezone.utc)

    def item(title: str, link: str, age_days: int) -> str:
        pub = format_datetime(now - timedelta(days=age_days))
        return (
            f"<item><title>{title}</title><link>{link}</link>"
            f"<description>{title}</description><pubDate>{pub}</pubDate></item>"
        )

    items = [
        item("New agent framework ships tool calling", "http://127.0.0.1/a", 0),
        item("Another autonomous agent benchmark", "http://127.0.0.1/b", 0),
        item("Gardening tips for spring", "http://127.0.0.1/c", 0),
        item("Ancient agent post from long ago", "http://127.0.0.1/d", 400),
    ]
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        f"<link>http://127.0.0.1/</link><description>d</description>{''.join(items)}"
        "</channel></rss>"
    ).encode()


@pytest.fixture
def feed_url():
    body = _feed()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/feed.xml"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def sandbox(tmp_path, feed_url):
    """A minimal copy of the repo whose only edition reads the local feed."""
    root = tmp_path / "repo"
    for name in ("scripts", "prompts"):
        shutil.copytree(
            REPO_ROOT / name, root / name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    edition_dir = root / "editions" / SLUG
    shutil.copytree(REPO_ROOT / "editions" / "agents", edition_dir)
    cfg_path = edition_dir / "edition.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["slug"] = SLUG
    cfg["keywords"] = [r"\bagents?\b"]
    cfg_path.write_text(yaml.safe_dump(cfg))
    (edition_dir / "sources.yaml").write_text(
        yaml.safe_dump({"rss": [{"id": "local", "name": "Local", "url": feed_url}]})
    )
    return root


def _run(root: Path, slug: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "EDITION"}
    # Keep the loopback feed off any proxy configured in the developer's shell.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(var, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "check_edition.py"), slug],
        cwd=root, env=env, capture_output=True, text=True, timeout=120,
    )


def test_check_edition_cli_lints_fetches_and_gates(sandbox):
    result = _run(sandbox, SLUG)

    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert f"== lint: editions/{SLUG}" in out
    assert "lint ok" in out
    assert "== dry run" in out
    row = next(line for line in out.splitlines() if line.startswith("rss/local"))
    # fetched -> recency -> keyword gate: 4 items, the 400-day-old one ages
    # out, the gardening post fails the keyword gate, two survive.
    assert row.split()[1:4] == ["4", "3", "2"], row
    assert "blogs:2" in row
    total = next(line for line in out.splitlines() if line.startswith("total"))
    assert total.split()[1:4] == ["4", "3", "2"], total
    # The dry run never touches the real content or logs tree.
    assert not (sandbox / "editions" / SLUG / "state.db").exists()


def test_check_edition_cli_fails_on_invalid_edition(sandbox):
    (sandbox / "editions" / SLUG / "sources.yaml").write_text(
        yaml.safe_dump({"rss": [{"id": "broken"}]})
    )

    result = _run(sandbox, SLUG)

    assert result.returncode == 1
    assert "ERROR" in result.stdout
    assert "missing ['url']" in result.stdout
    assert "lint FAILED; skipping the dry run" in result.stdout
    assert "== dry run" not in result.stdout


def test_check_edition_cli_rejects_unknown_slug(sandbox):
    result = _run(sandbox, "no-such-edition")

    assert result.returncode == 1
    assert "ERROR" in result.stdout
