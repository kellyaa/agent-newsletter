"""Load and validate an edition: a folder of editorial config under editions/<slug>/.

An edition is one newsletter subject. Everything subject-specific (sources,
keyword gate, tag vocabulary, thresholds, reader profile, prompt wording) lives
in ``editions/<slug>/``; the pipeline code is shared.

    editions/<slug>/edition.yaml       identity, reader profile, keywords, tags, sections
    editions/<slug>/sources.yaml       feed list
    editions/<slug>/prompts/           fragments filled into prompts/{rank,write}.md

Selection at runtime is the ``EDITION`` env var (see :func:`current`). There is
deliberately no default edition: a silent default is how one edition's data ends
up written into another's database.

Validation is strict and runs before any stage does real work, so a bad config
stops the run before fetch rather than halfway through rank.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
EDITIONS_DIR = REPO_ROOT / "editions"
# Rendered prompts and other per-edition debug output go under LOGS_ROOT/<slug>/.
# Module-level so tests can point it at a tmp dir and never write into logs/.
LOGS_ROOT = REPO_ROOT / "logs"

STATUSES = ("draft", "live", "paused")
SECTION_NAMES = ("papers", "news", "blogs")

_SLUG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_TAG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")

_TOP_REQUIRED = (
    "slug", "name", "tagline", "status", "order",
    "reader_profile", "keywords", "tags", "sections",
)
_TOP_OPTIONAL = ("downweighted",)
_SECTION_REQUIRED = ("featured_min", "appendix_min", "cap")
_SECTION_BURST = ("burst_cap", "burst_trigger_score", "burst_trigger_count")


class EditionError(Exception):
    """The edition is missing, unselected or its config is invalid."""


@dataclass(frozen=True)
class EditionPaths:
    """Where an edition's files live.

    Every path the pipeline derives from an edition goes through
    :meth:`Edition.paths`, so moving files (e.g. the content-branch layout
    migration) only changes that one function.
    """
    root: Path
    config: Path
    sources: Path
    prompt_fragments: Path
    logs: Path


@dataclass(frozen=True)
class Edition:
    slug: str
    name: str
    tagline: str
    status: str
    order: int
    reader_profile: str
    downweighted: tuple[str, ...]
    keywords: tuple[str, ...]
    tags: tuple[str, ...]
    # Plain nested dict (section -> rule -> int); callers get copies via
    # section_rules() so the frozen dataclass is not mutated through it.
    sections: Any

    def paths(self) -> EditionPaths:
        root = EDITIONS_DIR / self.slug
        return EditionPaths(
            root=root,
            config=root / "edition.yaml",
            sources=root / "sources.yaml",
            prompt_fragments=root / "prompts",
            logs=LOGS_ROOT / self.slug,
        )

    def section_rules(self) -> dict[str, dict[str, int]]:
        return {name: dict(rules) for name, rules in self.sections.items()}

    def valid_tags(self) -> set[str]:
        return set(self.tags)

    def keyword_regexes(self) -> list[str]:
        return list(self.keywords)


@dataclass(frozen=True)
class RuntimePaths:
    """Where one edition's mutable pipeline files live."""
    state_db: Path
    issues_dir: Path
    rubric_hash: Path
    candidates: Path
    ranked: Path


def selected_slug() -> str:
    """The ``EDITION`` env var, or :class:`EditionError` if unset. No default."""
    slug = os.environ.get("EDITION", "").strip()
    if not slug:
        raise EditionError(
            "EDITION is not set. Select an edition explicitly, e.g. "
            "`EDITION=agents uv run scripts/<stage>.py` (run.sh does this for you)."
        )
    return slug


def runtime_paths(content_root: Path, slug: str | None = None) -> RuntimePaths:
    """Resolve an edition's state DB, issue folder and debug artifacts.

    This is the single place that decides where those live. Today every edition
    still uses the legacy single-edition layout (``state.db`` and
    ``site/src/content/issues/`` at the content root; debug artifacts at the repo
    root), so *slug* is validated but does not yet affect the result. The
    content-layout migration (#301) changes only this function.
    """
    slug = slug or selected_slug()
    if not _SLUG_RE.fullmatch(slug):
        raise EditionError(f"invalid edition slug {slug!r}")
    return RuntimePaths(
        state_db=content_root / "state.db",
        issues_dir=content_root / "site" / "src" / "content" / "issues",
        rubric_hash=REPO_ROOT / ".rubric_hash",
        candidates=REPO_ROOT / "candidates.json",
        ranked=REPO_ROOT / "ranked.json",
    )


def _fail(path: Path, msg: str) -> EditionError:
    return EditionError(f"{path}: {msg}")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _str_list(path: Path, key: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise _fail(path, f"{key!r} must be a list of non-empty strings")
    return tuple(value)


def _validate_sections(path: Path, raw: Any) -> dict[str, dict[str, int]]:
    if not isinstance(raw, dict):
        raise _fail(path, "'sections' must be a mapping")
    if set(raw) != set(SECTION_NAMES):
        raise _fail(
            path,
            f"'sections' must have exactly {list(SECTION_NAMES)}, got {sorted(map(str, raw))}",
        )
    out: dict[str, dict[str, int]] = {}
    for name in SECTION_NAMES:
        rules = raw[name]
        if not isinstance(rules, dict):
            raise _fail(path, f"sections.{name} must be a mapping")
        allowed = set(_SECTION_REQUIRED) | set(_SECTION_BURST)
        unknown = set(rules) - allowed
        if unknown:
            raise _fail(path, f"sections.{name}: unknown keys {sorted(map(str, unknown))}")
        missing = [k for k in _SECTION_REQUIRED if k not in rules]
        if missing:
            raise _fail(path, f"sections.{name}: missing keys {missing}")
        burst_present = [k for k in _SECTION_BURST if k in rules]
        if burst_present and len(burst_present) != len(_SECTION_BURST):
            raise _fail(
                path, f"sections.{name}: burst_* keys must be given together {list(_SECTION_BURST)}"
            )
        for k, v in rules.items():
            if not _is_int(v) or v < 0:
                raise _fail(path, f"sections.{name}.{k} must be a non-negative integer")
        if rules["appendix_min"] > rules["featured_min"]:
            raise _fail(
                path,
                f"sections.{name}: appendix_min ({rules['appendix_min']}) must be "
                f"<= featured_min ({rules['featured_min']})",
            )
        out[name] = dict(rules)
    return out


def load(slug: str, editions_dir: Path | None = None) -> Edition:
    """Load and strictly validate ``editions/<slug>/edition.yaml``."""
    base = editions_dir if editions_dir is not None else EDITIONS_DIR
    path = base / slug / "edition.yaml"
    if not path.is_file():
        raise EditionError(f"unknown edition {slug!r}: {path} does not exist")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise _fail(path, f"invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise _fail(path, "top level must be a mapping")

    unknown = set(raw) - set(_TOP_REQUIRED) - set(_TOP_OPTIONAL)
    if unknown:
        raise _fail(path, f"unknown keys {sorted(map(str, unknown))}")
    missing = [k for k in _TOP_REQUIRED if k not in raw]
    if missing:
        raise _fail(path, f"missing keys {missing}")

    for key in ("slug", "name", "tagline", "reader_profile"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise _fail(path, f"{key!r} must be a non-empty string")

    if not _SLUG_RE.fullmatch(raw["slug"]):
        raise _fail(path, f"slug {raw['slug']!r} must match [a-z0-9-]+ (lowercase, hyphen-separated)")
    if raw["slug"] != slug:
        raise _fail(path, f"slug {raw['slug']!r} does not match folder name {slug!r}")

    if raw["status"] not in STATUSES:
        raise _fail(path, f"status {raw['status']!r} must be one of {list(STATUSES)}")
    if not _is_int(raw["order"]):
        raise _fail(path, "'order' must be an integer")

    keywords = _str_list(path, "keywords", raw["keywords"])
    if not keywords:
        raise _fail(path, "'keywords' must not be empty")
    for kw in keywords:
        try:
            re.compile(kw)
        except re.error as e:
            raise _fail(path, f"keyword {kw!r} does not compile: {e}") from e

    tags = _str_list(path, "tags", raw["tags"])
    if not tags:
        raise _fail(path, "'tags' must not be empty")
    for t in tags:
        if not _TAG_RE.fullmatch(t):
            raise _fail(path, f"tag {t!r} must be kebab-case")
    if len(set(tags)) != len(tags):
        raise _fail(path, "'tags' contains duplicates")

    downweighted = _str_list(path, "downweighted", raw.get("downweighted", []))
    sections = _validate_sections(path, raw["sections"])

    return Edition(
        slug=raw["slug"],
        name=raw["name"],
        tagline=raw["tagline"],
        status=raw["status"],
        order=raw["order"],
        reader_profile=raw["reader_profile"].strip(),
        downweighted=downweighted,
        keywords=keywords,
        tags=tags,
        sections=sections,
    )


@lru_cache(maxsize=None)
def _load_cached(slug: str) -> Edition:
    return load(slug)


def current() -> Edition:
    """The edition selected by the ``EDITION`` env var.

    Raises :class:`EditionError` when it is unset or empty. There is no default.
    """
    return _load_cached(selected_slug())
