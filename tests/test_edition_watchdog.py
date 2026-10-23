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
FAKE_RUN = r'''#!/usr/bin/env bash
echo "$@" >> "$(dirname "$0")/run-calls.txt"
slugs=()
while [ "$#" -gt 0 ]; do
  [ "$1" = "--edition" ] && { slugs+=("$2"); shift; }
  shift
done
for s in "${slugs[@]}"; do
  d="logs/$s"; mkdir -p "$d"; f="$d/run-$(date +%F).log"
  b=""
  for pair in $RUN_BEHAVIOUR; do [ "${pair%%=*}" = "$s" ] && b="${pair#*=}"; done
  case "$b" in
    ok) echo "2026-01-01T00:00:00Z [run] DONE" >> "$f" ;;
    fail:*) echo "2026-01-01T00:00:00Z [run] FAILED at stage=${b#fail:} rc=7" >> "$f" ;;
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
    shutil.copy2(REAL / "scripts" / "edition.py", r / "scripts" / "edition.py")
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
