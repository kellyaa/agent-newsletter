# shellcheck shell=bash
#
# The edition-list contract, sourced by run.sh and scripts/watchdog.sh. This is
# its only home (tests/test_edition_list_single_source.py enforces that).
#
# `scripts/edition.py list` prints one `slug<TAB>status<TAB>name` line per
# edition folder, in run order. status is an edition.yaml status
# (models.EditionStatus) or `invalid` (edition.LIST_INVALID_STATUS) for a
# folder whose config does not load.
#
# Both callers used to read it through `done < <(edition.py list)`. A failure
# inside process substitution trips neither `set -e` nor an ERR trap, so a
# crashing `edition.py list` looked like "no editions": run.sh logged "every
# edition is already ok today", wrote DONE and exited 0 with no alert (#670).
# editionlist_load captures the output first and returns the command's status.

EDITIONLIST_PAUSED=paused
EDITIONLIST_INVALID=invalid

# editionlist_load <python command...>
#   Run `<python...> scripts/edition.py list` (relative to the current
#   directory, the repo root) and fill the globals EDITION_SLUGS (run order)
#   and EDITION_STATUS_OF (slug -> status). Returns the command's non-zero
#   exit status (its stderr passes through) and leaves both empty on failure.
#   An empty list is not a failure here; callers decide what it means.
editionlist_load() {
  EDITION_SLUGS=()
  declare -gA EDITION_STATUS_OF=()
  local out rc=0 slug status _name
  out="$("$@" scripts/edition.py list)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    printf 'editionlist: scripts/edition.py list failed (rc=%s)\n' "$rc" >&2
    return "$rc"
  fi
  while IFS=$'\t' read -r slug status _name; do
    [ -n "$slug" ] || continue
    EDITION_STATUS_OF["$slug"]="$status"
    EDITION_SLUGS+=("$slug")
  done <<< "$out"
  return 0
}

# editionlist_is_paused <status>   0 iff a daily run skips this edition
editionlist_is_paused() { [ "$1" = "$EDITIONLIST_PAUSED" ]; }

# editionlist_is_invalid <status>  0 iff the edition's config does not load
editionlist_is_invalid() { [ "$1" = "$EDITIONLIST_INVALID" ]; }
