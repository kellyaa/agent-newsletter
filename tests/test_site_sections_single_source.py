"""The site spells the newsletter section vocabulary once: site/src/scripts/sections.ts.

Before this, ``"papers" | "news" | "blogs"`` and the Papers/News/Blogs labels
were re-typed in content.config.ts, the issue page, the tag page,
FeaturedItem, AppendixSection and MonthView. Adding or renaming a section in
``models.Section`` passed pytest and broke (or silently dropped) the site.
These tests pin:

* ``sections.ts`` SECTIONS equals ``models.Section`` (values and order).
* ``SECTION_DISPLAY`` has one entry per section.
* no other site source file spells the section-name union or re-declares
  a SECTIONS array.
"""
from __future__ import annotations

import re
from pathlib import Path

from models import Section

REPO = Path(__file__).resolve().parents[1]
SITE_SRC = REPO / "site" / "src"
SECTIONS_TS = SITE_SRC / "scripts" / "sections.ts"


def _site_sources() -> list[Path]:
    return sorted(
        p for p in SITE_SRC.rglob("*")
        if p.suffix in {".ts", ".astro"} and p != SECTIONS_TS
    )


def test_sections_ts_matches_models_section():
    m = re.search(r"export const SECTIONS = \[(.*?)\] as const;", SECTIONS_TS.read_text())
    assert m, "SECTIONS not found in sections.ts"
    assert re.findall(r'"(\w+)"', m.group(1)) == [s.value for s in Section]


def test_section_display_covers_every_section():
    m = re.search(r"SECTION_DISPLAY[^=]*=\s*\{(.*?)\n\};", SECTIONS_TS.read_text(), re.S)
    assert m, "SECTION_DISPLAY not found in sections.ts"
    keys = re.findall(r"^\s*(\w+):\s*\{", m.group(1), re.M)
    assert keys == [s.value for s in Section]


def test_no_site_file_respells_section_union():
    union = re.compile(r'"(?:papers|news|blogs)"\s*\|\s*"(?:papers|news|blogs)"')
    redeclared = re.compile(r"\bconst SECTIONS\s*=")
    offenders = [
        str(p.relative_to(REPO)) for p in _site_sources()
        if union.search(p.read_text()) or redeclared.search(p.read_text())
    ]
    assert offenders == [], f"spell sections via scripts/sections.ts: {offenders}"


def test_no_site_file_hardcodes_section_labels_list():
    label = re.compile(r'label:\s*"(?:Papers|News|Blogs)"')
    offenders = [
        str(p.relative_to(REPO)) for p in _site_sources() if label.search(p.read_text())
    ]
    assert offenders == [], f"take labels from SECTION_DISPLAY: {offenders}"
