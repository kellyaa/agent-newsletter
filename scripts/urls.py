"""URL policy shared by every script that accepts, stores or probes a URL.

Pure functions, no I/O. They used to live in db.py (the data access layer)
and as inline copies in new_edition.py, which re-derived the reddit feed URL
on its own and drifted from fetch.py: for ``top.json?t=day&limit=99`` it
probed ``...?t=day&limit=99&limit=1`` while fetch reads ``...?t=day&limit=N``.
One module means a fix to scheme policy, dedup keys or feed derivation lands
in every caller at once. ``tests/test_url_policy_single_source.py`` enforces
this.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunparse, urlunsplit

TRACKING_PARAM_PREFIXES = ("utm_", "ref_")
TRACKING_PARAMS = {"ref", "fbclid", "gclid", "mc_cid", "mc_eid", "source"}

# Schemes allowed for item URLs that flow into rendered <a href> on the
# public site (and RSS feed links). Anything else — javascript:, data:,
# file:, vbscript:, etc. — is a click-to-execute or local-file-disclosure
# vector and must be rejected at ingest, before it ever reaches state.db.
# See issue #248.
ALLOWED_URL_SCHEMES = {"http", "https"}

_ARXIV_PATH_RE = re.compile(r"^/(?:pdf|abs|html)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?(?:\.pdf)?/?$")


def is_allowed_url(url: str) -> bool:
    """Return True iff `url` parses to an http(s) scheme.

    Used at ingest (fetch.py) to reject non-http(s) URLs — javascript:,
    data:, file:, etc. — before they're persisted to state.db and
    eventually rendered as a clickable link on the site or in RSS, and by
    new_edition.py before it probes a user- or LLM-supplied URL.
    """
    try:
        scheme = urlparse(url.strip()).scheme.lower()
    except (AttributeError, ValueError):
        return False
    return scheme in ALLOWED_URL_SCHEMES


def canonicalize_url(url: str) -> str:
    """Return a normalized URL safe to use as a dedup key.

    - Lowercase scheme + host; drop an explicit default port (:80 / :443)
    - Strip tracking query params
    - Strip trailing slash on path
    - Resolve arxiv pdf/abs/v<N> variants to the abs form
    """
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    # An explicit default port names the same resource as the bare host, and
    # would otherwise defeat both dedup and the arxiv host match below.
    default_port = {"http": ":80", "https": ":443"}.get(scheme)
    if default_port and netloc.endswith(default_port):
        netloc = netloc[: -len(default_port)]
    path = parsed.path or "/"

    # Exact host or a subdomain (export.arxiv.org); a bare endswith("arxiv.org")
    # would also rewrite paths on unrelated hosts such as notarxiv.org.
    if netloc == "arxiv.org" or netloc.endswith(".arxiv.org"):
        # /pdf/2401.12345.pdf, /pdf/2401.12345v2, /abs/2401.12345v3 → /abs/2401.12345
        m = _ARXIV_PATH_RE.match(path)
        if m:
            path = f"/abs/{m.group(1)}"

    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"

    kept = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=False):
        kl = k.lower()
        if kl in TRACKING_PARAMS:
            continue
        if any(kl.startswith(p) for p in TRACKING_PARAM_PREFIXES):
            continue
        kept.append((k, v))
    query = urlencode(kept)

    return urlunparse((scheme, netloc, path, "", query, ""))


def url_id(url: str) -> str:
    """The items primary key: sha256 of :func:`canonicalize_url`."""
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()


def reddit_rss_url(url: str, limit: int) -> str:
    """Derive the unauthenticated RSS URL from a ``sources.yaml`` reddit URL.

    ``https://www.reddit.com/r/<sub>/top.json?t=day`` becomes
    ``https://www.reddit.com/r/<sub>/top/.rss?t=day&limit=<limit>``. Reddit
    answers 403 to the ``.json`` endpoints without OAuth, but the Atom feed
    still works and is already sorted by score, so ``limit`` (top N) takes the
    place of the old ``min_score`` gate. An existing ``limit`` is replaced, not
    duplicated. fetch.py reads this URL; new_edition.py probes it with
    ``limit=1``, so the probe checks the endpoint the pipeline will read.
    """
    parts = urlsplit(url)
    path = parts.path
    if not path.endswith(".rss"):
        path = path.removesuffix(".json").rstrip("/") + "/.rss"
    query = dict(parse_qsl(parts.query))
    query["limit"] = str(limit)
    return urlunsplit(parts._replace(path=path, query=urlencode(query)))


# The arXiv export API and the query fetch.fetch_arxiv sends. new_edition's
# probe uses the same two, so it checks the query the pipeline will run.
ARXIV_API_URL = "https://export.arxiv.org/api/query"


def arxiv_query_params(query: str, max_results: int) -> dict[str, str | int]:
    """Query params for :data:`ARXIV_API_URL`: newest submissions first."""
    return {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
