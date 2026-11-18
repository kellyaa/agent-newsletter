"""A source that cannot be fetched is recorded as failed, never as a quiet one (#762).

``fetch.main`` isolates each source in ``run_collector``: an adapter that
*raises* is logged as ``collector failed``, counted in ``errors=N``, written to
``source_fetch_outcomes`` with its ``error``, and when every source fails the
stage exits ``NO_DATA`` without stamping ``fetch_completed_date``, so a retry
fetches again. ``fetch_github_releases`` and ``fetch_html`` used to catch their
own failures (``gh`` missing or failing, bad JSON, an invalid name, no
``link_selector``) and return normally, which recorded the broken source as
``error=None, items_seen=0``: indistinguishable from a quiet feed, uncounted,
and a day whose only sources were broken shipped ``OK``.

This drives each broken-config / broken-environment case through ``fetch.main``
and requires a recorded error. It also pins ``link_selector`` as a required
``html`` key, so ``check_edition`` rejects the entry before it ever runs.
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import check_edition
import db as db_mod
import fetch
from edition import read_sources
from models import SOURCE_REQUIRED_KEYS, ExitCode, MetaKey, SourceFamily


@pytest.fixture()
def run(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    sources = tmp_path / "sources.yaml"
    monkeypatch.setattr(fetch, "sources_path", lambda: sources)
    monkeypatch.setattr(fetch, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(fetch, "init_db", lambda: db_mod.init_db(db_path))

    def _run(yaml_text: str):
        sources.write_text(yaml_text)
        rc = fetch.main()
        conn = db_mod.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT label, items_seen, error FROM source_fetch_outcomes ORDER BY label"
            ).fetchall()
            done = db_mod.get_meta(conn, MetaKey.FETCH_DONE)
        finally:
            conn.close()
        return rc, [tuple(r) for r in rows], done

    return _run


GH = "github_releases:\n  - owner: openai\n    repo: openai-python\n"


def _gh_reply(stdout: str):
    result = MagicMock()
    result.stdout = stdout
    return result


@pytest.mark.parametrize("side_effect,match", [
    (FileNotFoundError("gh"), "gh"),
    (subprocess.CalledProcessError(1, "gh"), "gh api failed"),
    (subprocess.TimeoutExpired("gh", 30), "gh api failed"),
    ([_gh_reply("not json")], "invalid JSON"),
    ([_gh_reply(json.dumps({"message": "Not Found"}))], "expected a JSON list"),
], ids=["gh-missing", "gh-exit", "gh-timeout", "bad-json", "non-list-json"])
def test_github_releases_failure_is_recorded(run, side_effect, match):
    with patch("subprocess.run", side_effect=side_effect):
        rc, rows, done = run(GH)
    assert rc == ExitCode.NO_DATA
    [(label, seen, error)] = rows
    assert (label, seen) == ("gh/openai/openai-python", 0)
    assert error is not None and match in error
    assert done is None  # a retry fetches again


def test_github_releases_invalid_name_is_recorded_without_calling_gh(run):
    with patch("subprocess.run") as gh:
        rc, rows, _ = run("github_releases:\n  - owner: openai\n    repo: '..'\n")
    gh.assert_not_called()
    assert rc == ExitCode.NO_DATA
    [(_label, _seen, error)] = rows
    assert error is not None and "invalid owner/repo" in error


def test_one_failing_repo_does_not_hide_the_others(run):
    def gh(cmd, **_kw):
        if "broken" in cmd[2]:
            raise subprocess.CalledProcessError(1, "gh")
        return _gh_reply(json.dumps([{"html_url": "https://github.com/o/ok/releases/v1",
                                      "tag_name": "v1"}]))
    with patch("subprocess.run", side_effect=gh):
        rc, rows, done = run(
            "github_releases:\n  - owner: o\n    repo: broken\n  - owner: o\n    repo: ok\n")
    assert rc == ExitCode.OK
    assert rows[0][0] == "gh/o/broken" and rows[0][2] is not None
    assert rows[1] == ("gh/o/ok", 1, None)
    assert done is not None


def test_html_without_link_selector_is_recorded(run):
    rc, rows, done = run("html:\n  - id: vendor\n    url: https://example.com/news\n")
    assert rc == ExitCode.NO_DATA
    [(label, seen, error)] = rows
    assert (label, seen) == ("html/vendor", 0)
    assert error is not None and "link_selector" in error
    assert done is None


def test_html_link_selector_is_a_required_key(tmp_path):
    assert "link_selector" in SOURCE_REQUIRED_KEYS[SourceFamily.HTML]
    path = tmp_path / "sources.yaml"
    path.write_text("html:\n  - id: vendor\n    url: https://example.com/news\n")
    result = check_edition.LintResult()
    check_edition._check_sources(read_sources(path), result)
    assert any("missing ['link_selector']" in e for e in result.errors), result.errors
