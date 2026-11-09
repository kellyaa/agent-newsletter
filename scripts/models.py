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


class SourceFamily(StrEnum):
    """Top-level keys of an edition's sources.yaml (one collector per family).

    fetch.iter_collectors walks these, check_edition lints them, and
    new_edition scaffolds a subset. Declaration order is the fetch order.
    """

    RSS = "rss"
    ARXIV = "arxiv"
    HN = "hn"
    REDDIT = "reddit"
    HTML = "html"
    GITHUB_RELEASES = "github_releases"


# Keys every sources.yaml entry of a family must carry; fetch indexes them
# directly (src["id"], src["url"], entry["owner"] ...), so a missing one is a
# KeyError mid-run. check_edition rejects entries lacking them.
SOURCE_REQUIRED_KEYS: dict[SourceFamily, frozenset[str]] = {
    SourceFamily.RSS: frozenset({"id", "url"}),
    SourceFamily.ARXIV: frozenset({"id", "query"}),
    SourceFamily.HN: frozenset({"id", "query"}),
    SourceFamily.REDDIT: frozenset({"id", "url"}),
    SourceFamily.HTML: frozenset({"id", "url"}),
    SourceFamily.GITHUB_RELEASES: frozenset({"owner", "repo"}),
}


def source_label(family: SourceFamily, entry: dict) -> str:
    """The per-source label fetch logs and records in ``source_fetch_outcomes``.

    ``<family>/<id>``, except GitHub releases: ``gh/<owner>/<repo>``.
    fetch.iter_collectors writes it and dead_sources.py joins sources.yaml
    entries to outcome rows by it, so both must build it here.
    """
    if family is SourceFamily.GITHUB_RELEASES:
        return f"gh/{entry.get('owner')}/{entry.get('repo')}"
    return f"{family.value}/{entry.get('id')}"


# Prefix of the ``items.source`` value each family's fetch adapter writes
# (``rss:<id>``, ``gh:<owner>/<repo>`` ...). gates.py reads it back to pick the
# recency window, near-dup priority and default section, so fetch builds it
# only via item_source() and gates parses it only via item_source_prefix().
# GitHub releases predate the family name and keep ``gh``: existing state.db
# rows carry it. tests/test_item_source_single_source.py enforces this.
ITEM_SOURCE_PREFIX: dict[SourceFamily, str] = {
    SourceFamily.RSS: "rss",
    SourceFamily.ARXIV: "arxiv",
    SourceFamily.HN: "hn",
    SourceFamily.REDDIT: "reddit",
    SourceFamily.HTML: "html",
    SourceFamily.GITHUB_RELEASES: "gh",
}


def item_source(family: SourceFamily, entry: dict) -> str:
    """The ``items.source`` value for an item fetched from *entry*.

    ``<prefix>:<id>``, except GitHub releases: ``gh:<owner>/<repo>``. Keys are
    indexed, as the adapters always did: a missing one raises, not ``rss:None``.
    """
    prefix = ITEM_SOURCE_PREFIX[family]
    if family is SourceFamily.GITHUB_RELEASES:
        return f"{prefix}:{entry['owner']}/{entry['repo']}"
    return f"{prefix}:{entry['id']}"


def item_source_prefix(source: str) -> str:
    """``"rss:simonwillison"`` -> ``"rss"`` (the inverse of item_source's prefix)."""
    return source.split(":", 1)[0]


# Optional per-entry sources.yaml overrides. fetch applies them (falling back
# with a warning on a bad value), check_edition rejects bad values, and
# new_edition only emits valid ones; all three read the rules from here so
# lint, scaffold and runtime agree on what is accepted.
SOURCE_BOOL_FLAGS: tuple[str, ...] = ("keyword_gate_bypass", "exclude_launches")
SOURCE_POSITIVE_INT_KEYS: tuple[str, ...] = ("recency_days", "limit", "dead_after_days")
REDDIT_DEFAULT_LIMIT = 10  # top-N posts per subreddit when `limit` is unset


def is_positive_int(value: object) -> bool:
    """True for an int > 0. bool is an int subclass, so True/False are rejected."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


# Content-branch layout and run-commit subjects (multi-edition layout, #301).
# scripts/run_edition.sh writes these and backfill.py reads them back, so both
# sides must agree; tests/test_content_layout_single_source.py checks the shell
# against these values. Kept here (no imports) so migrate_layout.py, which must
# not import db/edition, can use them too.
EDITIONS_DIRNAME = "editions"
STATE_DB_NAME = "state.db"
ISSUES_DIRNAME = "issues"
# The one edition that predates the layout: its state.db sat at the content
# root and its run commits read "newsletter: <date> ...".
LEGACY_EDITION = "agents"
LEGACY_RUN_COMMIT_PREFIX = "newsletter: "


def edition_state_db_relpath(slug: str) -> str:
    """``editions/<slug>/state.db``, relative to the content root (a git path)."""
    return f"{EDITIONS_DIRNAME}/{slug}/{STATE_DB_NAME}"


def run_commit_prefix(slug: str) -> str:
    """Subject prefix of an edition's run commits: ``newsletter(<slug>): ``.

    run_edition.sh follows it with ``<YYYY-MM-DD> daily run`` (or
    ``<YYYY-MM-DD> attempt N failed at <stage>``).
    """
    return f"newsletter({slug}): "


class MetaKey(StrEnum):
    """Keys in the state.db `meta` table that more than one module reads or writes."""

    # Run date for which fetch completed; fetch sets it, reset_day --refetch clears it.
    FETCH_DONE = "fetch_completed_date"
    # sha256 of the rendered rank prompt; prefilter compares and writes it,
    # migrate_layout seeds it from the legacy .rubric_hash file.
    RUBRIC_HASH = "rubric_hash"


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


class AttemptOutcome(StrEnum):
    """Outcome states for a pipeline run (run_attempts.outcome in state.db).

    Values match those used in db.py, run_attempt.py, run_edition.sh, and run.sh.
    This is the contract between Python accounting and shell orchestrators.
    """

    STARTED = "started"
    OK = "ok"
    FAILED = "failed"


# Sentinel value printed by run_attempt.py when there are no run attempts yet.
NO_ATTEMPT = "none"


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
