"""The Python-runner contract lives only in ``scripts/pyrunner.sh``.

``run.sh`` and ``scripts/watchdog.sh`` each used to pick the interpreter for
``scripts/*.py`` by hand: the ``NEWSLETTER_PYTHON`` override, the ``uv`` probe,
the ``.venv/bin/python`` path and what to do when nothing is usable. The two
copies differed (run.sh refuses, the watchdog falls back to ``python3``), and
nothing said that was on purpose or kept the two in step. ``pyrunner_select``
now holds both policies as named modes.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "pyrunner.sh"
SHELL = sorted({*REPO.glob("*.sh"), *(REPO / "scripts").glob("*.sh"),
                *(REPO / "launchd").glob("*.sh")} - {LIB})


def _select(root: Path, mode: str, path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run pyrunner_select; print rc, PY_RUN (one word per line) and PY_RUN_SOURCE."""
    script = (f'. "{LIB}"; rc=0; pyrunner_select "$1" "$2" || rc=$?; '
              'echo "rc=$rc"; echo "source=$PY_RUN_SOURCE"; printf "arg=%s\\n" "${PY_RUN[@]}"')
    base = {k: v for k, v in os.environ.items() if k != "NEWSLETTER_PYTHON"}
    return subprocess.run(
        ["bash", "-c", script, "_", str(root), mode],
        capture_output=True, text=True, timeout=20,
        env={**base, "PATH": f"{path}{os.pathsep}/usr/bin{os.pathsep}/bin", **env},
    )


def _parse(p: subprocess.CompletedProcess[str]) -> tuple[int, str, list[str]]:
    lines = p.stdout.splitlines()
    rc = int(lines[0].removeprefix("rc="))
    source = lines[1].removeprefix("source=")
    return rc, source, [l.removeprefix("arg=") for l in lines[2:] if l != "arg="]


def _bin(tmp_path: Path, uv_rc: int | None) -> Path:
    """A PATH dir with a fake uv (exiting uv_rc), or none at all."""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    if uv_rc is not None:
        (d / "uv").write_text(f"#!/bin/sh\nexit {uv_rc}\n")
        (d / "uv").chmod(0o755)
    return d


def _venv(root: Path) -> Path:
    py = root / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    py.chmod(0o755)
    return py


# ─── behaviour ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["run", "check"])
def test_override_wins_and_is_split_into_words(tmp_path, mode):
    _venv(tmp_path)
    p = _select(tmp_path, mode, _bin(tmp_path, 0), NEWSLETTER_PYTHON="uv run python")
    assert _parse(p) == (0, "uv run python (NEWSLETTER_PYTHON)", ["uv", "run", "python"])


def test_run_prefers_working_uv(tmp_path):
    _venv(tmp_path)
    assert _parse(_select(tmp_path, "run", _bin(tmp_path, 0))) == (
        0, "uv run python", ["uv", "run", "python"])


def test_run_falls_back_to_venv_when_uv_fails(tmp_path):
    py = _venv(tmp_path)
    rc, source, run = _parse(_select(tmp_path, "run", _bin(tmp_path, 1)))
    assert (rc, run) == (0, [str(py)])
    assert source.startswith(".venv/bin/python (uv unavailable")


def test_run_refuses_when_nothing_is_usable(tmp_path):
    rc, source, run = _parse(_select(tmp_path, "run", _bin(tmp_path, 1)))
    assert (rc, run) == (1, [])
    assert source.startswith("none usable")


def test_check_never_probes_uv(tmp_path):
    # A uv that would answer is still skipped: the hourly check must never
    # wait on the probe. A uv that records its calls proves it was not run.
    d = _bin(tmp_path, None)
    marker = tmp_path / "uv-called"
    (d / "uv").write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    (d / "uv").chmod(0o755)
    py = _venv(tmp_path)
    assert _parse(_select(tmp_path, "check", d)) == (0, ".venv/bin/python", [str(py)])
    assert not marker.exists()


def test_check_falls_back_to_python3(tmp_path):
    assert _parse(_select(tmp_path, "check", _bin(tmp_path, None))) == (0, "python3", ["python3"])


def test_unknown_mode_is_an_error(tmp_path):
    p = _select(tmp_path, "bogus", _bin(tmp_path, None))
    assert _parse(p)[0] == 2
    assert "unknown mode" in p.stderr


def test_venv_path_helper():
    p = subprocess.run(["bash", "-c", f'. "{LIB}"; pyrunner_venv_python /r'],
                       capture_output=True, text=True, check=True)
    assert p.stdout == "/r/.venv/bin/python\n"


# ─── single source ─────────────────────────────────────────────────────────

def _code(p: Path) -> str:
    return "\n".join(l for l in p.read_text().splitlines() if not l.lstrip().startswith("#"))


@pytest.mark.parametrize("script", SHELL, ids=lambda p: str(p.relative_to(REPO)))
def test_no_script_selects_the_runner_itself(script):
    code = _code(script)
    assert ".venv/bin/python" not in code, (
        f"{script.name} spells the venv interpreter; use pyrunner_select from scripts/pyrunner.sh")
    assert not re.search(r"\buv\s+--version", code), (
        f"{script.name} probes uv; use pyrunner_select from scripts/pyrunner.sh")
    if script.name != "run_edition.sh":  # runs what run.sh exported (see below)
        assert not re.search(r"<<<.*NEWSLETTER_PYTHON", code), (
            f"{script.name} parses NEWSLETTER_PYTHON; use pyrunner_select from scripts/pyrunner.sh")


@pytest.mark.parametrize("script,mode", [("run.sh", "run"), ("scripts/watchdog.sh", "check")])
def test_users_source_the_library(script, mode):
    text = (REPO / script).read_text()
    assert '. "$REPO_ROOT/scripts/pyrunner.sh"' in text
    assert f'pyrunner_select "$REPO_ROOT" {mode}' in text


def test_run_sh_exports_its_choice_to_run_edition():
    # run_edition.sh does not select: it runs whatever run.sh chose.
    assert 'export NEWSLETTER_PYTHON="${PY_RUN[*]}"' in (REPO / "run.sh").read_text()
    assert '"${NEWSLETTER_PYTHON:-python3}"' in (REPO / "scripts" / "run_edition.sh").read_text()
