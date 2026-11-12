"""scripts/watchdog.sh: per-edition EDITION: lines and the worst-of STATUS: last line."""
from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REAL = Path(__file__).resolve().parents[1]

EDITION_YAML = (
    "slug: {slug}\nname: {slug}\ntagline: t\nstatus: {status}\norder: {order}\n"
    "reader_profile: r\nkeywords: ['x']\ntags: [a]\nsections:\n"
    "  papers: {{featured_min: 7, appendix_min: 5, cap: 5}}\n"
    "  news: {{featured_min: 6, appendix_min: 4, cap: 6}}\n"
    "  blogs: {{featured_min: 6, appendix_min: 4, cap: 6}}\n"
)

# Stand-in for run.sh: for each --edition X, write that edition's log according to
# $RUN_BEHAVIOUR ("X=ok", "X=fail:rank", "X=silent"), and count invocations.
# The markers go through the real writer helpers in scripts/runlog.sh, so these
# tests follow whatever run_edition.sh actually emits.
FAKE_RUN = r'''#!/usr/bin/env bash
echo "$@" >> "$(dirname "$0")/run-calls.txt"
. "$(dirname "$0")/scripts/runlog.sh"
slugs=()
while [ "$#" -gt 0 ]; do
  [ "$1" = "--edition" ] && { slugs+=("$2"); shift; }
  shift
done
for s in "${slugs[@]}"; do
  f="$(runlog_path "$PWD" "$s" "$(date +%F)")"; mkdir -p "$(dirname "$f")"
  b=""
  for pair in $RUN_BEHAVIOUR; do [ "${pair%%=*}" = "$s" ] && b="${pair#*=}"; done
  case "$b" in
    ok) runlog_mark_done >> "$f" ;;
    fail:*) runlog_mark_failed "${b#fail:}" 7 >> "$f" ;;
    silent) : ;;
  esac
done
exit ${RUN_RC:-0}
'''


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "scripts").mkdir(parents=True)
    shutil.copy2(REAL / "scripts" / "watchdog.sh", r / "scripts" / "watchdog.sh")
    shutil.copy2(REAL / "scripts" / "runlog.sh", r / "scripts" / "runlog.sh")  # watchdog sources it
    shutil.copy2(REAL / "scripts" / "runlock.sh", r / "scripts" / "runlock.sh")  # and this
    shutil.copy2(REAL / "scripts" / "contentbranch.sh", r / "scripts" / "contentbranch.sh")
    shutil.copy2(REAL / "scripts" / "editionlist.sh", r / "scripts" / "editionlist.sh")
    shutil.copy2(REAL / "scripts" / "edition.py", r / "scripts" / "edition.py")
    shutil.copy2(REAL / "scripts" / "models.py", r / "scripts" / "models.py")  # edition.py imports it
    (r / "run.sh").write_text(FAKE_RUN)
    for slug, status, order in (("agents", "live", 10), ("beta", "draft", 20), ("gamma", "paused", 30)):
        (r / "editions" / slug).mkdir(parents=True)
        (r / "editions" / slug / "edition.yaml").write_text(
            EDITION_YAML.format(slug=slug, status=status, order=order))
    return r


def _today():
    return subprocess.run(["date", "+%F"], capture_output=True, text=True).stdout.strip()


def _log(repo, slug, *lines):
    d = repo / "logs" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / f"run-{_today()}.log").write_text("".join(l + "\n" for l in lines))


DONE = "2026-01-01T00:00:00Z [run] DONE"


def _wd(repo, *args, **env):
    e = {**os.environ, "NEWSLETTER_PYTHON": sys.executable, **env}
    p = subprocess.run(["bash", str(repo / "scripts" / "watchdog.sh"), *args],
                       cwd=repo, env=e, capture_output=True, text=True, timeout=60)
    lines = p.stdout.strip().splitlines()
    # `say` progress lines share stdout; the contract is the EDITION:/STATUS: lines,
    # with STATUS: last overall.
    assert not lines or lines[-1].startswith("STATUS: ") or "--help" in args or "--bogus" in args
    return p, [l for l in lines if l.startswith(("EDITION: ", "STATUS: "))]


def _calls(repo):
    f = repo / "scripts" / "run-calls.txt"
    f2 = repo / "run-calls.txt"
    return (f2.read_text() if f2.exists() else f.read_text() if f.exists() else "").splitlines()


def test_all_editions_done_is_already_ran(repo):
    d = _today()
    _log(repo, "agents", DONE)
    _log(repo, "beta", DONE)
    p, out = _wd(repo)
    assert p.returncode == 0
    assert out == [f"EDITION: agents ALREADY_RAN {d}", f"EDITION: beta ALREADY_RAN {d}", f"STATUS: ALREADY_RAN {d}"]
    assert _calls(repo) == []


def test_paused_editions_are_not_checked(repo):
    _log(repo, "agents", DONE)
    _log(repo, "beta", DONE)
    p, out = _wd(repo, "--check")
    assert p.returncode == 0
    assert not any("gamma" in l for l in out)


def test_missing_edition_is_healed_by_running_only_that_edition(repo):
    d = _today()
    _log(repo, "agents", DONE)
    p, out = _wd(repo, RUN_BEHAVIOUR="beta=ok")
    assert p.returncode == 0, p.stderr
    assert out == [f"EDITION: agents ALREADY_RAN {d}", f"EDITION: beta HEALED {d}", f"STATUS: HEALED {d}"]
    assert _calls(repo) == ["--edition beta"]


def test_failed_edition_is_named_with_its_stage(repo):
    d = _today()
    _log(repo, "agents", DONE)
    p, out = _wd(repo, RUN_BEHAVIOUR="beta=fail:rank", RUN_RC="1")
    assert p.returncode == 1
    assert out[-2] == f"EDITION: beta FAILED {d} rank"
    assert out[-1] == f"STATUS: FAILED {d} beta:rank"


def test_two_failures_are_both_named_on_the_status_line(repo):
    d = _today()
    p, out = _wd(repo, RUN_BEHAVIOUR="agents=fail:fetch beta=fail:write", RUN_RC="1")
    assert p.returncode == 1
    assert out[-1] == f"STATUS: FAILED {d} agents:fetch beta:write"
    assert _calls(repo) == ["--edition agents --edition beta"]


def test_one_healed_one_failed_is_worst_of_failed(repo):
    d = _today()
    p, out = _wd(repo, RUN_BEHAVIOUR="agents=ok beta=fail:rank", RUN_RC="1")
    assert out[0] == f"EDITION: agents HEALED {d}"
    assert out[-1] == f"STATUS: FAILED {d} beta:rank"


def test_exit_zero_without_done_marker_is_not_success(repo):
    d = _today()
    _log(repo, "agents", DONE)
    p, out = _wd(repo, RUN_BEHAVIOUR="beta=silent", RUN_RC="0")
    assert p.returncode == 1
    assert out[-2] == f"EDITION: beta FAILED {d} no-done-marker"
    assert out[-1] == f"STATUS: FAILED {d} beta:no-done-marker"


def test_done_marker_wins_over_nonzero_run_exit_code(repo):
    # Editions are judged by their own log, not by run.sh's exit code: a DONE
    # marker means HEALED even if run.sh exited non-zero afterwards.
    d = _today()
    _log(repo, "agents", DONE)
    p, out = _wd(repo, RUN_BEHAVIOUR="beta=ok", RUN_RC="9")
    assert p.returncode == 0
    assert out == [f"EDITION: agents ALREADY_RAN {d}", f"EDITION: beta HEALED {d}", f"STATUS: HEALED {d}"]


def test_failure_report_includes_run_sh_exit_code(repo):
    _log(repo, "agents", DONE)
    p, _ = _wd(repo, RUN_BEHAVIOUR="beta=fail:rank", RUN_RC="7")
    assert "run.sh rc=7; failed editions: beta:rank" in p.stdout


def test_log_without_done_or_failed_marker_after_run_is_no_done_marker(repo):
    d = _today()
    _log(repo, "agents", DONE)
    _log(repo, "beta", "2026-01-01T00:00:00Z [run] ── fetch ── start")
    p, out = _wd(repo, RUN_BEHAVIOUR="beta=silent")
    assert out[-1] == f"STATUS: FAILED {d} beta:no-done-marker"


def test_run_in_flight_leaves_everything_alone(repo):
    d = _today()
    _log(repo, "agents", DONE)
    with open(repo / ".watchdog.lock", "w") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        p, out = _wd(repo)
    assert p.returncode == 0
    assert out == [f"EDITION: agents ALREADY_RAN {d}", f"EDITION: beta IN_PROGRESS {d}", f"STATUS: IN_PROGRESS {d}"]
    assert _calls(repo) == []


def test_check_only_reports_without_running(repo):
    d = _today()
    _log(repo, "agents", "2026-01-01T00:00:00Z [run] FAILED at stage=publish rc=3")
    p, out = _wd(repo, "--check")
    assert p.returncode == 1
    assert out[-3:] == [
        f"EDITION: agents FAILED {d} publish",
        f"EDITION: beta FAILED {d} never-started",
        f"STATUS: FAILED {d} agents:publish beta:never-started",
    ]
    assert _calls(repo) == []


def test_concurrent_watchdog_stands_down(repo):
    d = _today()
    with open(repo / ".watchdog-self.lock", "w") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        p, out = _wd(repo)
    assert p.returncode == 1
    assert out[-1] == f"STATUS: FAILED {d} concurrent-run"
    assert _calls(repo) == []


def test_no_editions_found_is_a_failure(repo):
    d = _today()
    shutil.rmtree(repo / "editions")
    (repo / "editions").mkdir()
    p, out = _wd(repo)
    assert p.returncode == 1
    assert out[-1] == f"STATUS: FAILED {d} no-editions"


def test_invalid_edition_surfaces_as_failed(repo):
    d = _today()
    (repo / "editions" / "beta" / "edition.yaml").write_text("bogus: 1\n")
    _log(repo, "agents", DONE)
    p, out = _wd(repo, "--check")
    assert out[-1] == f"STATUS: FAILED {d} beta:never-started"


def test_help_and_unknown_arg(repo):
    p, _ = _wd(repo, "--help")
    assert p.returncode == 0 and "EDITION: <slug>" in p.stdout
    p, _ = _wd(repo, "--bogus")
    assert p.returncode == 2


def test_python_falls_back_to_venv_then_python3(repo):
    d = _today()
    _log(repo, "agents", DONE)
    _log(repo, "beta", DONE)
    env = {**os.environ}
    env.pop("NEWSLETTER_PYTHON", None)
    (repo / ".venv" / "bin").mkdir(parents=True)
    # A wrapper, not a symlink: a symlinked venv interpreter loses its site-packages.
    wrapper = repo / ".venv" / "bin" / "python"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    wrapper.chmod(0o755)
    p = subprocess.run(["bash", str(repo / "scripts" / "watchdog.sh")], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert p.stdout.strip().splitlines()[-1] == f"STATUS: ALREADY_RAN {d}"
    shutil.rmtree(repo / ".venv")
    p = subprocess.run(["bash", str(repo / "scripts" / "watchdog.sh")], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert p.stdout.strip().splitlines()[-1] == f"STATUS: ALREADY_RAN {d}"


# ─── log parsing: which stage died, what counts as DONE, which day's log ────

def test_last_failed_marker_names_the_stage(repo):
    """A log from a retried run has several FAILED lines; the latest one is the stage."""
    d = _today()
    _log(repo, "agents", DONE)
    _log(repo, "beta",
         "2026-01-01T00:00:00Z [run] FAILED at stage=fetch rc=1",
         "2026-01-01T00:05:00Z [run] FAILED at stage=publish rc=3")
    p, out = _wd(repo, "--check")
    assert out[-1] == f"STATUS: FAILED {d} beta:publish"


def test_stage_names_with_hyphens_are_reported_whole(repo):
    d = _today()
    _log(repo, "agents", DONE)
    _log(repo, "beta", "2026-01-01T00:00:00Z [run] FAILED at stage=run-attempt rc=2")
    p, out = _wd(repo, "--check")
    assert f"EDITION: beta FAILED {d} run-attempt" in out


def test_log_without_failed_marker_is_unknown_stage_when_only_checking(repo):
    """A started-but-unfinished log with no FAILED line reports stage 'unknown', not never-started."""
    d = _today()
    _log(repo, "agents", DONE)
    _log(repo, "beta", "2026-01-01T00:00:00Z [run] ── fetch ── start")
    p, out = _wd(repo, "--check")
    assert p.returncode == 1
    assert out[-2:] == [f"EDITION: beta FAILED {d} unknown", f"STATUS: FAILED {d} beta:unknown"]


@pytest.mark.parametrize("line", [
    "2026-01-01T00:00:00Z [run] DONE with warnings",   # trailing text: not the marker
    "2026-01-01T00:00:00Z [run] NOT DONE",              # DONE is not the whole message
    "[run] DONE",                                       # no timestamp prefix
    "2026-01-01T00:00:00Z [fetch] DONE",                # a stage's own DONE, not the run's
])
def test_only_the_exact_run_done_line_marks_a_log_complete(repo, line):
    d = _today()
    _log(repo, "agents", DONE)
    _log(repo, "beta", line)
    p, out = _wd(repo, "--check")
    assert p.returncode == 1
    assert out[-1] == f"STATUS: FAILED {d} beta:unknown"


def test_done_marker_anywhere_in_the_log_completes_it(repo):
    """A run that logged DONE and then more lines (e.g. a later push note) is still complete."""
    d = _today()
    _log(repo, "agents", DONE)
    _log(repo, "beta", "2026-01-01T00:00:00Z [run] FAILED at stage=fetch rc=1", DONE,
         "2026-01-01T00:00:09Z [run] note: pushed")
    p, out = _wd(repo, "--check")
    assert p.returncode == 0
    assert out[-1] == f"STATUS: ALREADY_RAN {d}"


def test_yesterdays_completed_log_does_not_count_for_today(repo):
    d = _today()
    _log(repo, "agents", DONE)
    old = repo / "logs" / "beta"
    old.mkdir(parents=True)
    (old / "run-2000-01-01.log").write_text(DONE + "\n")
    p, out = _wd(repo, "--check")
    assert p.returncode == 1
    assert out[-1] == f"STATUS: FAILED {d} beta:never-started"


# ─── CONTENT_ROOT handed to missed_days.py / dead_sources.py ────────────────

# Stand-ins that report the CONTENT_ROOT the watchdog exported to them, as a
# finding line under the reader's own prefix (exit 1), so they keep the
# readers' output contract (#673).
ECHO_ROOT = ('#!/usr/bin/env python3\nimport os, sys\n'
             'print({prefix!r} + " ROOT=" + os.environ.get("CONTENT_ROOT", ""))\nsys.exit(1)\n')


def _with_echo_scripts(repo):
    for name, prefix in (("missed_days.py", "MISSED:"), ("dead_sources.py", "DEAD:")):
        (repo / "scripts" / name).write_text(ECHO_ROOT.format(prefix=prefix))


def _roots(p):
    return [l.split(" ", 1)[1] for l in p.stdout.splitlines()
            if l.startswith(("MISSED: ROOT=", "DEAD: ROOT="))]


def _clean_env():
    e = {k: v for k, v in os.environ.items() if k != "CONTENT_ROOT"}
    return e


def _run_check(repo, **env):
    e = {**_clean_env(), "NEWSLETTER_PYTHON": sys.executable, **env}
    return subprocess.run(["bash", str(repo / "scripts" / "watchdog.sh"), "--check"],
                          cwd=repo, env=e, capture_output=True, text=True, timeout=60)


def test_content_worktree_is_used_when_present(repo):
    _with_echo_scripts(repo)
    wt = repo / ".worktrees" / "content"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere\n")
    p = _run_check(repo)
    assert _roots(p) == [f"ROOT={wt}", f"ROOT={wt}"]


def test_content_root_is_empty_without_worktree_or_override(repo):
    _with_echo_scripts(repo)
    p = _run_check(repo)
    assert _roots(p) == ["ROOT=", "ROOT="]


def test_directory_without_git_entry_is_not_a_content_worktree(repo):
    _with_echo_scripts(repo)
    (repo / ".worktrees" / "content").mkdir(parents=True)
    p = _run_check(repo)
    assert _roots(p) == ["ROOT=", "ROOT="]


def test_explicit_content_root_wins_over_the_worktree(repo, tmp_path):
    _with_echo_scripts(repo)
    wt = repo / ".worktrees" / "content"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere\n")
    other = tmp_path / "elsewhere"
    p = _run_check(repo, CONTENT_ROOT=str(other))
    assert _roots(p) == [f"ROOT={other}", f"ROOT={other}"]


def test_crashing_informational_scripts_do_not_change_status(repo):
    """missed_days / dead_sources are informational: a crash must not alter STATUS or the exit code."""
    d = _today()
    for name in ("missed_days.py", "dead_sources.py"):
        (repo / "scripts" / name).write_text("import sys\nprint('boom')\nsys.exit(9)\n")
    _log(repo, "agents", DONE)
    _log(repo, "beta", DONE)
    p, out = _wd(repo, "--check")
    assert p.returncode == 0
    assert out == [f"EDITION: agents ALREADY_RAN {d}", f"EDITION: beta ALREADY_RAN {d}", f"STATUS: ALREADY_RAN {d}"]


# ─── a failing MISSED:/DEAD: reader is reported, not read as "nothing found" (#673) ───

CRASHING_READER = "raise RuntimeError('db schema drift')\n"


def _check_lines(p, prefix):
    return [l for l in p.stdout.splitlines() if l.startswith(prefix)]


@pytest.mark.parametrize("reader", ["missed_days.py", "dead_sources.py"])
def test_crashing_reader_prints_check_failed(repo, reader):
    _with_echo_scripts(repo)
    (repo / "scripts" / reader).write_text(CRASHING_READER)
    p = _run_check(repo)
    assert _check_lines(p, "CHECK-FAILED: ") == [
        f"CHECK-FAILED: {reader} rc=1: RuntimeError: db schema drift"]
    # Informational only: same STATUS and exit code as with working readers.
    assert p.stdout.strip().splitlines()[-1] == f"STATUS: FAILED {_today()} agents:never-started beta:never-started"
    assert p.returncode == 1


@pytest.mark.parametrize("out, rc, ok", [
    ("", 0, True),
    ("MISSED: agents 2026-01-01\\nMISSED: beta 2026-01-02", 1, True),
    ("", 1, False),                                   # crash with no lines
    ("MISSED: agents 2026-01-01", 0, False),          # findings must exit 1
    ("MISSED: agents 2026-01-01\\nTraceback", 1, False),
    ("", 2, False),                                   # argparse error
])
def test_reader_contract(repo, out, rc, ok):
    (repo / "scripts" / "missed_days.py").write_text(
        f"import sys\nprint({out!r}.replace('\\\\n', '\\n'), end='')\n"
        f"print('oops', file=sys.stderr)\nsys.exit({rc})\n")
    (repo / "scripts" / "dead_sources.py").write_text("")
    p = _run_check(repo)
    failed = _check_lines(p, "CHECK-FAILED: ")
    if ok:
        assert failed == []
        assert _check_lines(p, "MISSED: ") == [l for l in out.split("\\n") if l]
    else:
        assert failed == [f"CHECK-FAILED: missed_days.py rc={rc}: oops"]
        assert _check_lines(p, "MISSED: ") == []
