"""Rubric hash tracking — detects prompt changes and invalidates stale scores.

When prompts/rank.md changes, papers candidates that were scored under a prior
rubric need re-scoring. This module owns that detection and invalidation so
that:
  1. prefilter.py doesn't need to know about the ranker's prompt file location
  2. rank.py can call the same function if it wants pre-flight validation
  3. The coupling between "prompt changed" and "invalidate scores" is explicit
     and testable in isolation

The rubric hash is persisted at .rubric_hash in the repo root.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from db import REPO_ROOT

log = logging.getLogger(__name__)

RUBRIC_PATH = REPO_ROOT / "prompts" / "rank.md"
RUBRIC_HASH_PATH = REPO_ROOT / ".rubric_hash"


def current_rubric_hash() -> str:
    """Return the SHA-256 hex digest of prompts/rank.md, or '' if missing."""
    if not RUBRIC_PATH.exists():
        return ""
    return hashlib.sha256(RUBRIC_PATH.read_bytes()).hexdigest()


def invalidate_stale_scores(conn) -> int:
    """If the rubric changed since the last run, wipe cached papers scores.

    Returns the count of rows invalidated (0 if hash hasn't changed or on
    first run). Writes the new hash to .rubric_hash.
    """
    current = current_rubric_hash()
    if not current:
        return 0

    last = RUBRIC_HASH_PATH.read_text().strip() if RUBRIC_HASH_PATH.exists() else ""
    if last == current:
        return 0

    cur = conn.execute(
        "UPDATE items SET score = NULL "
        "WHERE status = 'candidate' AND section = 'papers' AND score IS NOT NULL"
    )
    invalidated = cur.rowcount or 0
    conn.commit()
    RUBRIC_HASH_PATH.write_text(current)

    if invalidated:
        log.info(
            "rubric changed (hash %s → %s) — invalidated %d cached papers scores",
            last[:8] or "<none>", current[:8], invalidated,
        )
    else:
        log.info("rubric hash recorded: %s", current[:8])

    return invalidated
