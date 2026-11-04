"""Shared type definitions and domain constants for the newsletter pipeline.

These TypedDicts define the canonical shape of an item as it flows through
the pipeline. Using these instead of bare `dict` enables:
  - Static type checking (mypy/pyright catch field-name typos)
  - IDE autocompletion on item fields
  - Self-documenting stage contracts

The Status and Section constants centralize the string literals used as
item states and newsletter sections, preventing typo-induced silent failures.

Import from here in prefilter, rank, write, backfill, replay_writer.
"""
from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import TypedDict


# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

class Status(StrEnum):
    """Item lifecycle states. Values match the state.db `status` column."""

    NEW = "new"
    CANDIDATE = "candidate"
    FEATURED = "featured"
    APPENDIX = "appendix"
    DROPPED = "dropped"
    # Papers that aged out of the pool without ever reaching the ranker (issue #289).
    EXPIRED_UNJUDGED = "expired_unjudged"
    PUBLISHED = "published"


class Section(StrEnum):
    """Newsletter sections. Values match the state.db `section` column."""

    PAPERS = "papers"
    NEWS = "news"
    BLOGS = "blogs"


class EditionStatus(StrEnum):
    """Edition lifecycle states. Values match edition.yaml `status`.

    run.sh / watchdog.sh run draft and live editions and skip paused ones;
    check_edition treats lint warnings as errors for a live edition.
    """

    DRAFT = "draft"
    LIVE = "live"
    PAUSED = "paused"


class MetaKey(StrEnum):
    """Keys in the state.db `meta` table that more than one module reads or writes."""

    # Run date for which fetch completed; fetch sets it, reset_day --refetch clears it.
    FETCH_DONE = "fetch_completed_date"


class ExitCode(IntEnum):
    """Process exit codes shared by the pipeline stages (fetch → publish).

    run_edition.sh treats any non-zero code as a stage failure and records it
    in run_attempts.exit_code; these names say what each number means.
    """

    OK = 0
    # Every source failed, so the stage produced nothing (fetch).
    NO_DATA = 1
    # A file the stage needs is missing: a prompt, or the issue from write.
    MISSING_INPUT = 2
    # publish: the issue file is too small to be a real issue.
    ISSUE_TOO_SMALL = 4
    # publish: no featured and no appendix items, so the issue would be empty.
    EMPTY_ISSUE = 5


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------


class ItemRow(TypedDict, total=False):
    """Full item as stored in state.db. All columns present.

    `total=False` because some columns are nullable/optional in the DB.
    Required fields are documented; consumers should use narrower types below.
    """
    id: str
    source: str
    url: str
    canonical_url: str
    title: str
    author: str | None
    published_at: str | None
    fetched_at: str
    raw_text: str | None
    score: int | None
    tags: str | None  # JSON-encoded list in DB
    section: str | None
    section_override: str | None
    keyword_gate_bypass: int
    recency_days_override: int | None
    why: str | None
    status: str
    first_seen_date: str
    last_seen_date: str
    appearances: int
    times_competed: int
    published_date: str | None


class PrefilterItem(TypedDict):
    """Item shape as read by prefilter from DB (subset of ItemRow)."""
    id: str
    source: str
    url: str
    canonical_url: str
    title: str
    author: str | None
    published_at: str | None
    fetched_at: str
    raw_text: str | None
    status: str
    appearances: int
    section_override: str | None
    keyword_gate_bypass: int
    recency_days_override: int | None


class CandidateItem(TypedDict, total=False):
    """Item as emitted in candidates.json for the ranker.

    Required keys are always present; optional keys appear only for
    prescored papers (score, tags, why).
    """
    id: str
    source: str
    url: str
    title: str
    author: str | None
    published_at: str | None
    raw_text: str | None
    # Present only in papers_prescored bucket:
    score: int
    tags: list[str]
    why: str


class ScoredItem(TypedDict):
    """A single item as returned by the ranker LLM."""
    id: str
    score: int
    tags: list[str]
    why: str


class RankDecision(TypedDict):
    """Per-item decision from assign_statuses()."""
    status: str
    score: int
    tags: list[str]
    why: str
    section: str


class FeaturedItem(TypedDict, total=False):
    """Item shape passed to the writer, combining DB data + rank output."""
    id: str
    section: str
    source: str
    url: str
    title: str
    author: str | None
    published_at: str | None
    raw_text: str | None
    score: int
    tags: list[str]
    why: str | None


class AppendixItem(TypedDict):
    """Minimal item shape for the appendix section."""
    id: str
    section: str
    source: str
    url: str
    title: str


class FrontmatterFeatured(TypedDict, total=False):
    """Featured item as written into issue file YAML frontmatter."""
    id: str
    section: str
    source: str
    url: str
    title: str
    author: str | None
    score: int
    tags: list[str]
    summary: str
    takeaway: str | None
    open_question: str | None
