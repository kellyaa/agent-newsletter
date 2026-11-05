"""``publish.main(today=...)`` contract: the run date is injectable.

publish is date-keyed (issue filename, idempotent-skip lookup, runs row,
published_date). Taking ``today`` as a parameter lets tests and backfills pin
the date without exporting ``RUN_DATE``, matching ``write.main(today=...)``
and fetch/prefilter/rank's ``main(now=...)``. The AST guard keeps the
signature in place.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PINNED = "2026-07-09"
ENV_DATE = "2000-01-01"


def test_publish_main_accepts_optional_today():
    tree = ast.parse((SCRIPTS / "publish.py").read_text())
    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    assert len(mains) == 1
    args = mains[0].args
    assert [a.arg for a in args.args] == ["today"]
    assert len(args.defaults) == 1
    assert isinstance(args.defaults[0], ast.Constant) and args.defaults[0].value is None


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    import db as db_mod
    import publish as publish_mod

    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    conn.execute(
        """
        INSERT INTO items (
            id, source, url, canonical_url, title, fetched_at, status,
            section, first_seen_date, last_seen_date, appearances
        ) VALUES ('f-1', 'rss:test', 'https://example.com/1', 'https://example.com/1',
                  'T', '2026-07-09T00:00:00Z', 'featured', 'papers',
                  '2026-07-09', '2026-07-09', 1)
        """
    )
    conn.commit()
    conn.close()

    issues = tmp_path / "issues"
    issues.mkdir()
    monkeypatch.setattr(publish_mod, "ISSUES_DIR", issues)
    monkeypatch.setattr(publish_mod, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(publish_mod, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.delenv("LLM_USAGE_LOG", raising=False)
    monkeypatch.delenv("RUN_STARTED_AT", raising=False)
    monkeypatch.setenv("RUN_DATE", ENV_DATE)
    return publish_mod, issues, db_path


def _issue(issues: Path, date: str, size: int) -> None:
    (issues / f"{date}.md").write_text("x" * size)


def _run_dates(db_path) -> list[str]:
    import db as db_mod

    conn = db_mod.connect(db_path)
    try:
        return [r["date"] for r in conn.execute("SELECT date FROM runs ORDER BY date")]
    finally:
        conn.close()


def test_injected_today_overrides_run_date(wired):
    publish_mod, issues, db_path = wired
    _issue(issues, PINNED, publish_mod.MIN_FILE_SIZE_BYTES + 10)
    assert publish_mod.main(today=PINNED) == 0
    assert _run_dates(db_path) == [PINNED]


def test_injected_today_keys_issue_file_lookup(wired):
    publish_mod, issues, db_path = wired
    # Only the RUN_DATE file exists; the pinned date must not fall back to it.
    _issue(issues, ENV_DATE, publish_mod.MIN_FILE_SIZE_BYTES + 10)
    assert publish_mod.main(today=PINNED) == 2
    assert _run_dates(db_path) == []


def test_default_today_falls_back_to_run_date(wired):
    publish_mod, issues, db_path = wired
    _issue(issues, ENV_DATE, publish_mod.MIN_FILE_SIZE_BYTES + 10)
    assert publish_mod.main() == 0
    assert _run_dates(db_path) == [ENV_DATE]
