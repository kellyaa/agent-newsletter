"""Every script that makes outbound HTTP requests uses net.make_http_client.

new_edition.py used to build its own httpx.Client with no SSRF guard and the
default TLS context, so `--seed-source` URLs (user input) could hit loopback,
RFC-1918 or cloud-metadata addresses that fetch.py refuses (#505), and probes
went out with ALPN, which the egress proxy rejects. The client now lives in
net.py; this test keeps it the only place an httpx.Client is constructed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import fetch
import net
import new_edition

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _client_constructions(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"Client", "AsyncClient"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "httpx"
    )


def test_only_net_constructs_httpx_clients():
    offenders = {
        p.name: n
        for p in sorted(SCRIPTS.glob("*.py"))
        if p.name != "net.py" and (n := _client_constructions(p))
    }
    assert not offenders, f"build HTTP clients with net.make_http_client(): {offenders}"
    assert _client_constructions(SCRIPTS / "net.py") == 1


def test_detector_sees_client_calls(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("import httpx\nhttpx.Client()\nhttpx.AsyncClient(timeout=1)\nother.Client()\n")
    assert _client_constructions(p) == 2


def test_new_edition_client_has_ssrf_guard_and_alpn_free_tls():
    with new_edition._client() as client:
        assert client.event_hooks["request"] == [net.reject_non_public_target]
        assert client.headers["User-Agent"] == net.USER_AGENT
        assert client.timeout.read == net.HTTP_TIMEOUT
        assert client.follow_redirects is True


def test_fetch_keeps_its_patchable_client_names():
    # tests and check_edition patch/call these on the fetch module
    assert fetch._make_http_client is net.make_http_client
    assert fetch._reject_non_public_target is net.reject_non_public_target
    assert fetch._make_ssl_context is net.make_ssl_context
    assert fetch._NoALPNContext is net.NoALPNContext
