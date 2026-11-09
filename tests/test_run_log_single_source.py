"""The run-log contract lives only in ``scripts/runlog.sh``.

``scripts/run_edition.sh`` writes ``logs/<slug>/run-<date>.log`` and ends a
good run with ``<ts> [run] DONE``, a failed one with ``FAILED at stage=<stage>``.
``run.sh`` and ``scripts/watchdog.sh`` read those markers back to tell done,
failed-at-stage and never-ran apart. Each script used to spell the path, the
markers, the regexes, ``ts``/``log`` and ``notify`` by hand, and the watchdog
tests faked the markers inline, so a one-sided reword (or a stage name outside
the reader's character class) made the watchdog re-run good editions or report
``stage=unknown`` with nothing failing.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "runlog.sh"
USERS = (REPO / "run.sh", REPO / "scripts" / "run_edition.sh", REPO / "scripts" / "watchdog.sh")
SHELL = sorted({*REPO.glob("*.sh"), *(REPO / "scripts").glob("*.sh")} - {LIB})


def _sh(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"; {script}', "_", *args],
        capture_output=True, text=True, timeout=10,
    )


def _stages() -> set[str]:
    """Every literal CURRENT_STAGE plus every run_stage label in the writers."""
    out: set[str] = set()
    for p in (REPO / "run.sh", REPO / "scripts" / "run_edition.sh"):
        text = p.read_text()
        out |= set(re.findall(r'^\s*CURRENT_STAGE="([^"$]+)"', text, re.M))
        out |= set(re.findall(r'^run_stage "([^"]+)"', text, re.M))
    return out


def test_stage_list_is_not_empty():
    stages = _stages()
    assert {"fetch", "prefilter", "rank", "write", "publish", "git", "config"} <= stages


@pytest.mark.parametrize("stage", sorted(_stages()))
def test_failed_marker_round_trips_every_stage(tmp_path, stage):
    f = tmp_path / "run.log"
    r = _sh('runlog_mark_failed "$1" 7 "— see x" >> "$2"; runlog_failed_stage "$2"', stage, str(f))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == stage
    assert _sh('runlog_is_complete "$1"', str(f)).returncode == 1


def test_done_marker_round_trips(tmp_path):
    f = tmp_path / "run.log"
    assert _sh('log "── fetch ── ok" >> "$1"; runlog_mark_done >> "$1"', str(f)).returncode == 0
    assert _sh('runlog_is_complete "$1"', str(f)).returncode == 0
    # A DONE log with no FAILED line has no failed stage.
    assert _sh('runlog_failed_stage "$1"', str(f)).returncode == 1


def test_last_failure_wins_and_missing_file_has_none(tmp_path):
    f = tmp_path / "run.log"
    _sh('runlog_mark_failed fetch 1 >> "$1"; runlog_mark_failed rank 2 >> "$1"', str(f))
    assert _sh('runlog_failed_stage "$1"', str(f)).stdout.strip() == "rank"
    missing = str(tmp_path / "nope.log")
    assert _sh('runlog_failed_stage "$1"', missing).returncode == 1
    assert _sh('runlog_is_complete "$1"', missing).returncode == 1


def test_done_must_be_a_whole_run_line(tmp_path):
    f = tmp_path / "run.log"
    f.write_text("2026-01-01T00:00:00Z [run] DONE: seen=3\n"
                 "2026-01-01 00:00:00 INFO fetch: DONE\n")
    assert _sh('runlog_is_complete "$1"', str(f)).returncode == 1


def test_lines_and_paths():
    r = _sh('log hi; runlog_mark_done; runlog_mark_failed rank 3 "— see x"; '
            'runlog_dir /r; runlog_dir /r s; runlog_path /r s 2026-01-02; '
            'runlog_summary_path /r 2026-01-02')
    lines = r.stdout.splitlines()
    ts = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"
    assert re.fullmatch(ts + r" \[run\] hi", lines[0])
    assert re.fullmatch(ts + r" \[run\] DONE", lines[1])
    assert re.fullmatch(ts + r" \[run\] FAILED at stage=rank rc=3 — see x", lines[2])
    assert lines[3:] == ["/r/logs", "/r/logs/s", "/r/logs/s/run-2026-01-02.log",
                         "/r/logs/run-2026-01-02.log"]


@pytest.mark.parametrize("path", USERS, ids=lambda p: p.name)
def test_users_source_the_library(path):
    assert re.search(r'^\. "\$REPO_ROOT/scripts/runlog\.sh"$', path.read_text(), re.M)


# Helpers the three run-log scripts must take from scripts/runlog.sh. (The root
# watchdog.sh is a separate stale-commit alarm with its own multi-channel
# notify; it never reads or writes a run log, so only MARKERS apply to it.)
HELPERS = {
    "ts format": re.compile(r"date -u \+"),
    "ts()/log()/notify() definition": re.compile(r"^\s*(ts|log|notify)\(\)\s*\{", re.M),
    "osascript": re.compile(r"osascript"),
}
# Marker and path spellings no shell script but scripts/runlog.sh may contain.
MARKERS = {
    "DONE marker": re.compile(r'(log|echo|printf)[^\n]*"DONE"|\\\[run\\\] DONE'),
    "FAILED marker": re.compile(r'(log|echo|printf) "FAILED at stage=|grep[^\n]*FAILED at stage='),
    "log path": re.compile(r"logs/[^\s\"']*run-\$"),
}


def _hits(path: Path, rules: dict[str, re.Pattern[str]]) -> list[str]:
    text = path.read_text()
    return [name for name, rx in rules.items() if rx.search(text)]


@pytest.mark.parametrize("path", SHELL, ids=lambda p: str(p.relative_to(REPO)))
def test_no_inline_markers(path):
    hits = _hits(path, MARKERS)
    assert not hits, f"{path.name} spells the run-log contract inline: {hits}; use scripts/runlog.sh"


@pytest.mark.parametrize("path", USERS, ids=lambda p: p.name)
def test_no_inline_helpers(path):
    hits = _hits(path, HELPERS)
    assert not hits, f"{path.name} redefines run-log helpers: {hits}; use scripts/runlog.sh"


def test_library_holds_every_forbidden_spelling():
    # Keeps the rules honest: each pattern must match the one real home.
    rules = {**HELPERS, **MARKERS}
    text = LIB.read_text()
    missing = [n for n, rx in rules.items() if n != "log path" and not rx.search(text)]
    assert not missing


@pytest.mark.parametrize("name,text", [
    ("DONE marker", 'log "DONE"'),
    ("DONE marker", "grep -q '^\\S* \\[run\\] DONE$' f"),
    ("FAILED marker", 'log "FAILED at stage=$s rc=$rc"'),
    ("FAILED marker", "grep -oE 'FAILED at stage=[a-z-]+' f"),
    ("log path", 'f="$REPO_ROOT/logs/$slug/run-$DATE_TODAY.log"'),
])
def test_marker_rules_catch_the_old_inline_spellings(name, text):
    assert MARKERS[name].search(text)
