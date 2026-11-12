# shellcheck shell=bash
#
# The Python-runner contract, sourced by run.sh and scripts/watchdog.sh. This is
# its only home (tests/test_python_runner_single_source.py enforces that).
#
# Which interpreter runs scripts/*.py. Normally `uv run python`. But on the pod
# `uv` is a lazy shim that installs uv through mise on first use, and that
# install lands outside $HOME, so a pod restart wipes it and the reinstall has
# to succeed again. On 2026-09-22 it stopped succeeding: mise's
# GitHub-attestation check could not reach tuf-repo-cdn.sigstore.dev, so
# `uv --version` hung in a retry loop and every stage died before it started.
# The committed .venv and the interpreter it points at are both under $HOME and
# survive restarts, so fall back to calling that python directly. The probe is
# cheap and self-correcting: once uv works again, uv is used again.
# NEWSLETTER_PYTHON overrides the choice (tests, unusual setups); run.sh exports
# the runner it chose as NEWSLETTER_PYTHON, so scripts/run_edition.sh just reads it.
#
# pyrunner_select <repo-root> <mode>
#   Sets PY_RUN (array) and PY_RUN_SOURCE (what was chosen and why, for the log).
#   run   (run.sh): NEWSLETTER_PYTHON, else `uv run python` when `uv --version`
#         answers within PYRUNNER_UV_TIMEOUT_S (45) seconds, else .venv/bin/python,
#         else return 1 (refuse loudly rather than guess).
#   check (scripts/watchdog.sh, hourly): NEWSLETTER_PYTHON, else
#         .venv/bin/python, else python3. No uv probe, so a hung uv never stalls
#         the check; a bare python3 without the dependencies still fails loudly
#         (STATUS: FAILED <date> edition-list).
#   Returns 2 on an unknown mode.

PYRUNNER_UV_TIMEOUT_S=45

# pyrunner_venv_python <repo-root>   the committed venv's interpreter
pyrunner_venv_python() { printf '%s\n' "$1/.venv/bin/python"; }

pyrunner_select() {
  local root="$1" mode="$2" venv
  venv="$(pyrunner_venv_python "$root")"
  PY_RUN=()
  PY_RUN_SOURCE=""
  case "$mode" in
    run|check) ;;
    *) printf 'pyrunner_select: unknown mode %s\n' "$mode" >&2; return 2 ;;
  esac
  if [ -n "${NEWSLETTER_PYTHON:-}" ]; then
    read -r -a PY_RUN <<< "$NEWSLETTER_PYTHON"
    PY_RUN_SOURCE="$NEWSLETTER_PYTHON (NEWSLETTER_PYTHON)"
  elif [ "$mode" = run ] && timeout "$PYRUNNER_UV_TIMEOUT_S" uv --version >/dev/null 2>&1; then
    PY_RUN=(uv run python)
    PY_RUN_SOURCE="uv run python"
  elif [ -x "$venv" ]; then
    PY_RUN=("$venv")
    if [ "$mode" = run ]; then
      PY_RUN_SOURCE=".venv/bin/python (uv unavailable — see scripts/pyrunner.sh)"
    else
      PY_RUN_SOURCE=".venv/bin/python"
    fi
  elif [ "$mode" = check ]; then
    PY_RUN=(python3)
    PY_RUN_SOURCE="python3"
  else
    PY_RUN_SOURCE="none usable: uv is broken and .venv/bin/python is missing"
    return 1
  fi
}
