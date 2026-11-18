"""An edition's sources.yaml is parsed once, by ``edition.read_sources``.

fetch, check_edition and dead_sources each used to ``yaml.safe_load`` the file
themselves and handled a malformed one differently. check_edition reported
it, dead_sources skipped it, and fetch crashed: a comments-only file on a draft
edition passed lint and then failed every daily run at stage ``fetch`` with
``AttributeError: 'NoneType' object has no attribute 'get'``. A single
non-mapping entry also aborted the whole fetch, not just that source. Now the
three read the same structure: lint reports what is wrong, fetch skips exactly
that, and the watchdog labels the same entries fetch records.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

import check_edition
import db as db_mod
import dead_sources
import edition
import fetch
from models import ExitCode, SourceFamily, source_label

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO = SCRIPTS.parent

GOOD_RSS = "  - id: ok\n    url: https://example.com/feed.xml\n"

# (yaml text, fatal?, entries the reader keeps as {family: [ids]}, problem fragments)
CASES = {
    "empty": ("", False, {}, []),
    "comments_only": ("# sources to come\n", False, {}, []),
    "null_family": ("rss:\n", False, {}, []),
    "string_entry": ("rss:\n  - https://example.com/feed.xml\n" + GOOD_RSS, False,
                     {"rss": ["ok"]}, ["rss[0] must be a mapping"]),
    "family_is_mapping": ("rss:\n  id: a\n  url: https://example.com/f\nhn:\n"
                          "  - id: h\n    query: q\n", False,
                          {"hn": ["h"]}, ["'rss' must be a list"]),
    "unknown_family": ("rsss:\n  - id: a\n    url: u\nrss:\n" + GOOD_RSS, False,
                       {"rss": ["ok"]}, ["unknown source families ['rsss']"]),
    "top_level_list": ("- a\n- b\n", True, {}, []),
    "invalid_yaml": ("rss: [unclosed\n", True, {}, []),
}


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(text)
    return p


def _ids(sources: dict) -> dict[str, list[str]]:
    return {fam: [e.get("id") for e in entries] for fam, entries in sources.items() if entries}


@pytest.mark.parametrize("name", CASES)
def test_reader_shape(tmp_path, name):
    text, is_fatal, kept, fragments = CASES[name]
    sf = edition.read_sources(_write(tmp_path, text))
    assert (sf.fatal is not None) == is_fatal
    assert _ids(sf.as_dict()) == kept
    for frag in fragments:
        assert any(frag in p for p in sf.problems), sf.problems
    if not fragments:
        assert sf.problems == ()


def test_missing_file_is_fatal(tmp_path):
    sf = edition.read_sources(tmp_path / "nope.yaml")
    assert sf.fatal and "missing" in sf.fatal
    assert sf.as_dict() == {}


def test_unreadable_file_is_fatal(tmp_path):
    p = tmp_path / "sources.yaml"
    p.write_bytes(b"\xff\xfe\x00bad")
    sf = edition.read_sources(p)
    assert sf.fatal and "unreadable" in sf.fatal


def test_entries_keep_their_original_index(tmp_path):
    sf = edition.read_sources(_write(tmp_path, "rss:\n  - nope\n" + GOOD_RSS))
    assert [i for i, _e in sf.entries("rss")] == [1]
    assert sf.entries("not-a-family") == ()


def test_known_families_only_in_declaration_order(tmp_path):
    text = "".join(f"{f.value}: []\n" for f in reversed(SourceFamily)) + "bogus: []\n"
    sf = edition.read_sources(_write(tmp_path, text))
    assert list(sf.as_dict()) == [f.value for f in SourceFamily]


# --- the three callers agree ---------------------------------------------


def _lint_errors(sf) -> list[str]:
    result = check_edition.LintResult()
    check_edition._check_sources(sf, result)
    return result.errors


def _fetched_labels(monkeypatch, path: Path, tmp_path: Path) -> tuple[int, list[str]]:
    """Run fetch.main on `path` with every adapter stubbed; return (rc, labels walked)."""
    walked: list[str] = []

    def stub(src, *a, **kw):
        return iter(())

    for fn in ("fetch_rss", "fetch_arxiv", "fetch_hn", "fetch_reddit", "fetch_html",
               "fetch_github_releases"):
        monkeypatch.setattr(fetch, fn, stub)
    real_iter = fetch.iter_collectors

    def spy(sources, client):
        for label, gen in real_iter(sources, client):
            walked.append(label)
            yield label, gen

    db_path = tmp_path / "state.db"
    monkeypatch.setattr(fetch, "iter_collectors", spy)
    monkeypatch.setattr(fetch, "sources_path", lambda: path)
    monkeypatch.setattr(fetch, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(fetch, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    return fetch.main(), walked


@pytest.mark.parametrize("name", CASES)
def test_fetch_lint_and_watchdog_agree(tmp_path, monkeypatch, name):
    text, is_fatal, kept, fragments = CASES[name]
    path = _write(tmp_path, text)
    sf = edition.read_sources(path)

    # Lint: everything the reader flags is an error, nothing else is.
    errors = _lint_errors(sf)
    if is_fatal:
        assert errors == [sf.fatal]
    else:
        assert errors == list(sf.problems)

    # Fetch: never crashes; walks exactly the entries lint accepted.
    rc, walked = _fetched_labels(monkeypatch, path, tmp_path)
    expected = [source_label(SourceFamily(fam), {"id": i})
                for fam, ids in kept.items() for i in ids]
    if is_fatal:
        assert rc == ExitCode.BAD_CONFIG
        assert walked == []
    else:
        assert rc == ExitCode.OK
        assert walked == expected

    # Watchdog: labels the same entries fetch records outcomes for.
    thresholds = dead_sources.configured_thresholds(path, 7)
    if is_fatal:
        assert thresholds is None
    else:
        assert sorted(thresholds) == sorted(expected)


def test_fetch_logs_skipped_parts(tmp_path, monkeypatch, caplog):
    path = _write(tmp_path, CASES["string_entry"][0])
    with caplog.at_level(logging.WARNING, logger="fetch"):
        rc, walked = _fetched_labels(monkeypatch, path, tmp_path)
    assert rc == ExitCode.OK and walked == ["rss/ok"]
    assert any("rss[0] must be a mapping" in r.getMessage() and "skipped" in r.getMessage()
               for r in caplog.records)


def test_fetch_names_the_file_on_fatal(tmp_path, monkeypatch, caplog):
    path = tmp_path / "missing.yaml"
    with caplog.at_level(logging.ERROR, logger="fetch"):
        rc, _ = _fetched_labels(monkeypatch, path, tmp_path)
    assert rc == ExitCode.BAD_CONFIG
    assert any(str(path) in r.getMessage() for r in caplog.records)


def test_comments_only_draft_lints_and_fetches(tmp_path, monkeypatch):
    # The reported bug: lint-clean draft whose sources.yaml has no entries yet.
    editions_dir = tmp_path / "editions"
    edition.copy_config(REPO / "editions" / "agents", editions_dir / "demo")
    cfg = editions_dir / "demo" / "edition.yaml"
    text = cfg.read_text()
    text = "\n".join(
        "slug: demo" if line.startswith("slug:") else
        "status: draft" if line.startswith("status:") else line
        for line in text.splitlines()
    ) + "\n"
    cfg.write_text(text)
    (editions_dir / "demo" / "sources.yaml").write_text("# sources to come\n")
    monkeypatch.setattr(edition, "EDITIONS_DIR", editions_dir)

    result = check_edition.lint("demo")
    assert result.ok, result.errors
    assert result.sources == {f.value: [] for f in SourceFamily}

    rc, walked = _fetched_labels(monkeypatch, editions_dir / "demo" / "sources.yaml", tmp_path)
    assert rc == ExitCode.OK and walked == []


# --- guard -----------------------------------------------------------------


def _yaml_load_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "yaml"
        and n.func.attr in {"safe_load", "load", "full_load", "unsafe_load"}
    ]


# Scripts that parse YAML that is not a sources.yaml: edition.yaml and the
# reader itself (edition), issue frontmatter (write), the edition template
# (new_edition).
OTHER_YAML = {"edition", "write", "new_edition"}


@pytest.mark.parametrize("name", ["fetch", "check_edition", "dead_sources"])
def test_sources_readers_do_not_parse_yaml(name):
    calls = _yaml_load_calls(SCRIPTS / f"{name}.py")
    assert calls == [], (
        f"scripts/{name}.py:{calls} parses YAML itself; read sources.yaml "
        "only via edition.read_sources"
    )


def test_no_new_yaml_parsers():
    parsers = {p.stem for p in SCRIPTS.glob("*.py") if _yaml_load_calls(p)}
    assert parsers <= OTHER_YAML, (
        f"{sorted(parsers - OTHER_YAML)} parse YAML; a sources.yaml must be read "
        "via edition.read_sources (see AGENTS.md)"
    )
