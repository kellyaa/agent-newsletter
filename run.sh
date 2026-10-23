#!/usr/bin/env bash
#
# Daily orchestrator for the newsletter pipeline: runs every edition in turn.
#
# Each edition (editions/<slug>/) runs in its own child process,
# scripts/run_edition.sh (fetch → prefilter → rank → write → publish → commit and
# push editions/<slug>/). One edition failing never blocks the others; the
# orchestrator carries on, then exits non-zero if any edition failed.
#
# Every stage is idempotent, so a re-run picks up where a failure left off.
#
# Usage:
#   ./run.sh                  — every draft + live edition, in `order`. Skips an
#                               edition whose latest attempt today is ok, so a
#                               plain re-run is cheap and safe.
#   ./run.sh --failed         — only editions whose latest attempt today failed
#                               or died mid-run ("started"). "nothing to re-run"
#                               and exit 0 if there are none.
#   ./run.sh --edition X      — only edition X (repeatable). Paused editions are
#                               allowed here, for manual runs.
#   ./run.sh --force          — per selected edition: reset today's post-fetch
#                               state and re-run everything downstream of fetch
#                               (preserves fetched data; arXiv is rate-limited)
#   ./run.sh --refetch        — also delete today's fetched items so fetch runs
#                               again. Implies --force and REQUIRES --edition, so
#                               it can't re-pull arXiv for every edition at once.
#
# Logs: logs/run-DATE.log is the orchestrator summary (one line per edition);
# logs/<slug>/run-DATE.log is the full per-edition output, ending in DONE.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

FORCE=false
REFETCH=false
FAILED_ONLY=false
EDITIONS_WANTED=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --force) FORCE=true ;;
    --refetch) REFETCH=true; FORCE=true ;;
    --failed) FAILED_ONLY=true ;;
    --edition)
      if [ "$#" -lt 2 ] || [ -z "$2" ]; then
        printf -- '--edition needs a value\n' >&2; exit 2
      fi
      EDITIONS_WANTED+=("$2"); shift ;;
    --edition=*) EDITIONS_WANTED+=("${1#--edition=}") ;;
    -h|--help)
      # Print the header comment (stops output from going to the run log).
      sed -n '3,30p' "$0"
      exit 0
      ;;
    *) printf 'unknown arg: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$REFETCH" = true ] && [ "${#EDITIONS_WANTED[@]}" -eq 0 ]; then
  printf -- '--refetch requires --edition <slug> (it re-pulls arXiv; do it one edition at a time)\n' >&2
  exit 2
fi
if [ "$FAILED_ONLY" = true ] && [ "${#EDITIONS_WANTED[@]}" -gt 0 ]; then
  printf -- '--failed and --edition cannot be combined\n' >&2
  exit 2
fi

LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"
DATE_TODAY="$(date +%F)"
LOG_FILE="$LOG_DIR/run-$DATE_TODAY.log"

# tee everything through to a per-day log so we can grep failures later.
exec > >(tee -a "$LOG_FILE") 2>&1

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '%s [run] %s\n' "$(ts)" "$*"; }

export RUN_DATE="$DATE_TODAY"

# ─── Single-run lock ───────────────────────────────────────────────────────
# Hold an advisory lock for the life of this run (editions run one after
# another under it; children inherit the descriptor). scripts/watchdog.sh probes
# this same lock to tell "died mid-run" apart from "still running", so it never
# starts a second pipeline on top of a live one. Released when the shell exits.
LOCK_FILE="$REPO_ROOT/.watchdog.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another run holds $LOCK_FILE; exiting without doing anything"
  exit 0
fi

# Fire a macOS notification. No-op on non-Darwin or if osascript is missing.
notify() {
  local title="$1"; shift
  local message="$1"; shift
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"${message//\"/\\\"}\" with title \"${title//\"/\\\"}\"" \
      >/dev/null 2>&1 || true
  fi
}

# A failure of the orchestrator itself (before or between editions). Edition
# failures are handled in run_edition.sh and are never fatal here.
fail_handler() {
  local rc=$?
  log "FAILED in orchestrator at stage=${CURRENT_STAGE:-unknown} rc=$rc — see $LOG_FILE"
  notify "Newsletter pipeline failed" \
    "orchestrator stage=${CURRENT_STAGE:-unknown} rc=$rc — see logs/$(basename "$LOG_FILE")"
  exit "$rc"
}
CURRENT_STAGE="startup"
trap fail_handler ERR

# ─── Python runner ─────────────────────────────────────────────────────────
# Normally `uv run python`. But on this pod `uv` is a lazy shim that installs
# uv through mise on first use, and that install lands outside $HOME — so a pod
# restart wipes it and the reinstall has to succeed again. On 2026-09-22 it
# stopped succeeding: mise's GitHub-attestation check can't reach
# tuf-repo-cdn.sigstore.dev, so `uv --version` hangs in a retry loop forever and
# every stage dies before it starts. The committed .venv and the interpreter it
# points at are both under $HOME and survive restarts, so fall back to calling
# that python directly rather than letting a broken tool installer sink the run.
# The probe is cheap and self-correcting: once uv works again, uv is used again.
# NEWSLETTER_PYTHON overrides the probe (tests, unusual setups).
PY_RUN=()
if [ -n "${NEWSLETTER_PYTHON:-}" ]; then
  read -r -a PY_RUN <<< "$NEWSLETTER_PYTHON"
  log "── python runner ── $NEWSLETTER_PYTHON (NEWSLETTER_PYTHON)"
elif timeout 45 uv --version >/dev/null 2>&1; then
  PY_RUN=(uv run python)
  log "── python runner ── uv run python"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PY_RUN=("$REPO_ROOT/.venv/bin/python")
  log "── python runner ── .venv/bin/python (uv unavailable — see comment in run.sh)"
else
  log "── python runner ── none usable: uv is broken and .venv/bin/python is missing"
  exit 1
fi
export NEWSLETTER_PYTHON="${PY_RUN[*]}"

# ─── Environment loading ───────────────────────────────────────────────────
# Load LLM credentials and other config. Sourced in order, so the *last* file
# wins for any given key:
#   1. ~/.config/agent-newsletter/env  — machine-wide defaults (optional)
#   2. .env                            — repo-local, gitignored (overrides)
for ENV_FILE in "$HOME/.config/agent-newsletter/env" "$REPO_ROOT/.env"; do
  if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
  fi
done

# ─── Self-update ───────────────────────────────────────────────────────────
# Pull the current branch so pipeline code, prompts, and site scaffold are
# current before generating content against them. For the launchd daily run
# this is a no-op catchup on main. For a human running ./run.sh from a
# feature branch, it updates that branch. --ff-only lets git's native error
# surface if the working tree is dirty or the pull isn't fast-forward.
CURRENT_STAGE="self-update"
log "── self-update ── pulling current branch"
git pull --ff-only

# ─── Content worktree ──────────────────────────────────────────────────────
# The 'content' orphan branch holds machine-authored deploy artifacts
# (editions/<slug>/state.db and editions/<slug>/issues/*). Pipeline scripts
# write into this worktree via the CONTENT_ROOT env var. The worktree is
# created on first run and reused thereafter; the pull catches up any commits
# pushed from another machine or a manual edit.
CURRENT_STAGE="content-worktree"
CONTENT_WORKTREE="$REPO_ROOT/.worktrees/content"

if ! git worktree list --porcelain | grep -q "$CONTENT_WORKTREE\$"; then
  log "── content worktree ── creating at .worktrees/content"
  git worktree add "$CONTENT_WORKTREE" content
fi
log "── content worktree ── pulling"
git -C "$CONTENT_WORKTREE" pull --ff-only

export CONTENT_ROOT="$CONTENT_WORKTREE"
export CONTENT_WORKTREE

# ─── Layout migration ──────────────────────────────────────────────────────
# One-time, idempotent: moves the single-edition layout (state.db and
# site/src/content/issues/ at the content root) under editions/agents/. A no-op
# once done. It refuses (exit 3) while the deploy workflow cannot yet stage the
# new layout; running on would write a fresh agents DB at the new path and
# strand the history, so stop here, loudly.
CURRENT_STAGE="migrate-layout"
log "── migrate layout ──"
"${PY_RUN[@]}" scripts/migrate_layout.py --content-root "$CONTENT_WORKTREE" --push

# ─── Select editions ───────────────────────────────────────────────────────
CURRENT_STAGE="select-editions"
# `edition.py list` prints "slug<TAB>status<TAB>name" per edition folder, in run
# order. A folder with a broken config shows as "invalid": it still runs (and
# fails loudly, with a notification) instead of silently vanishing.
declare -A STATUS_OF=()
ALL_SLUGS=()
while IFS=$'\t' read -r slug status _name; do
  [ -n "$slug" ] || continue
  STATUS_OF["$slug"]="$status"
  ALL_SLUGS+=("$slug")
done < <("${PY_RUN[@]}" scripts/edition.py list)

# Latest attempt today for an edition: ok | failed | started | none.
latest_attempt() {
  EDITION="$1" "${PY_RUN[@]}" scripts/run_attempt.py latest 2>/dev/null || echo none
}

SELECTED=()
if [ "${#EDITIONS_WANTED[@]}" -gt 0 ]; then
  for slug in "${EDITIONS_WANTED[@]}"; do
    if [ -z "${STATUS_OF[$slug]+x}" ]; then
      log "unknown edition '$slug' (known: ${ALL_SLUGS[*]:-none})"
      exit 2
    fi
    SELECTED+=("$slug")
  done
else
  for slug in "${ALL_SLUGS[@]}"; do
    status="${STATUS_OF[$slug]}"
    [ "$status" = paused ] && continue
    latest="$(latest_attempt "$slug")"
    if [ "$FAILED_ONLY" = true ]; then
      # An invalid config never records an attempt; it has not succeeded either.
      case "$latest" in failed|started) ;; *) [ "$status" = invalid ] || continue ;; esac
    elif [ "$FORCE" != true ] && [ "$latest" = ok ]; then
      log "── $slug ── already ok today; skipping"
      continue
    fi
    SELECTED+=("$slug")
  done
fi

if [ "${#SELECTED[@]}" -eq 0 ]; then
  if [ "$FAILED_ONLY" = true ]; then
    log "nothing to re-run: no edition failed today"
  else
    log "nothing to run: every edition is already ok today (use --force to re-run)"
  fi
  log "DONE"
  exit 0
fi
log "── editions ── ${SELECTED[*]}"

# ─── Run each edition in its own child process ─────────────────────────────
# A failure is recorded and the loop carries on. The child sends its own
# notification and pushes its own failed-attempt row.
child_args=()
[ "$REFETCH" = true ] && child_args+=(--refetch)
[ "$REFETCH" != true ] && [ "$FORCE" = true ] && child_args+=(--force)

OK_SLUGS=()
FAILED_SLUGS=()
SUMMARY=()
CURRENT_STAGE="editions"
for slug in "${SELECTED[@]}"; do
  log "── $slug ── start (logs/$slug/run-$DATE_TODAY.log)"
  # `if` (not `set +e`): the ERR trap fires on a failing command even with -e
  # off, but never for a command tested by `if`.
  if EDITION="$slug" bash "$REPO_ROOT/scripts/run_edition.sh" ${child_args[@]+"${child_args[@]}"}; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    OK_SLUGS+=("$slug")
    SUMMARY+=("$slug: ok")
    log "── $slug ── ok"
  else
    stage="$(grep -oE 'FAILED at stage=[a-z-]+' "$REPO_ROOT/logs/$slug/run-$DATE_TODAY.log" 2>/dev/null \
      | tail -1 | cut -d= -f2 || true)"
    FAILED_SLUGS+=("$slug")
    SUMMARY+=("$slug: FAILED at stage=${stage:-unknown} rc=$rc")
    log "── $slug ── FAILED at stage=${stage:-unknown} rc=$rc — re-run: ./run.sh --edition $slug"
  fi
done

log "── summary ── ok=${#OK_SLUGS[@]} failed=${#FAILED_SLUGS[@]}"
for line in "${SUMMARY[@]}"; do log "  $line"; done

if [ "${#FAILED_SLUGS[@]}" -gt 0 ]; then
  log "FAILED editions: ${FAILED_SLUGS[*]} — re-run with ./run.sh --failed"
  trap - ERR
  exit 1
fi

log "DONE"
