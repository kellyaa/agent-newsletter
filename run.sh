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
      awk 'NR>=3 { if (/^#/) print; else exit }' "$0"
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

# ts, log, notify and the run-log path/marker helpers (the run-log contract).
# shellcheck source=scripts/runlog.sh
. "$REPO_ROOT/scripts/runlog.sh"
# The content worktree path (the content-branch contract).
# shellcheck source=scripts/contentbranch.sh
. "$REPO_ROOT/scripts/contentbranch.sh"

LOG_DIR="$(runlog_dir "$REPO_ROOT")"
mkdir -p "$LOG_DIR"
DATE_TODAY="$(date +%F)"
LOG_FILE="$(runlog_summary_path "$REPO_ROOT" "$DATE_TODAY")"

# tee everything through to a per-day log so we can grep failures later.
exec > >(tee -a "$LOG_FILE") 2>&1

export RUN_DATE="$DATE_TODAY"

# ─── Environment loading ───────────────────────────────────────────────────
# LLM credentials and operator settings: ~/.config/agent-newsletter/env, then
# .env (the last file wins). Loaded before the lock, the runner choice and any
# notify, so WATCHDOG_WEBHOOK_URL and NEWSLETTER_PYTHON set there apply to them.
# The file list and order live in scripts/envfiles.sh.
# shellcheck source=scripts/envfiles.sh
. "$REPO_ROOT/scripts/envfiles.sh"
envfiles_load "$REPO_ROOT"

# ─── Single-run lock ───────────────────────────────────────────────────────
# Hold an advisory lock for the life of this run (editions run one after
# another under it; children inherit the descriptor). scripts/watchdog.sh probes
# this same lock to tell "died mid-run" apart from "still running", so it never
# starts a second pipeline on top of a live one. Released when the shell exits.
# The lock contract (path, helpers, the no-flock case) lives in runlock.sh.
# shellcheck source=scripts/runlock.sh
. "$REPO_ROOT/scripts/runlock.sh"
LOCK_FILE="$(runlock_run_path "$REPO_ROOT")"
lock_rc=0
runlock_acquire 9 "$LOCK_FILE" || lock_rc=$?
case "$lock_rc" in
  0) ;;
  1)
    log "another run holds $LOCK_FILE; exiting without doing anything"
    exit 0 ;;
  *)
    # Not a held lock: without flock no run can ever start, so fail loudly
    # instead of exiting 0 like a lockout (README, Prerequisites).
    log "FAILED: flock not found; cannot take the single-run lock — install flock (util-linux)"
    notify "Newsletter pipeline failed" \
      "flock not found; run.sh cannot take its lock — see logs/$(basename "$LOG_FILE")"
    exit 1 ;;
esac

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
# uv when it answers, else the committed .venv; NEWSLETTER_PYTHON overrides.
# The selection (and why uv may be skipped) lives in scripts/pyrunner.sh.
# shellcheck source=scripts/pyrunner.sh
. "$REPO_ROOT/scripts/pyrunner.sh"
if ! pyrunner_select "$REPO_ROOT" run; then
  log "── python runner ── $PY_RUN_SOURCE"
  exit 1
fi
log "── python runner ── $PY_RUN_SOURCE"
export NEWSLETTER_PYTHON="${PY_RUN[*]}"

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
CONTENT_WORKTREE="$(content_worktree_path "$REPO_ROOT")"

if ! git worktree list --porcelain | grep -q "$CONTENT_WORKTREE\$"; then
  log "── content worktree ── creating at ${CONTENT_WORKTREE#"$REPO_ROOT"/}"
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
# The edition list (scripts/editionlist.sh): one slug per edition folder, in run
# order. A folder with a broken config shows as "invalid": it still runs (and
# fails loudly, with a notification) instead of silently vanishing. A failing
# `edition.py list` fails this stage; it must never read as "nothing to run".
# shellcheck source=scripts/editionlist.sh
. "$REPO_ROOT/scripts/editionlist.sh"
editionlist_load "${PY_RUN[@]}"
ALL_SLUGS=(${EDITION_SLUGS[@]+"${EDITION_SLUGS[@]}"})

# Latest attempt today for an edition: ok | failed | started | none.
latest_attempt() {
  EDITION="$1" "${PY_RUN[@]}" scripts/run_attempt.py latest 2>/dev/null || echo none
}

SELECTED=()
if [ "${#EDITIONS_WANTED[@]}" -gt 0 ]; then
  for slug in "${EDITIONS_WANTED[@]}"; do
    if [ -z "${EDITION_STATUS_OF[$slug]+x}" ]; then
      log "unknown edition '$slug' (known: ${ALL_SLUGS[*]:-none})"
      exit 2
    fi
    SELECTED+=("$slug")
  done
else
  for slug in "${ALL_SLUGS[@]}"; do
    status="${EDITION_STATUS_OF[$slug]}"
    editionlist_is_paused "$status" && continue
    latest="$(latest_attempt "$slug")"
    if [ "$FAILED_ONLY" = true ]; then
      # An invalid config never records an attempt; it has not succeeded either.
      case "$latest" in failed|started) ;; *) editionlist_is_invalid "$status" || continue ;; esac
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
  runlog_mark_done
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
  slug_log="$(runlog_path "$REPO_ROOT" "$slug" "$DATE_TODAY")"
  log "── $slug ── start (${slug_log#"$REPO_ROOT"/})"
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
    stage="$(runlog_failed_stage "$slug_log" || true)"
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

runlog_mark_done
