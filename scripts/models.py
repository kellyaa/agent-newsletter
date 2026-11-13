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

import json
from datetime import datetime, timezone
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


# The ranker's score range (integers, inclusive). rank's output schema and
# sanitizer, edition.yaml's score thresholds and the site's zod schema
# (site/src/content.config.ts) all use it (tests/test_score_range_single_source.py).
SCORE_MIN = 0
SCORE_MAX = 10
# edition.yaml `sections.<name>` keys compared against a score, so they must
# lie in [SCORE_MIN, SCORE_MAX]; a value above it can never be reached.
SECTION_SCORE_KEYS: tuple[str, ...] = ("featured_min", "appendix_min", "burst_trigger_score")


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

# Family-specific positive-int sources.yaml keys and the default fetch uses
# when a key is unset (None = no default: the adapter applies no cap). fetch
# reads them only via its `_family_int` helper (a bad value logs a warning and
# falls back to the default), check_edition rejects a bad value, and
# new_edition takes its fallbacks from here (tests/test_source_family_keys_single_source.py).
SOURCE_FAMILY_INT_KEYS: dict[SourceFamily, dict[str, int | None]] = {
    SourceFamily.ARXIV: {"max_results": 30},
    SourceFamily.HN: {"hours_back": 48, "min_points": 50},
    SourceFamily.HTML: {"max_items": None},
}
# Seconds between arXiv API requests (arXiv asks for at least 3 s). fetch
# sleeps this long between arxiv collectors, new_edition between probes.
ARXIV_REQUEST_DELAY_S = 3.0


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
# The folder name of both the base prompt templates (<repo>/prompts/) and an
# edition's prompt fragments (editions/<slug>/prompts/). scripts/prompts.py owns
# the rest of the prompt layout (tests/test_prompt_layout_single_source.py).
PROMPTS_DIRNAME = "prompts"
# One issue file per edition per day: editions/<slug>/issues/<YYYY-MM-DD>.md.
# write/backfill create it, publish/replay_writer read it, reset_day deletes it,
# and site/src/content.config.ts globs it (tests/test_issue_filename_single_source.py).
ISSUE_SUFFIX = ".md"
ISSUE_GLOB = f"*{ISSUE_SUFFIX}"
# The one edition that predates the layout: its state.db sat at the content
# root and its run commits read "newsletter: <date> ...".
LEGACY_EDITION = "agents"
LEGACY_RUN_COMMIT_PREFIX = "newsletter: "


def parse_instant(raw: str) -> datetime:
    """Parse a stored ISO-8601 instant into an aware datetime (zone-less => UTC).

    The one reading rule for ``items.published_at`` / ``fetched_at`` and
    ``$RUN_STARTED_AT``: a trailing ``Z`` is accepted (older Pythons' and
    feeds' spelling of UTC) and a naive value is taken as UTC, so callers can
    subtract it from an aware ``now`` without a TypeError. Raises
    ``ValueError`` for an unparseable string and ``AttributeError`` for a
    non-string; callers keep their own fallback
    (``tests/test_instant_parse_single_source.py``).
    """
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def encode_tags(tags: list[str]) -> str:
    """Encode a tag list for the ``items.tags`` column (a JSON array of strings).

    The one writer of the column's format; ``decode_tags`` is its one reader
    (``tests/test_tags_codec_single_source.py``).
    """
    return json.dumps([t for t in tags if isinstance(t, str)])


def decode_tags(raw: object) -> list[str]:
    """Decode an ``items.tags`` value into a list of strings, never raising.

    NULL, empty, unparseable, non-string and non-list values (a hand edit, a
    JSON string like ``"agents"``) decode to ``[]``, and non-string elements are
    dropped. A bare ``json.loads`` would hand ``"agents"`` to the frontmatter
    as six one-letter tags.
    """
    if not raw or not isinstance(raw, (str, bytes, bytearray)):
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [t for t in value if isinstance(t, str)]


def edition_state_db_relpath(slug: str) -> str:
    """``editions/<slug>/state.db``, relative to the content root (a git path)."""
    return f"{EDITIONS_DIRNAME}/{slug}/{STATE_DB_NAME}"


def issue_filename(date: str) -> str:
    """``<YYYY-MM-DD>.md``: the issue file for *date* inside an edition's issues dir.

    The stem is the ISO date, so sorting the stems sorts issues by day
    (``write.find_previous_newsletter`` relies on that).
    """
    return f"{date}{ISSUE_SUFFIX}"


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
    tags: str | None  # JSON-encoded list in DB: models.encode_tags / decode_tags
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


class IssueMetadata(TypedDict):
    """The ``metadata`` block of an issue file's frontmatter (and writer input).

    Built only by ``write.writer_metadata``. Its keys are the producer half of
    the site's ``metadata: z.object`` in ``site/src/content.config.ts``
    (``tests/test_issue_frontmatter_single_source.py``). ``items_considered``
    is None when a replay cannot reconstruct it.
    """
    items_considered: int | None
    items_featured_total: int
    items_featured_papers: int
    items_featured_news: int
    items_featured_blogs: int
    items_appendix: int


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
