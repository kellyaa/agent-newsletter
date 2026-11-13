"""Edge cases of net.reject_non_public_target not pinned by test_fetch_ssrf_guard.

The guard is the SSRF defence for every user-configured URL, so the
mutation-prone corners are asserted directly: multi-record DNS answers,
scoped IPv6 addresses in getaddrinfo results, and the exact opt-out value.
"""
from __future__ import annotations

import socket

import httpx
import pytest

import net


def _req(url: str) -> httpx.Request:
    return httpx.Request("GET", url)


def _infos(*addrs: str):
    out = []
    for a in addrs:
        fam = socket.AF_INET6 if ":" in a else socket.AF_INET
        sockaddr = (a, 0, 0, 0) if fam == socket.AF_INET6 else (a, 0)
        out.append((fam, socket.SOCK_STREAM, 6, "", sockaddr))
    return out


def test_any_non_public_record_among_public_ones_is_refused(monkeypatch):
    # DNS-rebinding style answer: first record public, second private.
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: _infos("93.184.216.34", "10.0.0.7"))
    with pytest.raises(httpx.RequestError, match="10.0.0.7"):
        net.reject_non_public_target(_req("https://mixed.example/feed"))


def test_all_public_records_allowed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: _infos("93.184.216.34", "2606:2800:220:1::1"))
    assert net.reject_non_public_target(_req("https://ok.example/feed")) is None


def test_scoped_ipv6_address_is_parsed_and_refused(monkeypatch):
    # getaddrinfo can return 'fe80::1%eth0'. Whether the guard strips the zone
    # id or ip_address() tolerates it (3.9+), the result must be a refusal and
    # not a ValueError escaping from the hook.
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: _infos("fe80::1%eth0"))
    with pytest.raises(httpx.RequestError, match="fe80::1"):
        net.reject_non_public_target(_req("https://scoped.example/feed"))


def test_ipv4_mapped_loopback_literal_refused():
    with pytest.raises(httpx.RequestError, match="non-public address"):
        net.reject_non_public_target(_req("http://[::ffff:127.0.0.1]/feed"))


def test_literal_ip_skips_dns_lookup(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("getaddrinfo must not be called for literal IPs")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert net.reject_non_public_target(_req("http://93.184.216.34/feed")) is None
    with pytest.raises(httpx.RequestError):
        net.reject_non_public_target(_req("http://192.168.0.1/feed"))


@pytest.mark.parametrize("value", ["0", "true", "yes", "", " 1", "2"])
def test_only_exact_1_disables_guard(monkeypatch, value):
    monkeypatch.setenv("FETCH_ALLOW_PRIVATE_HOSTS", value)
    with pytest.raises(httpx.RequestError, match="non-public address"):
        net.reject_non_public_target(_req("http://127.0.0.1/feed"))


def test_opt_out_skips_dns_lookup_entirely(monkeypatch):
    monkeypatch.setenv("FETCH_ALLOW_PRIVATE_HOSTS", "1")

    def boom(*a, **k):
        raise AssertionError("no lookup expected when guard disabled")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert net.reject_non_public_target(_req("https://anything.example/")) is None


def test_unresolvable_name_fails_closed(monkeypatch):
    # The connection opens its own socket and resolves the name again, so a
    # name that fails to resolve in the hook but resolves to a private address
    # at connect time would slip past a fail-open guard. Refuse instead.
    def boom(*a, **k):
        raise socket.gaierror("Name or service not known")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(httpx.RequestError, match="does not resolve"):
        net.reject_non_public_target(_req("https://nx.attacker.example/feed"))


def test_unresolvable_name_allowed_when_guard_disabled(monkeypatch):
    # The opt-out short-circuits before any lookup, so an unresolvable name is
    # not turned into a refusal when the operator has asked for private hosts.
    monkeypatch.setenv("FETCH_ALLOW_PRIVATE_HOSTS", "1")

    def boom(*a, **k):
        raise socket.gaierror("Name or service not known")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert net.reject_non_public_target(_req("https://nx.example/feed")) is None


def test_error_carries_the_request(monkeypatch):
    req = _req("http://10.9.8.7/feed")
    with pytest.raises(httpx.RequestError) as ei:
        net.reject_non_public_target(req)
    assert ei.value.request is req
    assert "10.9.8.7" in str(ei.value)


def test_make_ssl_context_requires_verified_hostname():
    import ssl
    ctx = net.make_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert ctx.set_alpn_protocols(["http/1.1"]) is None


def test_make_http_client_settings():
    with net.make_http_client() as client:
        assert client.follow_redirects is True
        assert client.headers["User-Agent"] == net.USER_AGENT
        assert client.timeout == httpx.Timeout(net.HTTP_TIMEOUT)
