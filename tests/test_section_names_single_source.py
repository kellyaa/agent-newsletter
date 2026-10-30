"""The newsletter's section names are defined once, in models.Section.

edition.py (validates edition.yaml `sections`), check_edition.py (validates
sources.yaml `section:` overrides) and fetch.py (applies those overrides) each
used to carry their own literal copy of ("papers", "news", "blogs"). Adding or
renaming a section in one place and not the others would let an edition pass
the linter yet be rejected at run time, or the reverse.
"""
from __future__ import annotations

import check_edition
import edition
import fetch
from models import Section


def test_edition_section_names_come_from_models():
    assert edition.SECTION_NAMES == tuple(s.value for s in Section)
    assert all(type(n) is str for n in edition.SECTION_NAMES)


def test_check_edition_reuses_edition_section_names():
    assert check_edition.SECTIONS is edition.SECTION_NAMES


def test_fetch_valid_sections_match_models():
    assert fetch.VALID_SECTIONS == {s.value for s in Section}
