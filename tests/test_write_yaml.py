"""Tests for write.py: YAML frontmatter serializer and assemble_issue."""
from __future__ import annotations

import pytest
import yaml  # PyYAML for round-trip validation

from write import (
    emit_yaml_frontmatter,
    assemble_issue,
)


# ---------------------------------------------------------------------------
# emit_yaml_frontmatter (round-trip with PyYAML)
# ---------------------------------------------------------------------------

class TestEmitYamlFrontmatter:
    """Emitted frontmatter must be parseable by PyYAML and round-trip correctly."""

    def _parse_frontmatter(self, text: str) -> dict:
        """Extract the YAML block between --- fences and parse it."""
        assert text.startswith("---\n"), "must start with ---"
        end = text.find("\n---\n", 4)
        assert end != -1, "must have closing ---"
        block = text[4:end]
        return yaml.safe_load(block)

    def test_simple_dict_round_trips(self):
        data = {"date": "2026-06-01", "theme": "Agents everywhere"}
        result = emit_yaml_frontmatter(data)
        parsed = self._parse_frontmatter(result)
        assert parsed["date"] == "2026-06-01"
        assert parsed["theme"] == "Agents everywhere"

    def test_none_values_round_trip(self):
        data = {"theme": None}
        result = emit_yaml_frontmatter(data)
        parsed = self._parse_frontmatter(result)
        assert parsed["theme"] is None

    def test_nested_list_of_dicts(self):
        data = {
            "featured": [
                {"id": "abc", "score": 9, "title": "Paper on agents"},
            ]
        }
        result = emit_yaml_frontmatter(data)
        parsed = self._parse_frontmatter(result)
        assert parsed["featured"][0]["id"] == "abc"
        assert parsed["featured"][0]["score"] == 9

    def test_special_chars_in_title(self):
        data = {"title": 'He said "LLMs are great" & I agree'}
        result = emit_yaml_frontmatter(data)
        parsed = self._parse_frontmatter(result)
        assert parsed["title"] == 'He said "LLMs are great" & I agree'

    def test_backslash_in_string(self):
        data = {"path": "C:\\Users\\foo"}
        result = emit_yaml_frontmatter(data)
        parsed = self._parse_frontmatter(result)
        assert parsed["path"] == "C:\\Users\\foo"

    def test_newline_in_string(self):
        data = {"summary": "line one\nline two"}
        result = emit_yaml_frontmatter(data)
        parsed = self._parse_frontmatter(result)
        assert parsed["summary"] == "line one\nline two"

    def test_empty_list(self):
        data = {"tags": []}
        result = emit_yaml_frontmatter(data)
        parsed = self._parse_frontmatter(result)
        assert parsed["tags"] == []

    def test_integer_value(self):
        data = {"score": 8}
        result = emit_yaml_frontmatter(data)
        parsed = self._parse_frontmatter(result)
        assert parsed["score"] == 8

    def test_output_has_opening_and_closing_fence(self):
        result = emit_yaml_frontmatter({"x": 1})
        assert result.startswith("---\n")
        assert "\n---\n" in result


# ---------------------------------------------------------------------------
# assemble_issue
# ---------------------------------------------------------------------------

class TestAssembleIssue:
    """assemble_issue() builds a valid frontmatter+body MD file."""

    def _parse_frontmatter(self, text: str) -> dict:
        assert text.startswith("---\n")
        end = text.find("\n---\n", 4)
        assert end != -1
        return yaml.safe_load(text[4:end])

    def _make_featured(self, n: int = 1) -> list[dict]:
        return [
            {
                "id": f"id-{i}",
                "section": "papers",
                "source": "arxiv:cs.AI",
                "url": f"https://arxiv.org/abs/2401.{i:05d}",
                "title": f"Paper {i}",
                "author": "Smith et al.",
                "published_at": "2026-06-01T00:00:00Z",
                "raw_text": "Abstract text",
                "score": 8,
                "tags": ["research"],
                "why": "Novel approach",
            }
            for i in range(n)
        ]

    def _make_appendix(self) -> dict:
        return {"papers": [], "news": [], "blogs": []}

    def _make_metadata(self, n_featured: int = 1) -> dict:
        return {
            "items_considered": 50,
            "items_featured_total": n_featured,
            "items_featured_papers": n_featured,
            "items_featured_news": 0,
            "items_featured_blogs": 0,
            "items_appendix": 0,
        }

    def _make_writer_output(self, featured: list[dict]) -> dict:
        return {
            "theme": "Agents taking over",
            "items": [
                {
                    "id": it["id"],
                    "summary": f"Summary for {it['id']}",
                    "takeaway": "Key insight",
                    "open_question": None,
                }
                for it in featured
            ],
        }

    def test_output_starts_with_frontmatter_fence(self):
        f = self._make_featured(1)
        issue = assemble_issue("2026-06-01", f, self._make_appendix(),
                               self._make_metadata(), self._make_writer_output(f))
        assert issue.startswith("---\n")

    def test_date_in_frontmatter(self):
        f = self._make_featured(1)
        issue = assemble_issue("2026-06-01", f, self._make_appendix(),
                               self._make_metadata(), self._make_writer_output(f))
        fm = self._parse_frontmatter(issue)
        assert fm["date"] == "2026-06-01"

    def test_featured_items_have_correct_structure(self):
        f = self._make_featured(2)
        issue = assemble_issue("2026-06-01", f, self._make_appendix(),
                               self._make_metadata(2), self._make_writer_output(f))
        fm = self._parse_frontmatter(issue)
        assert len(fm["featured"]) == 2
        for entry in fm["featured"]:
            assert "id" in entry
            assert "url" in entry
            assert "summary" in entry

    def test_url_comes_from_db_not_llm(self):
        """URLs must come from the featured items dict, not the writer output."""
        f = self._make_featured(1)
        f[0]["url"] = "https://arxiv.org/abs/2401.99999"
        issue = assemble_issue("2026-06-01", f, self._make_appendix(),
                               self._make_metadata(), self._make_writer_output(f))
        assert "2401.99999" in issue

    def test_missing_writer_summary_falls_back_to_title(self):
        f = self._make_featured(1)
        writer_output = {"theme": None, "items": []}  # no summary for item
        issue = assemble_issue("2026-06-01", f, self._make_appendix(),
                               self._make_metadata(), writer_output)
        fm = self._parse_frontmatter(issue)
        # The fallback is the title
        assert fm["featured"][0]["summary"] == f[0]["title"]

    def test_theme_in_frontmatter(self):
        f = self._make_featured(1)
        wo = self._make_writer_output(f)
        wo["theme"] = "LLM agents go mainstream"
        issue = assemble_issue("2026-06-01", f, self._make_appendix(),
                               self._make_metadata(), wo)
        fm = self._parse_frontmatter(issue)
        assert fm["theme"] == "LLM agents go mainstream"

    def test_none_theme_allowed(self):
        f = self._make_featured(1)
        wo = {"theme": None, "items": self._make_writer_output(f)["items"]}
        issue = assemble_issue("2026-06-01", f, self._make_appendix(),
                               self._make_metadata(), wo)
        fm = self._parse_frontmatter(issue)
        assert fm["theme"] is None

    def test_appendix_sections_present(self):
        f = self._make_featured(1)
        appendix = {
            "papers": [{"id": "p1", "section": "papers", "source": "arxiv:cs.AI",
                        "url": "https://arxiv.org/abs/2401.00001", "title": "Old paper"}],
            "news": [],
            "blogs": [],
        }
        issue = assemble_issue("2026-06-01", f, appendix, self._make_metadata(),
                               self._make_writer_output(f))
        fm = self._parse_frontmatter(issue)
        assert len(fm["appendix"]["papers"]) == 1

    def test_score_is_integer_in_frontmatter(self):
        f = self._make_featured(1)
        f[0]["score"] = 9
        issue = assemble_issue("2026-06-01", f, self._make_appendix(),
                               self._make_metadata(), self._make_writer_output(f))
        fm = self._parse_frontmatter(issue)
        assert isinstance(fm["featured"][0]["score"], int)
