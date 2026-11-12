"""The site's edition.yaml reader (site/src/scripts/editions.ts) agrees with edition.load.

The site parses ``editions/<slug>/edition.yaml`` itself at build time. Before
this, its rules had drifted from ``scripts/edition.py``: the slug regex
``[a-z0-9-]+`` accepted ``news-`` / ``-news`` / ``news--daily`` and ``order``
accepted any finite number (``1.5``), so a folder the pipeline lists as
``invalid`` (run.sh fails it with an alert) still built and showed on the
site. A status added to ``models.EditionStatus`` passed pytest and broke the
site build. These tests pin:

* ``EDITION_STATUSES`` equals ``models.EditionStatus`` (values and order);
* the site's ``SLUG_RE`` is ``edition.SLUG_RE`` and both accept and reject the
  same sample slugs;
* ``order`` is checked with ``Number.isInteger``;
* every field the site reads is a required edition.yaml key;
* the site's ``LEGACY_EDITION`` equals ``models.LEGACY_EDITION``;
* no other site source re-declares ``SLUG_RE`` or the status list, or spells
  the legacy slug as a string literal.
"""
from __future__ import annotations

import re
from pathlib import Path

import edition
from models import LEGACY_EDITION, EditionStatus

REPO = Path(__file__).resolve().parents[1]
SITE_SRC = REPO / "site" / "src"
EDITIONS_TS = SITE_SRC / "scripts" / "editions.ts"

SAMPLE_SLUGS = [
    "agents", "engineering-with-ai", "ai-2", "a", "news-", "-news",
    "news--daily", "News", "news_daily", "", "näws",
]


def _ts() -> str:
    return EDITIONS_TS.read_text()


def _site_slug_re() -> re.Pattern[str]:
    m = re.search(r"export const SLUG_RE = /\^(.*?)\$/;", _ts())
    assert m, "SLUG_RE (anchored /^...$/) not found in editions.ts"
    return re.compile(m.group(1))


def _site_sources() -> list[Path]:
    return sorted(
        p for p in SITE_SRC.rglob("*")
        if p.suffix in {".ts", ".astro", ".js", ".mjs"} and p != EDITIONS_TS
    )


def test_statuses_match_models_edition_status():
    m = re.search(r"export const EDITION_STATUSES = \[(.*?)\] as const;", _ts())
    assert m, "EDITION_STATUSES not found in editions.ts"
    assert re.findall(r'"(\w+)"', m.group(1)) == [s.value for s in EditionStatus]


def test_slug_regex_matches_edition_slug_re():
    assert _site_slug_re().pattern == edition.SLUG_RE.pattern


def test_slug_regex_accepts_and_rejects_the_same_slugs():
    site = _site_slug_re()
    for slug in SAMPLE_SLUGS:
        assert bool(site.fullmatch(slug)) == bool(edition.SLUG_RE.fullmatch(slug)), slug


def test_order_must_be_an_integer():
    assert "Number.isInteger(order)" in _ts()
    assert "Number.isFinite(order)" not in _ts()


def test_site_reads_only_required_edition_keys():
    read = set(re.findall(r'\bstr\("(\w+)"\)', _ts())) | set(re.findall(r"\bd\.(\w+)", _ts()))
    assert read == {"slug", "name", "tagline", "status", "order"}
    assert read <= set(edition._TOP_REQUIRED)


def test_legacy_edition_matches_models():
    m = re.search(r'export const LEGACY_EDITION = "([^"]+)";', _ts())
    assert m, "LEGACY_EDITION not found in editions.ts"
    assert m.group(1) == LEGACY_EDITION


def test_no_other_site_file_respells_the_edition_contract():
    slug_re = re.compile(r"\bSLUG_RE\s*=")
    statuses = re.compile(r'\[\s*"draft"\s*,\s*"live"')
    legacy = re.compile(rf"""["']{re.escape(LEGACY_EDITION)}["']""")
    offenders = [
        str(p.relative_to(REPO)) for p in _site_sources()
        if slug_re.search(t := p.read_text()) or statuses.search(t) or legacy.search(t)
    ]
    assert offenders == [], f"take the edition contract from scripts/editions.ts: {offenders}"
