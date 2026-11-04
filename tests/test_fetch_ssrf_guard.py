"""SSRF guard on the shared fetch client: non-public targets are refused."""
from __future__ import annotations

import socket

import httpx
import pytest

import fetch


def _client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks={"request": [fetch._reject_non_public_target]},
    )


def _addrinfo(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def test_make_http_client_installs_guard():
    with fetch._make_http_client() as client:
        assert client.event_hooks["request"] == [fetch._reject_non_public_target]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/feed",
    "http://10.0.0.5/feed",
    "http://192.168.1.1/feed",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/feed",
])
def test_literal_non_public_ip_refused(url):
    calls = []
    with _client(lambda r: calls.append(r) or httpx.Response(200)) as client:
        with pytest.raises(httpx.RequestError, match="non-public address"):
            client.get(url)
    assert calls == []


def test_hostname_resolving_to_private_ip_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("10.1.2.3"))
    with _client(lambda r: httpx.Response(200)) as client:
        with pytest.raises(httpx.RequestError, match="10.1.2.3"):
            client.get("https://internal.example/feed")


def test_public_hostname_allowed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))
    with _client(lambda r: httpx.Response(200, text="ok")) as client:
        assert client.get("https://example.com/feed").text == "ok"


def test_unresolvable_hostname_not_blocked_by_guard(monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror("nope")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with _client(lambda r: httpx.Response(200, text="ok")) as client:
        assert client.get("https://nx.example/feed").text == "ok"


def test_redirect_to_metadata_endpoint_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})

    with _client(handler) as client:
        with pytest.raises(httpx.RequestError, match="169.254.169.254"):
            client.get("https://example.com/feed")
    assert seen == ["https://example.com/feed"]


def test_allow_private_hosts_env_disables_guard(monkeypatch):
    monkeypatch.setenv("FETCH_ALLOW_PRIVATE_HOSTS", "1")
    with _client(lambda r: httpx.Response(200, text="ok")) as client:
        assert client.get("http://127.0.0.1/feed").text == "ok"
