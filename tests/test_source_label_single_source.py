"""Per-source labels are built once, in models.source_label (#601).

fetch.iter_collectors writes the label into ``source_fetch_outcomes`` and
dead_sources.py joins sources.yaml entries to those rows by it. Each used to
build ``<family>/<id>`` / ``gh/<owner>/<repo>`` itself, and nothing linked them:
if one side changed, no label would match and the DEAD watchdog would go quiet
without any error. The two new watchdog readers (dead_sources, missed_days) also
read state.db through db.py helpers, as every stage does.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

import dead_sources
import fetch
from models import SourceFamily, source_label

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

SOURCES = {
    SourceFamily.RSS.value: [{"id": "r1", "url": "https://example.com/a"}],
    SourceFamily.ARXIV.value: [{"id": "a1", "query": "q"}, {"id": "a2", "query": "q"}],
    SourceFamily.HN.value: [{"id": "h1", "query": "q"}],
    SourceFamily.REDDIT.value: [{"id": "rd1", "url": "https://www.reddit.com/r/x/"},
                                {"id": "rd2", "url": "https://www.reddit.com/r/y/"}],
    SourceFamily.HTML.value: [{"id": "w1", "url": "https://example.com/news"}],
    SourceFamily.GITHUB_RELEASES.value: [{"owner": "o", "repo": "r"}],
}


def test_label_format_unchanged():
    # Existing source_fetch_outcomes rows (90-day retention) use these labels.
    assert source_label(SourceFamily.RSS, {"id": "x"}) == "rss/x"
    assert source_label(SourceFamily.GITHUB_RELEASES, {"owner": "o", "repo": "r"}) == "gh/o/r"


def test_every_fetched_label_is_known_to_dead_sources(monkeypatch, tmp_path):
    """Round trip: every label fetch records is a key dead_sources looks up."""
    for name in ("fetch_rss", "fetch_arxiv", "fetch_hn", "fetch_reddit", "fetch_html"):
        monkeypatch.setattr(fetch, name, lambda src, **kw: iter(()))
    monkeypatch.setattr(fetch, "fetch_github_releases", lambda entries: iter(()))
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)

    fetched = [label for label, _gen in fetch.iter_collectors(SOURCES, http_client=None)]

    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text(yaml.safe_dump(SOURCES), encoding="utf-8")
    known = dead_sources.configured_thresholds(sources_file, 7)

    assert len(fetched) == sum(len(v) for v in SOURCES.values())
    assert sorted(fetched) == sorted(known)


def _label_fstrings(path: Path) -> list[str]:
    """f-strings that start with a family prefix or ``gh/``: inline label builders."""
    prefixes = tuple(f"{f.value}/" for f in SourceFamily) + ("gh/",)
    hits = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if isinstance(first, ast.Constant) and str(first.value).startswith(prefixes):
                hits.append(f"{path.name}:{node.lineno}")
    return hits


@pytest.mark.parametrize("module", ["fetch.py", "dead_sources.py"])
def test_no_inline_label_fstrings(module):
    hits = _label_fstrings(SCRIPTS / module)
    assert hits == [], f"build source labels with models.source_label: {hits}"


def test_label_detector_catches_regression(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text('x = f"gh/{o}/{r}"\ny = f"rss/{i}"\nz = f"other/{i}"\n')
    assert _label_fstrings(bad) == ["bad.py:1", "bad.py:2"]


@pytest.mark.parametrize("module", ["missed_days.py", "dead_sources.py"])
def test_watchdog_readers_have_no_inline_sql(module):
    """state.db reads go through db.py helpers (db.connect_readonly & co)."""
    tree = ast.parse((SCRIPTS / module).read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript", "connect"}
    ]
    imports = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Import) and any(a.name == "sqlite3" for a in node.names)
    ]
    assert calls == [] and imports == [], f"move {module} SQL into db.py: {calls + imports}"
