"""Outbound HTTP client shared by every script that talks to the network.

fetch.py (and check_edition.py through it) and new_edition.py all probe
user-configured URLs. They must use the same client so every one gets the
SSRF guard (#505), the ALPN-free TLS context the egress proxy needs, and one
User-Agent. Keeping the factory here, not in fetch.py, lets non-stage scripts
use it without importing a pipeline stage.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import ssl

import certifi
import httpx

USER_AGENT = "agent-newsletter/0.1 (+https://github.com/; bot)"
HTTP_TIMEOUT = 20.0


class NoALPNContext(ssl.SSLContext):
    """SSLContext that ignores attempts to advertise ALPN protocols.

    The pod's egress proxy answers 406 (empty body, no content-type, and one
    fewer x-cache hop than a real response) to any TLS handshake that carries
    an ALPN extension, so the request never reaches the origin. httpx always
    calls set_alpn_protocols(["http/1.1"]) on its context; curl and urllib
    happen not to send ALPN through the proxy, which is why they still work.
    Swallowing the call keeps the ClientHello ALPN-free.
    """

    def set_alpn_protocols(self, protocols):  # noqa: D102 - see class docstring
        return None


def make_ssl_context() -> ssl.SSLContext:
    ctx = NoALPNContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=certifi.where())
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    return ctx


def reject_non_public_target(request: httpx.Request) -> None:
    """httpx request hook: refuse requests aimed at non-public addresses.

    httpx runs request hooks for every hop, so a public feed URL that redirects
    to loopback, RFC-1918, link-local (cloud metadata at 169.254.169.254) or
    other non-global addresses is stopped before the connection is made. A host
    that does not resolve is let through; the request itself then fails.
    Set FETCH_ALLOW_PRIVATE_HOSTS=1 to disable the check (local test feeds,
    self-hosted internal sources).
    """
    if os.environ.get("FETCH_ALLOW_PRIVATE_HOSTS") == "1":
        return
    host = request.url.host
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            return
        addrs = [ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos]
    for addr in addrs:
        if not addr.is_global:
            raise httpx.RequestError(
                f"refusing to fetch {host}: resolves to non-public address {addr}",
                request=request,
            )


def make_http_client() -> httpx.Client:
    """Create a shared HTTP client with connection pooling.

    A single Client instance reuses TCP connections across requests to the
    same host, avoiding repeated TLS handshakes and TCP slow-starts. The
    caller is responsible for closing the client when done (via context
    manager or explicit .close()).
    """
    return httpx.Client(
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        event_hooks={"request": [reject_non_public_target]},
        verify=make_ssl_context(),
    )
