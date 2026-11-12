"""scripts/dead_sources.py: sources with no items for days (issue #572)."""
from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

import dead_sources
import db as db_mod

REAL = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 10)

EDITION_YAML = (
    "slug: {slug}\nname: {slug}\ntagline: t\nstatus: {status}\norder: {order}\n"
    "reader_profile: r\nkeywords: ['x']\ntags: [a]\nsections:\n"
    "  papers: {{featured_min: 7, appendix_min: 5, cap: 5}}\n"
    "  news: {{featured_min: 6, appendix_min: 4, cap: 6}}\n"
    "  blogs: {{featured_min: 6, appendix_min: 4, cap: 6}}\n"
)
SOURCES_YAML = """\
rss:
  - id: quiet
    url: https://example.com/feed
  - id: slow
    url: https://example.com/slow
    dead_after_days: 30
  - id: bad
    url: https://example.com/bad
    dead_after_days: 0
github_releases:
  - owner: acme
    repo: tool
"""


def _day(n: int) -> str:
    """The date `n` days before TODAY."""
    return (TODAY - timedelta(days=n)).isoformat()


def _record(root: Path, slug: str, label: str, seen_by_age: dict[int, int],
            error: str | None = None) -> None:
    """One outcome row per (age in days, items seen)."""
    path = root / "editions" / slug / "state.db"
    db_mod.init_db(path)
    conn = db_mod.connect(path)
    for age, seen in seen_by_age.items():
        db_mod.record_source_fetch(conn, _day(age), label, items_seen=seen, items_new=0,
                                   error=error, fetched_at=f"{_day(age)}T07:00:00Z")
    conn.close()


@pytest.fixture()
def editions(tmp_path, monkeypatch):
    """Live agents, draft beta, paused gamma, each with the same sources.yaml."""
    cfg = tmp_path / "cfg"
    for slug, status, order in (("agents", "live", 10), ("beta", "draft", 20), ("gamma", "paused", 30)):
        (cfg / slug).mkdir(parents=True)
        (cfg / slug / "edition.yaml").write_text(EDITION_YAML.format(slug=slug, status=status, order=order))
        (cfg / slug / "sources.yaml").write_text(SOURCES_YAML)
    monkeypatch.setattr(dead_sources.edition, "EDITIONS_DIR", cfg)
    root = tmp_path / "content"
    monkeypatch.setenv("CONTENT_ROOT", str(root))
    return root


ZERO_WEEK = {n: 0 for n in range(0, 8)}  # 8 days of empty fetches


def test_is_dead_when_every_run_in_the_window_is_empty():
    rows = [(_day(n), False) for n in range(0, 8)]
    assert dead_sources.is_dead(rows, TODAY, 7)


def test_is_not_dead_when_any_run_in_the_window_produced_items():
    rows = [(_day(n), n == 3) for n in range(0, 8)]
    assert not dead_sources.is_dead(rows, TODAY, 7)


def test_old_items_outside_the_window_do_not_save_a_source():
    rows = [(_day(n), n == 20) for n in range(0, 21)]
    assert dead_sources.is_dead(rows, TODAY, 7)


def test_new_source_gets_a_grace_period_equal_to_its_threshold():
    rows = [(_day(n), False) for n in range(0, 4)]  # first row only 3 days old
    assert not dead_sources.is_dead(rows, TODAY, 7)


def test_window_is_exactly_the_last_n_days_today_included():
    """days=3 covers today and the two days before it; a healthy day 3 ago is outside."""
    rows = [(_day(0), False), (_day(1), False), (_day(2), False), (_day(3), True)]
    assert dead_sources.is_dead(rows, TODAY, 3)


def test_a_healthy_run_on_the_first_day_of_the_window_saves_the_source():
    rows = [(_day(0), False), (_day(1), False), (_day(2), True)]
    assert not dead_sources.is_dead(rows, TODAY, 3)


def test_source_first_seen_exactly_threshold_days_ago_is_dead():
    """The grace period ends once the first row is as old as the window's first day."""
    rows = [(_day(n), False) for n in range(0, 3)]
    assert dead_sources.is_dead(rows, TODAY, 3)


def test_source_with_no_rows_in_the_window_is_not_dead():
    assert not dead_sources.is_dead([(_day(30), False)], TODAY, 7)


def test_find_dead_reports_zero_item_and_error_sources(editions):
    _record(editions, "agents", "rss/quiet", ZERO_WEEK)
    _record(editions, "agents", "gh/acme/tool", {n: 0 for n in range(0, 8)}, error="boom")
    assert sorted(dead_sources.find_dead(editions, TODAY, 7)) == [
        ("agents", "gh/acme/tool", 7), ("agents", "rss/quiet", 7),
    ]


def test_find_dead_ignores_a_healthy_source(editions):
    _record(editions, "agents", "rss/quiet", {**ZERO_WEEK, 2: 5})
    assert dead_sources.find_dead(editions, TODAY, 7) == []


def test_dead_after_days_override_extends_the_threshold(editions):
    _record(editions, "agents", "rss/slow", ZERO_WEEK)  # 8 days of nothing, threshold 30
    assert dead_sources.find_dead(editions, TODAY, 7) == []
    _record(editions, "agents", "rss/slow", {n: 0 for n in range(0, 31)})
    assert dead_sources.find_dead(editions, TODAY, 7) == [("agents", "rss/slow", 30)]


def test_invalid_dead_after_days_is_logged_and_ignored(editions, caplog):
    _record(editions, "agents", "rss/bad", ZERO_WEEK)
    with caplog.at_level(logging.WARNING, logger="dead_sources"):
        found = dead_sources.find_dead(editions, TODAY, 7)
    assert found == [("agents", "rss/bad", 7)]
    assert "invalid dead_after_days 0" in caplog.text


def test_source_removed_from_sources_yaml_is_not_reported(editions):
    _record(editions, "agents", "rss/retired", ZERO_WEEK)
    assert dead_sources.find_dead(editions, TODAY, 7) == []


def test_find_dead_skips_paused_editions_and_missing_state_db(editions):
    _record(editions, "gamma", "rss/quiet", ZERO_WEEK)
    assert dead_sources.find_dead(editions, TODAY, 7) == []  # gamma paused, others have no db


def test_find_dead_skips_a_db_without_the_outcome_table(editions):
    path = editions / "editions" / "agents" / "state.db"
    path.parent.mkdir(parents=True)
    sqlite3.connect(path).execute("CREATE TABLE meta (key TEXT)").connection.close()
    assert dead_sources.find_dead(editions, TODAY, 7) == []


def test_configured_thresholds_is_none_for_a_missing_file(tmp_path):
    assert dead_sources.configured_thresholds(tmp_path / "nope.yaml", 7) is None


def test_configured_thresholds_is_none_for_invalid_yaml(tmp_path):
    f = tmp_path / "sources.yaml"
    f.write_text("rss: [unclosed\n")
    assert dead_sources.configured_thresholds(f, 7) is None


@pytest.mark.parametrize("text", ["", "- just\n- a list\n", "plain scalar\n"])
def test_configured_thresholds_is_none_when_top_level_is_not_a_mapping(tmp_path, text):
    f = tmp_path / "sources.yaml"
    f.write_text(text)
    assert dead_sources.configured_thresholds(f, 7) is None


def test_configured_thresholds_skips_non_mapping_entries(tmp_path):
    f = tmp_path / "sources.yaml"
    f.write_text("rss:\n  - just-a-string\n  - id: ok\n    url: https://example.com/f\n")
    assert dead_sources.configured_thresholds(f, 7) == {"rss/ok": 7}


def test_find_dead_skips_an_edition_with_unreadable_sources_yaml(editions):
    _record(editions, "agents", "rss/quiet", ZERO_WEEK)
    (dead_sources.edition.EDITIONS_DIR / "agents" / "sources.yaml").write_text("rss: [unclosed\n")
    assert dead_sources.find_dead(editions, TODAY, 7) == []


def test_fetch_outcomes_is_read_only(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    before = path.read_bytes()
    dead_sources.fetch_outcomes(path)
    assert path.read_bytes() == before


def test_main_prints_dead_lines_and_exits_1(editions, capsys):
    _record(editions, "agents", "rss/quiet", ZERO_WEEK)
    rc = dead_sources.main(["--today", TODAY.isoformat()])
    assert capsys.readouterr().out == "DEAD: agents/rss/quiet no items for 7 days\n"
    assert rc == 1


def test_main_exits_0_when_nothing_is_dead(editions, capsys):
    assert dead_sources.main(["--today", TODAY.isoformat()]) == 0
    assert capsys.readouterr().out == ""


def test_main_days_sets_the_default_threshold(editions, capsys):
    _record(editions, "agents", "rss/quiet", {n: 0 for n in range(0, 4)})
    assert dead_sources.main(["--days", "3", "--today", TODAY.isoformat()]) == 1
    assert capsys.readouterr().out == "DEAD: agents/rss/quiet no items for 3 days\n"


def test_main_accepts_a_one_day_threshold(editions, capsys):
    """--days 1 is the smallest valid threshold: a source empty today is dead."""
    _record(editions, "agents", "rss/quiet", {0: 0})
    assert dead_sources.main(["--days", "1", "--today", TODAY.isoformat()]) == 1
    assert capsys.readouterr().out == "DEAD: agents/rss/quiet no items for 1 days\n"


def test_main_rejects_non_positive_days(editions):
    with pytest.raises(SystemExit) as exc:
        dead_sources.main(["--days", "0"])
    assert exc.value.code == 2


def test_default_threshold_is_seven_days():
    assert dead_sources.DEFAULT_DAYS == 7


# ─── wired into scripts/watchdog.sh ───────────────────────────────────────

def test_watchdog_check_prints_dead_lines_before_status(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in ("watchdog.sh", "edition.py", "models.py", "db.py", "urls.py",
                 "missed_days.py", "dead_sources.py", "logsetup.py", "runlog.sh", "runlock.sh",
                 "contentbranch.sh", "editionlist.sh"):
        shutil.copy2(REAL / "scripts" / name, repo / "scripts" / name)
    (repo / "run.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    ed = repo / "editions" / "agents"
    ed.mkdir(parents=True)
    (ed / "edition.yaml").write_text(EDITION_YAML.format(slug="agents", status="live", order=10))
    (ed / "sources.yaml").write_text("rss:\n  - id: quiet\n    url: https://example.com/feed\n")
    today = date.today()
    path = ed / "state.db"
    db_mod.init_db(path)
    conn = db_mod.connect(path)
    for n in range(0, 10):
        d = (today - timedelta(days=n)).isoformat()
        db_mod.record_source_fetch(conn, d, "rss/quiet", items_seen=0, items_new=0,
                                   error=None, fetched_at=f"{d}T07:00:00Z")
    conn.close()

    p = subprocess.run(
        ["bash", str(repo / "scripts" / "watchdog.sh"), "--check"], cwd=repo,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "NEWSLETTER_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=60,
    )
    lines = p.stdout.strip().splitlines()
    assert "DEAD: agents/rss/quiet no items for 7 days" in lines
    assert lines[-1].startswith("STATUS: ")
    assert p.returncode == 1  # no run log for today: unchanged behaviour
