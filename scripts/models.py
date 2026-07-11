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

from enum import StrEnum
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
    PUBLISHED = "published"


class Section(StrEnum):
    """Newsletter sections. Values match the state.db `section` column."""

    PAPERS = "papers"
    NEWS = "news"
    BLOGS = "blogs"


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
