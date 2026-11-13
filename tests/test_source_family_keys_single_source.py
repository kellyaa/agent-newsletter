"""Family-specific sources.yaml int keys and the arXiv query have one home.

``arxiv.max_results``, ``hn.hours_back`` / ``min_points`` and
``html.max_items`` were read raw by fetch (``source.get(key, <literal>)``) and
never linted. ``hours_back: "48"`` passed check_edition and then crashed the
HN collector with a TypeError on every run; ``min_points: -5`` or
``max_results: 0`` went to the API unchanged. new_edition kept its own copies
of the defaults, and fetch and new_edition each spelled the arXiv API URL,
its sort params and the 3 s politeness delay. These tests pin:

* the keys and defaults live in ``models.SOURCE_FAMILY_INT_KEYS``;
* lint rejects every value fetch would discard, and fetch never sends one;
* the arXiv URL / params live in ``urls`` and the delay in ``models``;
* no inline copy of any of them comes back.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import MagicMock

import pytest
import yaml

import check_edition
import fetch
import new_edition
import urls
from models import ARXIV_REQUEST_DELAY_S, SOURCE_FAMILY_INT_KEYS, SourceFamily
from test_check_edition import _clone_agents, editions_dir  # noqa: F401  (fixture)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
BAD = [0, -5, True, "48", 2.5]

BASE = {
    SourceFamily.ARXIV: {"id": "a", "query": "cat:cs.AI"},
    SourceFamily.HN: {"id": "h", "query": "agents"},
    SourceFamily.HTML: {"id": "x", "url": "https://example.com/blog", "link_selector": "a"},
}
KEYS = [(f, k) for f, keys in SOURCE_FAMILY_INT_KEYS.items() for k in keys]


def test_every_family_key_has_a_lint_fixture():
    assert set(SOURCE_FAMILY_INT_KEYS) <= set(BASE)
    assert set(SOURCE_FAMILY_INT_KEYS) <= set(SourceFamily)


@pytest.mark.parametrize("family,key", KEYS, ids=lambda v: str(v))
@pytest.mark.parametrize("value", BAD, ids=repr)
def test_lint_rejects_what_fetch_discards(editions_dir, family, key, value):
    dest = _clone_agents(editions_dir)
    (dest / "sources.yaml").write_text(yaml.safe_dump(
        {family.value: [{**BASE[family], key: value}]}))
    assert any(f"{key} must be a positive integer" in e for e in check_edition.lint("demo").errors)
    assert fetch._family_int(family, {**BASE[family], key: value}, key) == \
        SOURCE_FAMILY_INT_KEYS[family][key]


@pytest.mark.parametrize("family,key", KEYS, ids=lambda v: str(v))
def test_lint_accepts_positive_ints_and_fetch_uses_them(editions_dir, family, key):
    dest = _clone_agents(editions_dir)
    (dest / "sources.yaml").write_text(yaml.safe_dump(
        {family.value: [{**BASE[family], key: 7}]}))
    assert not any(key in e for e in check_edition.lint("demo").errors)
    assert fetch._family_int(family, {**BASE[family], key: 7}, key) == 7
    assert fetch._family_int(family, dict(BASE[family]), key) == SOURCE_FAMILY_INT_KEYS[family][key]


def test_bad_value_warns(caplog):
    with caplog.at_level(logging.WARNING, logger=fetch.log.name):
        assert fetch._family_int(SourceFamily.HN, {"id": "h", "hours_back": "48"}, "hours_back") == 48
    assert "invalid hours_back '48'" in caplog.text


def _client_capturing(calls: list) -> MagicMock:
    resp = MagicMock(text="<feed/>", content=b"<feed/>")
    resp.json.return_value = {"hits": []}
    client = MagicMock()
    client.get.side_effect = lambda url, params=None: calls.append((url, params)) or resp
    return client


def test_bad_hn_values_no_longer_crash_or_reach_the_api():
    calls: list = []
    list(fetch.fetch_hn({"id": "h", "query": "q", "hours_back": "48", "min_points": -5},
                        client=_client_capturing(calls)))
    (_url, params), = calls
    assert params["numericFilters"].endswith(",points>=50")


def test_bad_arxiv_max_results_does_not_reach_the_api():
    calls: list = []
    list(fetch.fetch_arxiv({"id": "a", "query": "q", "max_results": 0},
                           client=_client_capturing(calls)))
    assert calls == [(urls.ARXIV_API_URL, urls.arxiv_query_params("q", 30))]


def test_new_edition_probes_the_query_fetch_runs():
    calls: list = []
    assert new_edition.check_arxiv(_client_capturing(calls), "q") == "query returned no results"
    assert calls == [(urls.ARXIV_API_URL, urls.arxiv_query_params("q", 1))]


@pytest.mark.parametrize("value", [None, 0, True, "30", 101])
def test_new_edition_scaffold_defaults_come_from_models(monkeypatch, value):
    monkeypatch.setattr(new_edition, "check_arxiv", lambda client, q: None)
    out, dropped = new_edition.validate_sources(
        {"arxiv": [{"query": "q", "max_results": value}], "hn": [{"query": "q"}]}, [],
        client=None, sleep=lambda _s: None)
    assert dropped == []
    assert out["arxiv"][0]["max_results"] == SOURCE_FAMILY_INT_KEYS[SourceFamily.ARXIV]["max_results"]
    assert out["hn"][0]["hours_back"] == SOURCE_FAMILY_INT_KEYS[SourceFamily.HN]["hours_back"]
    assert out["hn"][0]["min_points"] == new_edition.SCAFFOLD_HN_MIN_POINTS


# ─── no inline copies ──────────────────────────────────────────────────────

def _constants(path: Path) -> list[tuple[int, object]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [(n.lineno, n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant)]


ARXIV_API_HOST = urlsplit(urls.ARXIV_API_URL).hostname


def _is_arxiv_api(value: str) -> bool:
    """True for a URL (or bare host) whose host is the arXiv API host."""
    return (urlsplit(value).hostname or value) == ARXIV_API_HOST


def test_arxiv_api_detector():
    assert _is_arxiv_api(urls.ARXIV_API_URL)
    assert _is_arxiv_api("http://export.arxiv.org/api/query?x=1")
    assert _is_arxiv_api(ARXIV_API_HOST)
    assert not _is_arxiv_api("https://arxiv.org/abs/2403.00100")


@pytest.mark.parametrize("path", sorted(set(SCRIPTS.glob("*.py")) - {SCRIPTS / "urls.py"}),
                         ids=lambda p: p.name)
def test_arxiv_query_spelled_only_in_urls(path):
    hits = [(ln, v) for ln, v in _constants(path)
            if isinstance(v, str) and (_is_arxiv_api(v) or v in {"sortBy", "submittedDate"})]
    assert hits == [], f"{path.name}: use urls.ARXIV_API_URL / urls.arxiv_query_params: {hits}"


@pytest.mark.parametrize("name", ("fetch.py", "new_edition.py"))
def test_no_literal_sleep_for_arxiv(name):
    tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
    literal = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", getattr(n.func, "id", None)) == "sleep"
        and n.args and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == ARXIV_REQUEST_DELAY_S
    ]
    assert literal == [], f"{name}:{literal} use models.ARXIV_REQUEST_DELAY_S"


def test_fetch_reads_family_keys_only_through_the_helper():
    keys = {k for f in SOURCE_FAMILY_INT_KEYS.values() for k in f}
    tree = ast.parse((SCRIPTS / "fetch.py").read_text(encoding="utf-8"))
    raw = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "get"
        and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value in keys
    ]
    assert raw == [], f"fetch.py:{raw} reads a family int key raw; use _family_int"


@pytest.mark.parametrize("name", ("fetch.py", "new_edition.py"))
def test_no_local_family_defaults(name):
    tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
    assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    assert not assigned & {"SOURCE_FAMILY_INT_KEYS", "ARXIV_REQUEST_DELAY_S", "ARXIV_API"}
