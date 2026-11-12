"""scripts/missed_days.py: days with no run_attempts row (issue #519)."""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import db as db_mod
import missed_days

REAL = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 4)

EDITION_YAML = (
    "slug: {slug}\nname: {slug}\ntagline: t\nstatus: {status}\norder: {order}\n"
    "reader_profile: r\nkeywords: ['x']\ntags: [a]\nsections:\n"
    "  papers: {{featured_min: 7, appendix_min: 5, cap: 5}}\n"
    "  news: {{featured_min: 6, appendix_min: 4, cap: 6}}\n"
    "  blogs: {{featured_min: 6, appendix_min: 4, cap: 6}}\n"
)


def _record(root: Path, slug: str, *dates: str) -> None:
    path = root / "editions" / slug / "state.db"
    db_mod.init_db(path)
    conn = db_mod.connect(path)
    for d in dates:
        db_mod.insert_run_attempt(conn, d, 1, f"{d}T07:00:00Z", "abc", 0, "h")
    conn.close()


@pytest.fixture()
def editions(tmp_path, monkeypatch):
    """Edition configs (live agents, draft beta, paused gamma) and an empty content root."""
    cfg = tmp_path / "cfg"
    for slug, status, order in (("agents", "live", 10), ("beta", "draft", 20), ("gamma", "paused", 30)):
        (cfg / slug).mkdir(parents=True)
        (cfg / slug / "edition.yaml").write_text(EDITION_YAML.format(slug=slug, status=status, order=order))
    monkeypatch.setattr(missed_days.edition, "EDITIONS_DIR", cfg)
    root = tmp_path / "content"
    monkeypatch.setenv("CONTENT_ROOT", str(root))
    return root


def test_missed_dates_lists_the_gap_between_recorded_days():
    recorded = {"2026-10-29", "2026-10-30", "2026-11-01", "2026-11-02", "2026-11-03"}
    assert missed_days.missed_dates(recorded, TODAY, 7) == ["2026-10-31"]


def test_missed_dates_ignores_today_and_days_before_first_attempt():
    # today (11-04) has no row yet, and 10-28 predates the first attempt.
    assert missed_days.missed_dates({"2026-11-02", "2026-11-03"}, TODAY, 7) == []


def test_missed_dates_with_nothing_recorded_is_empty():
    assert missed_days.missed_dates(set(), TODAY, 7) == []


def test_missed_dates_window_is_limited_to_days():
    recorded = {"2026-10-01", "2026-11-03"}
    assert missed_days.missed_dates(recorded, TODAY, 2) == ["2026-11-02"]


def test_find_missed_reports_each_live_and_draft_edition_and_skips_paused(editions):
    _record(editions, "agents", "2026-10-30", "2026-11-01", "2026-11-02", "2026-11-03")
    _record(editions, "beta", "2026-11-01", "2026-11-03")
    _record(editions, "gamma", "2026-10-01")
    assert missed_days.find_missed(editions, TODAY, 7) == [
        ("agents", "2026-10-31"), ("beta", "2026-11-02"),
    ]


def test_find_missed_skips_edition_without_state_db(editions):
    _record(editions, "agents", "2026-11-02", "2026-11-03")
    assert missed_days.find_missed(editions, TODAY, 7) == []


def test_find_missed_includes_edition_with_invalid_config(editions, tmp_path, monkeypatch):
    bad = tmp_path / "cfg" / "broken"
    bad.mkdir()
    (bad / "edition.yaml").write_text("not: [valid")
    _record(editions, "broken", "2026-11-01", "2026-11-03")
    assert ("broken", "2026-11-02") in missed_days.find_missed(editions, TODAY, 7)


def test_attempt_dates_without_the_table_is_empty(tmp_path):
    path = tmp_path / "state.db"
    sqlite3.connect(path).close()
    assert missed_days.attempt_dates(path) == set()


def test_attempt_dates_is_read_only(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    before = path.read_bytes()
    missed_days.attempt_dates(path)
    assert path.read_bytes() == before


def test_main_prints_missed_lines_and_exits_1(editions, capsys):
    _record(editions, "agents", "2026-10-30", "2026-11-01", "2026-11-02")
    rc = missed_days.main(["--today", "2026-11-03"])
    assert capsys.readouterr().out == "MISSED: agents 2026-10-31\n"
    assert rc == 1


def test_main_exits_0_when_no_gaps(editions, capsys):
    _record(editions, "agents", "2026-11-01", "2026-11-02")
    assert missed_days.main(["--today", "2026-11-03"]) == 0
    assert capsys.readouterr().out == ""


def test_main_defaults_today_to_the_local_date(editions, capsys):
    assert missed_days.main([]) == 0


def test_main_rejects_non_positive_days(editions):
    with pytest.raises(SystemExit) as exc:
        missed_days.main(["--days", "0"])
    assert exc.value.code == 2


def test_main_default_window_is_seven_days(editions, capsys):
    """Only the 7 days before today are checked by default: 10-26 is out of range."""
    _record(editions, "agents", "2026-10-20", "2026-11-03")
    rc = missed_days.main(["--today", "2026-11-03"])
    assert missed_days.DEFAULT_DAYS == 7
    assert capsys.readouterr().out.splitlines() == [
        f"MISSED: agents 2026-10-{d}" for d in (27, 28, 29, 30, 31)
    ] + ["MISSED: agents 2026-11-01", "MISSED: agents 2026-11-02"]
    assert rc == 1


def test_main_accepts_a_one_day_window(editions, capsys):
    """--days 1 is the smallest valid window: it checks only yesterday."""
    _record(editions, "agents", "2026-10-30")
    rc = missed_days.main(["--days", "1", "--today", "2026-11-03"])
    assert capsys.readouterr().out == "MISSED: agents 2026-11-02\n"
    assert rc == 1


def test_main_rejects_negative_days(editions):
    with pytest.raises(SystemExit) as exc:
        missed_days.main(["--days", "-3"])
    assert exc.value.code == 2


# ─── wired into scripts/watchdog.sh ───────────────────────────────────────

FAKE_RUN = "#!/usr/bin/env bash\nexit 0\n"


def test_watchdog_check_prints_missed_lines_before_status(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in ("watchdog.sh", "edition.py", "models.py", "db.py", "urls.py", "missed_days.py",
                 "runlog.sh", "runlock.sh", "editionlist.sh", "pyrunner.sh"):
        shutil.copy2(REAL / "scripts" / name, repo / "scripts" / name)
    (repo / "run.sh").write_text(FAKE_RUN)
    (repo / "editions" / "agents").mkdir(parents=True)
    (repo / "editions" / "agents" / "edition.yaml").write_text(
        EDITION_YAML.format(slug="agents", status="live", order=10))
    _record(repo, "agents", "2000-01-01", "2000-01-03")  # long ago: outside the 7-day window
    today = date.today()
    first = today.fromordinal(today.toordinal() - 3)
    last = today.fromordinal(today.toordinal() - 1)
    _record(repo, "agents", first.isoformat(), last.isoformat())

    p = subprocess.run(
        ["bash", str(repo / "scripts" / "watchdog.sh"), "--check"], cwd=repo,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "NEWSLETTER_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=60,
    )
    lines = p.stdout.strip().splitlines()
    missed = today.fromordinal(today.toordinal() - 2).isoformat()
    assert f"MISSED: agents {missed}" in lines
    assert lines[-1].startswith("STATUS: ")
    assert p.returncode == 1  # no run log for today: unchanged behaviour
