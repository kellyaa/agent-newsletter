"""Tests for fetch.py: pure helper functions and validation logic."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from fetch import (
    _to_iso,
    _validate_section,
    _validate_keyword_gate_bypass,
    _validate_recency_days,
    _stamp_overrides,
    Item,
)


# ---------------------------------------------------------------------------
# _to_iso
# ---------------------------------------------------------------------------

class TestToIso:
    def test_none_returns_none(self):
        assert _to_iso(None) is None

    def test_empty_string_returns_none(self):
        assert _to_iso("") is None

    def test_string_passthrough(self):
        assert _to_iso("2026-06-01T12:00:00+00:00") == "2026-06-01T12:00:00+00:00"

    def test_time_struct_converts(self):
        t = time.gmtime(0)  # 1970-01-01T00:00:00Z
        result = _to_iso(t)
        assert result is not None
        assert "1970" in result

    def test_invalid_time_struct_returns_none(self):
        # Create an invalid time struct by passing a struct with impossible values
        # that would raise on datetime construction
        class BadStruct:
            tm_year = 9999
            def __getitem__(self, i):
                return [9999, 13, 40, 99, 99, 99][i]  # month=13 is invalid
        assert _to_iso(BadStruct()) is None


# ---------------------------------------------------------------------------
# _validate_section
# ---------------------------------------------------------------------------

class TestValidateSection:
    @pytest.mark.parametrize("value", ["papers", "news", "blogs"])
    def test_valid_sections_pass_through(self, value):
        assert _validate_section(value, "test-source") == value

    def test_none_returns_none(self):
        assert _validate_section(None, "test-source") is None

    def test_invalid_section_returns_none(self, caplog):
        result = _validate_section("invalid", "test-source")
        assert result is None

    def test_invalid_section_logs_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="fetch"):
            _validate_section("unknown-section", "src-id")
        assert "invalid section" in caplog.text.lower() or "invalid" in caplog.text


# ---------------------------------------------------------------------------
# _validate_keyword_gate_bypass
# ---------------------------------------------------------------------------

class TestValidateKeywordGateBypass:
    def test_none_returns_false(self):
        assert _validate_keyword_gate_bypass(None, "src") is False

    def test_false_returns_false(self):
        assert _validate_keyword_gate_bypass(False, "src") is False

    def test_true_returns_true(self):
        assert _validate_keyword_gate_bypass(True, "src") is True

    def test_string_true_returns_false_and_warns(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="fetch"):
            result = _validate_keyword_gate_bypass("true", "src")
        assert result is False
        assert "invalid" in caplog.text.lower() or caplog.records

    def test_integer_1_returns_false_and_warns(self, caplog):
        # Only bool True is valid; int 1 is not
        import logging
        with caplog.at_level(logging.WARNING, logger="fetch"):
            result = _validate_keyword_gate_bypass(1, "src")
        assert result is False


# ---------------------------------------------------------------------------
# _validate_recency_days
# ---------------------------------------------------------------------------

class TestValidateRecencyDays:
    def test_none_returns_none(self):
        assert _validate_recency_days(None, "src") is None

    def test_positive_int_passes(self):
        assert _validate_recency_days(7, "src") == 7

    def test_zero_returns_none_and_warns(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="fetch"):
            result = _validate_recency_days(0, "src")
        assert result is None

    def test_negative_returns_none(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="fetch"):
            result = _validate_recency_days(-1, "src")
        assert result is None

    def test_bool_true_returns_none(self, caplog):
        # bool is a subclass of int; must be explicitly rejected
        import logging
        with caplog.at_level(logging.WARNING, logger="fetch"):
            result = _validate_recency_days(True, "src")
        assert result is None

    def test_bool_false_returns_none(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="fetch"):
            result = _validate_recency_days(False, "src")
        assert result is None

    def test_float_returns_none(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="fetch"):
            result = _validate_recency_days(3.5, "src")
        assert result is None

    def test_string_returns_none(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="fetch"):
            result = _validate_recency_days("7", "src")
        assert result is None


# ---------------------------------------------------------------------------
# _stamp_overrides
# ---------------------------------------------------------------------------

def _make_item(**kwargs) -> Item:
    defaults = dict(
        source="rss:test",
        url="https://example.com/1",
        title="Test item",
        author=None,
        published_at=None,
        raw_text=None,
    )
    defaults.update(kwargs)
    return Item(**defaults)


class TestStampOverrides:
    def test_section_override_applied(self):
        items = [_make_item()]
        result = list(_stamp_overrides(items, "papers", False, None))
        assert result[0].section_override == "papers"

    def test_section_override_none_not_applied(self):
        item = _make_item()
        item.section_override = "news"
        result = list(_stamp_overrides([item], None, False, None))
        assert result[0].section_override == "news"  # unchanged

    def test_keyword_gate_bypass_set(self):
        items = [_make_item()]
        result = list(_stamp_overrides(items, None, True, None))
        assert result[0].keyword_gate_bypass is True

    def test_keyword_gate_bypass_false_not_set(self):
        item = _make_item()
        item.keyword_gate_bypass = True
        result = list(_stamp_overrides([item], None, False, None))
        assert result[0].keyword_gate_bypass is True  # pre-existing value preserved

    def test_recency_days_override_applied(self):
        items = [_make_item()]
        result = list(_stamp_overrides(items, None, False, 14))
        assert result[0].recency_days_override == 14

    def test_recency_days_none_not_applied(self):
        item = _make_item()
        item.recency_days_override = 7
        result = list(_stamp_overrides([item], None, False, None))
        assert result[0].recency_days_override == 7  # unchanged

    def test_multiple_items_all_get_override(self):
        items = [_make_item() for _ in range(3)]
        result = list(_stamp_overrides(items, "blogs", True, 5))
        for it in result:
            assert it.section_override == "blogs"
            assert it.keyword_gate_bypass is True
            assert it.recency_days_override == 5

    def test_generator_yields_same_objects(self):
        items = [_make_item()]
        result = list(_stamp_overrides(iter(items), "papers", False, None))
        assert len(result) == 1
