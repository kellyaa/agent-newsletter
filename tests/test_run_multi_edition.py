"""run.sh orchestrator + scripts/run_edition.sh, end to end on a fixture repo.

The fixture is a real git repo with a bare remote that has `main` and `content`
branches, the real orchestration scripts, and stub pipeline stages (fetch,
prefilter, rank, write, publish) that either succeed or fail on demand via
FAIL_EDITION / FAIL_STAGE. No network, no LLM.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REAL = Path(__file__).resolve().parents[1]
DATE = "2026-10-22"

EDITION_YAML = """\
slug: {slug}
name: {name}
tagline: tagline for {slug}
status: {status}
order: {order}
reader_profile: A reader.
keywords: ['\\bfoo\\b']
tags: [alpha]
sections:
  papers: {{featured_min: 7, appendix_min: 5, cap: 5}}
  news: {{featured_min: 6, appendix_min: 4, cap: 6}}
  blogs: {{featured_min: 6, appendix_min: 4, cap: 6}}
"""

# The real content-branch .gitignore (origin/content): a whitelist that ignores
# `editions/` until migrate_layout.ensure_gitignore() adds it.
CONTENT_GITIGNORE = """\
/*
!/state.db
!/site
/site/*
!/site/src
/site/src/*
!/site/src/content
/site/src/content/*
!/site/src/content/issues
!/.gitignore
!/.github
/.github/*
!/.github/workflows
/.github/workflows/*
!/.github/workflows/deploy.yml
"""

STAGE_STUB = '''\
import os, sys
from datetime import datetime
from pathlib import Path

STAGE = Path(__file__).stem
slug = os.environ["EDITION"]
if os.environ.get("FAIL_EDITION") == slug and os.environ.get("FAIL_STAGE") == STAGE:
    print(f"stub {STAGE} failing for {slug}", file=sys.stderr)
    sys.exit(7)
if STAGE == "write":
    from db import CONTENT_ROOT
    import edition
    d = edition.runtime_paths(CONTENT_ROOT).issues_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{os.environ['RUN_DATE']}.md").write_text("issue for " + slug)
'''



def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=check,
    )


def _write_edition(root: Path, slug: str, *, status="live", order=20, name=None):
    d = root / "editions" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "edition.yaml").write_text(
        EDITION_YAML.format(slug=slug, name=name or slug.title(), status=status, order=order)
    )


@pytest.fixture()
def fx(tmp_path):
    """(repo, env) for a clone with editions agents(10) and beta(20)."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.name", "T")
    _git(seed, "config", "user.email", "t@example.com")
    shutil.copy2(REAL / "run.sh", seed / "run.sh")
    (seed / "scripts").mkdir()
    for f in (REAL / "scripts").iterdir():
        if f.suffix in (".py", ".sh"):
            shutil.copy2(f, seed / "scripts" / f.name)
    for stage in ("fetch", "prefilter", "rank", "write", "publish"):
        (seed / "scripts" / f"{stage}.py").write_text(STAGE_STUB)
    _write_edition(seed, "agents", order=10, name="Agents Daily")
    _write_edition(seed, "beta", order=20, name="Beta Daily")
    _write_edition(seed, "gamma", status="paused", order=30)
    (seed / "editions" / "_template").mkdir()
    (seed / ".gitignore").write_text("logs/\n.worktrees/\n.watchdog.lock\n.watchdog-self.lock\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "main")
    _git(seed, "checkout", "-q", "--orphan", "content")
    _git(seed, "rm", "-rqf", ".")
    (seed / "README").write_text("content\n")
    (seed / ".gitignore").write_text(CONTENT_GITIGNORE)
    _git(seed, "add", "-f", "README", ".gitignore")
    _git(seed, "commit", "-qm", "content init")
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(remote)], check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(repo)], check=True)
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.com")
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "NEWSLETTER_PYTHON": sys.executable,
        "RUN_DATE": DATE,
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    for k in ("EDITION", "CONTENT_ROOT", "FAIL_EDITION", "FAIL_STAGE"):
        env.pop(k, None)
    return repo, remote, env


_PINNED_TODAY: str | None = None


def _run(repo: Path, env: dict, *args: str, **extra) -> subprocess.CompletedProcess:
    # run.sh derives the date from `date +%F`; RUN_DATE is exported *by* run.sh,
    # so the log paths below use today's real date. Pin that date at launch so a
    # UTC-midnight rollover between run.sh and the later assertions can't make
    # _today() disagree with the files run.sh wrote.
    global _PINNED_TODAY
    _PINNED_TODAY = _real_today()
    return subprocess.run(
        ["bash", str(repo / "run.sh"), *args], cwd=repo, env={**env, **extra},
        capture_output=True, text=True, timeout=120,
    )


def _real_today() -> str:
    return subprocess.run(["date", "+%F"], capture_output=True, text=True).stdout.strip()


def _today() -> str:
    """The date of the most recent run.sh launch (falls back to the real date)."""
    return _PINNED_TODAY or _real_today()


def _remote_files(remote: Path, branch="content") -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(remote), "ls-tree", "-r", "--name-only", branch],
        capture_output=True, text=True, check=True,
    ).stdout
    return set(out.split())


def _remote_log(remote: Path, branch="content") -> str:
    return subprocess.run(
        ["git", "-C", str(remote), "log", "--format=%s", branch],
        capture_output=True, text=True, check=True,
    ).stdout


def _attempts(repo: Path, slug: str, env: dict) -> list[tuple]:
    import sqlite3
    db = repo / ".worktrees" / "content" / "editions" / slug / "state.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT attempt, outcome, failed_stage FROM run_attempts ORDER BY attempt"
        ).fetchall()
    finally:
        conn.close()


@pytest.fixture()
def fake_notifier(fx):
    """A fake osascript on PATH that records each notification's text."""
    repo, remote, env = fx
    bin_dir = repo.parent / "bin"
    bin_dir.mkdir()
    log = repo.parent / "notifications.log"
    script = bin_dir / "osascript"
    script.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    script.chmod(0o755)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return log


def test_second_edition_failing_does_not_block_the_first(fx, fake_notifier):
    repo, remote, env = fx
    r = _run(repo, env, FAIL_EDITION="beta", FAIL_STAGE="rank")
    assert r.returncode == 1, r.stdout + r.stderr
    files = _remote_files(remote)
    # The first edition was committed AND pushed, with its issue.
    assert f"editions/agents/issues/{_today()}.md" in files
    assert "editions/agents/state.db" in files
    # The failed one pushed only its state.db (with the failed attempt row).
    assert "editions/beta/state.db" in files
    assert not [f for f in files if f.startswith("editions/beta/issues/")]
    assert "newsletter(agents):" in _remote_log(remote)
    assert "newsletter(beta): " in _remote_log(remote) and "failed at rank" in _remote_log(remote)
    assert _attempts(repo, "agents", env) == [(1, "ok", None)]
    assert _attempts(repo, "beta", env) == [(1, "failed", "rank")]
    # One notification, naming the edition, the stage and the re-run command.
    notes = fake_notifier.read_text().strip().splitlines()
    assert len(notes) == 1
    assert "Beta Daily failed" in notes[0] and "stage=rank" in notes[0] and "./run.sh --failed" in notes[0]
    # Logs: orchestrator summary + one per edition, ending in DONE only on success.
    top = (repo / "logs" / f"run-{_today()}.log").read_text()
    assert "agents: ok" in top and "beta: FAILED at stage=rank" in top
    assert "gamma" not in top  # paused editions are skipped
    a_log = (repo / "logs" / "agents" / f"run-{_today()}.log").read_text()
    b_log = (repo / "logs" / "beta" / f"run-{_today()}.log").read_text()
    assert a_log.rstrip().endswith("[run] DONE")
    assert "[run] DONE" not in b_log and "FAILED at stage=rank" in b_log
    assert not (repo / "logs" / "gamma").exists()


def test_failed_reruns_only_the_failed_edition(fx):
    repo, remote, env = fx
    assert _run(repo, env, FAIL_EDITION="beta", FAIL_STAGE="write").returncode == 1
    r = _run(repo, env, "--failed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "── editions ── beta" in r.stdout
    assert "── agents ──" not in r.stdout
    assert _attempts(repo, "agents", env) == [(1, "ok", None)]  # untouched
    assert _attempts(repo, "beta", env) == [(1, "failed", "write"), (2, "ok", None)]
    assert f"editions/beta/issues/{_today()}.md" in _remote_files(remote)


def test_failed_with_nothing_failed_exits_zero(fx):
    repo, remote, env = fx
    assert _run(repo, env).returncode == 0
    r = _run(repo, env, "--failed")
    assert r.returncode == 0
    assert "nothing to re-run" in r.stdout


def test_plain_rerun_skips_editions_that_are_ok(fx):
    repo, remote, env = fx
    assert _run(repo, env).returncode == 0
    r = _run(repo, env)
    assert r.returncode == 0
    assert "agents ── already ok today; skipping" in r.stdout
    assert "beta ── already ok today; skipping" in r.stdout
    assert _attempts(repo, "agents", env) == [(1, "ok", None)]


def test_edition_flag_selects_and_allows_paused(fx):
    repo, remote, env = fx
    r = _run(repo, env, "--edition", "gamma")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "── editions ── gamma" in r.stdout
    assert f"editions/gamma/issues/{_today()}.md" in _remote_files(remote)
    assert _attempts(repo, "agents", env) == []


def test_unknown_edition_is_rejected(fx):
    repo, remote, env = fx
    r = _run(repo, env, "--edition", "nope")
    assert r.returncode == 2
    assert "unknown edition 'nope'" in r.stdout


def test_force_reruns_ok_editions(fx):
    repo, remote, env = fx
    assert _run(repo, env, "--edition", "agents").returncode == 0
    r = _run(repo, env, "--force", "--edition", "agents")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FORCE: resetting today's post-fetch state" in r.stdout
    assert _attempts(repo, "agents", env) == [(1, "ok", None), (2, "ok", None)]


def test_refetch_with_edition_runs_the_reset(fx):
    repo, remote, env = fx
    assert _run(repo, env, "--edition", "agents").returncode == 0
    r = _run(repo, env, "--refetch", "--edition", "agents")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "REFETCH: deleting today's fetched items" in r.stdout


def test_invalid_edition_config_fails_that_edition_only(fx, fake_notifier):
    repo, remote, env = fx
    (repo / "editions" / "beta" / "edition.yaml").write_text("slug: beta\nbogus: 1\n")
    r = _run(repo, env)
    assert r.returncode == 1
    assert "beta: FAILED at stage=config" in r.stdout
    assert "agents: ok" in r.stdout
    assert "failed" in fake_notifier.read_text()
    # run_edition.sh stopped before any stage: no attempt row, no stage logs.
    assert "── fetch ── start" not in (repo / "logs" / "beta" / f"run-{_today()}.log").read_text()
    # ... and --failed keeps picking it up, since it never succeeded.
    r2 = _run(repo, env, "--failed")
    assert "── editions ── beta" in r2.stdout


def test_refetch_without_edition_is_rejected(fx):
    repo, remote, env = fx
    r = _run(repo, env, "--refetch")
    assert r.returncode == 2
    assert "--refetch requires --edition" in r.stderr
    assert not (repo / "logs").exists()


def test_failed_and_edition_cannot_be_combined(fx):
    repo, remote, env = fx
    r = _run(repo, env, "--failed", "--edition", "agents")
    assert r.returncode == 2
    assert "cannot be combined" in r.stderr


def test_edition_flag_needs_a_value(fx):
    repo, remote, env = fx
    r = _run(repo, env, "--edition")
    assert r.returncode == 2
    assert "--edition needs a value" in r.stderr


def test_edition_equals_form(fx):
    repo, remote, env = fx
    r = _run(repo, env, "--edition=beta")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "── editions ── beta" in r.stdout


def test_run_edition_requires_edition_env(fx):
    repo, remote, env = fx
    r = subprocess.run(
        ["bash", str(repo / "scripts" / "run_edition.sh")], cwd=repo, env=env,
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "EDITION must be set" in r.stderr


def test_run_edition_rejects_unknown_arg_and_prints_help(fx):
    repo, remote, env = fx
    script = str(repo / "scripts" / "run_edition.sh")
    bad = subprocess.run(["bash", script, "--nope"], cwd=repo, env=env, capture_output=True, text=True)
    assert bad.returncode == 2 and "unknown arg: --nope" in bad.stderr
    helped = subprocess.run(["bash", script, "--help"], cwd=repo, env=env, capture_output=True, text=True)
    assert helped.returncode == 0 and "Run ONE edition" in helped.stdout


def test_run_edition_with_nothing_to_commit_still_finishes(fx):
    repo, remote, env = fx
    assert _run(repo, env, "--edition", "agents").returncode == 0
    wt = repo / ".worktrees" / "content"
    r = subprocess.run(
        ["bash", str(repo / "scripts" / "run_edition.sh")], cwd=repo,
        env={**env, "EDITION": "agents", "CONTENT_WORKTREE": str(wt),
             "NEWSLETTER_PYTHON": sys.executable, "RUN_DATE": _today()},
        capture_output=True, text=True,
    )
    # A second attempt records a new run_attempts row, so there is something to commit;
    # either way it must end in DONE.
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[run] DONE" in r.stdout


def test_run_edition_without_remote_skips_push(fx):
    repo, remote, env = fx
    assert _run(repo, env, "--edition", "agents").returncode == 0
    wt = repo / ".worktrees" / "content"
    _git(wt, "remote", "remove", "origin")
    r = subprocess.run(
        ["bash", str(repo / "scripts" / "run_edition.sh")], cwd=repo,
        env={**env, "EDITION": "agents", "CONTENT_WORKTREE": str(wt),
             "NEWSLETTER_PYTHON": sys.executable, "RUN_DATE": _today()},
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no remote configured; skipping push" in r.stdout


def test_run_edition_push_failure_exits_nonzero(fx):
    repo, remote, env = fx
    assert _run(repo, env, "--edition", "agents").returncode == 0
    wt = repo / ".worktrees" / "content"
    _git(wt, "remote", "set-url", "origin", str(repo.parent / "gone.git"))
    r = subprocess.run(
        ["bash", str(repo / "scripts" / "run_edition.sh")], cwd=repo,
        env={**env, "EDITION": "agents", "CONTENT_WORKTREE": str(wt),
             "NEWSLETTER_PYTHON": sys.executable, "RUN_DATE": _today()},
        capture_output=True, text=True,
    )
    assert r.returncode == 1
    assert "push failed" in r.stdout


def test_python_runner_fallbacks(fx, tmp_path):
    repo, remote, env = fx
    env = {k: v for k, v in env.items() if k != "NEWSLETTER_PYTHON"}
    bin_dir = tmp_path / "nouv"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "uv").chmod(0o755)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    # uv unusable and no .venv → refuse loudly rather than guess.
    r = _run(repo, env)
    assert r.returncode == 1
    assert "none usable" in r.stdout
    # uv unusable but a committed .venv/bin/python exists → use it.
    (repo / ".venv" / "bin").mkdir(parents=True)
    # A wrapper, not a symlink: a symlinked venv interpreter loses its site-packages.
    wrapper = repo / ".venv" / "bin" / "python"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    wrapper.chmod(0o755)
    r = _run(repo, env, "--edition", "agents")
    assert ".venv/bin/python (uv unavailable" in r.stdout
    assert r.returncode == 0, r.stdout + r.stderr


def test_uv_is_preferred_when_it_works(fx, tmp_path):
    repo, remote, env = fx
    env = {k: v for k, v in env.items() if k != "NEWSLETTER_PYTHON"}
    bin_dir = tmp_path / "fakeuv"
    bin_dir.mkdir()
    # `uv --version` works; `uv run python ...` runs the test interpreter.
    (bin_dir / "uv").write_text(f'#!/bin/sh\nif [ "$1" = "--version" ]; then exit 0; fi\nshift; shift\nexec "{sys.executable}" "$@"\n')
    (bin_dir / "uv").chmod(0o755)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    r = _run(repo, env, "--edition", "agents")
    assert "── python runner ── uv run python" in r.stdout
    assert r.returncode == 0, r.stdout + r.stderr


def test_orchestrator_failure_before_editions_notifies(fx, fake_notifier):
    repo, remote, env = fx
    # Dirty tracked file → `git pull --ff-only` refuses once main has moved.
    _git(repo, "config", "pull.ff", "only")
    other = repo.parent / "other"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(other)], check=True)
    _git(other, "config", "user.name", "T")
    _git(other, "config", "user.email", "t@example.com")
    (other / "run.sh").write_text((other / "run.sh").read_text() + "\n# moved\n")
    _git(other, "commit", "-qam", "move")
    _git(other, "push", "-q")
    (repo / "run.sh").write_text((repo / "run.sh").read_text() + "\n# dirty\n")
    r = _run(repo, env)
    assert r.returncode != 0
    assert "FAILED in orchestrator at stage=self-update" in r.stdout
    assert "orchestrator stage=self-update" in fake_notifier.read_text()


def test_unknown_arg_and_help(fx):
    repo, remote, env = fx
    assert _run(repo, env, "--nope").returncode == 2
    h = _run(repo, env, "--help")
    assert h.returncode == 0 and "Usage:" in h.stdout and "--failed" in h.stdout
    assert not (repo / "logs").exists()


RECORDER_STUB = STAGE_STUB + '''
import json
with open(os.environ["STAGE_REC"], "a") as fh:
    fh.write(json.dumps({
        "stage": STAGE,
        **{k: os.environ.get(k) for k in (
            "EDITION", "CONTENT_ROOT", "RUN_DATE", "RUN_ATTEMPT",
            "RUN_STARTED_AT", "LLM_USAGE_LOG")},
    }) + "\\n")
'''


def _direct_run_edition(repo: Path, env: dict, *args: str, **extra):
    wt = repo / ".worktrees" / "content"
    return subprocess.run(
        ["bash", str(repo / "scripts" / "run_edition.sh"), *args], cwd=repo,
        env={**env, "EDITION": "agents", "CONTENT_WORKTREE": str(wt),
             "RUN_DATE": _today(), **extra},
        capture_output=True, text=True, timeout=120,
    )


def test_run_edition_runs_stages_in_order_with_accounting_env(fx):
    """Pin the contract between run_edition.sh and the stages it launches:
    fetch -> prefilter -> rank -> write -> publish, each seeing the edition,
    the content root, the date, the run-attempt number and the per-edition
    usage log that llm.py / publish.py account against."""
    import json
    repo, remote, env = fx
    assert _run(repo, env, "--edition", "agents").returncode == 0
    for stage in ("fetch", "prefilter", "rank", "write", "publish"):
        (repo / "scripts" / f"{stage}.py").write_text(RECORDER_STUB)
    rec = repo.parent / "stages.jsonl"
    r = _direct_run_edition(repo, env, STAGE_REC=str(rec))
    assert r.returncode == 0, r.stdout + r.stderr
    rows = [json.loads(line) for line in rec.read_text().splitlines()]
    assert [x["stage"] for x in rows] == ["fetch", "prefilter", "rank", "write", "publish"]
    wt = str(repo / ".worktrees" / "content")
    for x in rows:
        assert x["EDITION"] == "agents"
        assert x["CONTENT_ROOT"] == wt  # defaults to CONTENT_WORKTREE
        assert x["RUN_DATE"] == _today()
        assert x["RUN_ATTEMPT"] == "2"  # the first attempt was the run.sh call above
        assert x["RUN_STARTED_AT"] and x["RUN_STARTED_AT"].endswith("Z")
        assert x["LLM_USAGE_LOG"] == str(repo / "logs" / "agents" / f"usage-{_today()}.jsonl")
    assert len({x["RUN_STARTED_AT"] for x in rows}) == 1
    for stage in ("fetch", "prefilter", "rank", "write", "publish"):
        assert f"── {stage} ── ok" in r.stdout


def test_run_edition_stage_failure_stops_later_stages_and_keeps_exit_code(fx, fake_notifier):
    repo, remote, env = fx
    assert _run(repo, env, "--edition", "agents").returncode == 0
    for stage in ("fetch", "prefilter", "rank", "write", "publish"):
        (repo / "scripts" / f"{stage}.py").write_text(RECORDER_STUB)
    rec = repo.parent / "stages.jsonl"
    r = _direct_run_edition(repo, env, STAGE_REC=str(rec),
                            FAIL_EDITION="agents", FAIL_STAGE="rank")
    assert r.returncode == 7, r.stdout + r.stderr  # the stage's own status, not 1
    import json
    ran = [json.loads(line)["stage"] for line in rec.read_text().splitlines()]
    assert ran == ["fetch", "prefilter"]  # rank failed before recording; write/publish never ran
    assert "── rank ── FAILED (exit 7)" in r.stdout
    assert "[run] DONE" not in r.stdout
    assert _attempts(repo, "agents", env)[-1] == (2, "failed", "rank")
    assert "stage=rank rc=7" in fake_notifier.read_text()


def test_run_edition_push_failure_keeps_attempt_ok_and_commit_for_next_push(fx, fake_notifier):
    """A failed push is not a failed run: the attempt stays ok, no alert fires,
    the issue stays committed locally, and the next run's push carries it."""
    repo, remote, env = fx
    assert _run(repo, env, "--edition", "agents").returncode == 0
    wt = repo / ".worktrees" / "content"
    good_url = _git(wt, "remote", "get-url", "origin").stdout.strip()
    _git(wt, "remote", "set-url", "origin", str(repo.parent / "gone.git"))
    pushed_before = _remote_log(remote)
    local_before = _git(wt, "log", "--format=%s").stdout

    r = _direct_run_edition(repo, env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "git: committed" in r.stdout
    assert "git: push failed (will retry on next run)" in r.stdout
    assert "[run] DONE" not in r.stdout
    # Not treated as a stage failure: no FAILED line, no notification, no failed row.
    assert "FAILED at stage" not in r.stdout
    assert not fake_notifier.exists()
    assert _attempts(repo, "agents", env)[-1] == (2, "ok", None)
    # Committed locally, absent from the remote.
    assert _remote_log(remote) == pushed_before
    assert _git(wt, "log", "--format=%s").stdout != local_before
    assert "newsletter(agents): " in _git(wt, "log", "-1", "--format=%s").stdout

    # Remote is reachable again: the next run's push carries the stranded commit.
    _git(wt, "remote", "set-url", "origin", good_url)
    r2 = _direct_run_edition(repo, env)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "git: pushed to content" in r2.stdout
    assert _remote_log(remote).splitlines() == _git(wt, "log", "--format=%s").stdout.splitlines()
    assert _attempts(repo, "agents", env)[-1] == (3, "ok", None)


@pytest.mark.parametrize("flag, marker", [
    ("--force", "FORCE: resetting today's post-fetch state"),
    ("--refetch", "REFETCH: deleting today's fetched items so fetch runs again"),
])
def test_run_edition_reset_runs_after_attempt_start_and_before_fetch(fx, flag, marker):
    repo, remote, env = fx
    assert _run(repo, env, "--edition", "agents").returncode == 0
    r = _direct_run_edition(repo, env, flag)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert out.index("── run attempt ──") < out.index(marker) < out.index("── fetch ── start")
    # Exactly one of the two reset modes is announced.
    assert out.count("FORCE: resetting") + out.count("REFETCH: deleting") == 1
    assert _attempts(repo, "agents", env)[-1] == (2, "ok", None)


def test_run_edition_without_flags_does_not_reset(fx):
    repo, remote, env = fx
    assert _run(repo, env, "--edition", "agents").returncode == 0
    r = _direct_run_edition(repo, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FORCE:" not in r.stdout and "REFETCH:" not in r.stdout


def test_run_edition_reset_failure_fails_at_reset_before_any_stage(fx, fake_notifier):
    repo, remote, env = fx
    assert _run(repo, env, "--edition", "agents").returncode == 0
    for stage in ("fetch", "prefilter", "rank", "write", "publish"):
        (repo / "scripts" / f"{stage}.py").write_text(RECORDER_STUB)
    (repo / "scripts" / "reset_day.py").write_text("import sys\nsys.exit(5)\n")
    rec = repo.parent / "stages.jsonl"
    r = _direct_run_edition(repo, env, "--force", STAGE_REC=str(rec))
    assert r.returncode == 5, r.stdout + r.stderr
    assert not rec.exists()  # no stage ran
    assert "FAILED at stage=reset rc=5" in r.stdout
    assert _attempts(repo, "agents", env)[-1] == (2, "failed", "reset")
    assert "stage=reset rc=5" in fake_notifier.read_text()
    assert "attempt 2 failed at reset" in _remote_log(remote)
