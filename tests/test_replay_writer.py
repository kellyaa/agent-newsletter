"""Tests for replay_writer.py: parse_issue_frontmatter() and load_from_issue_file()."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest
import yaml

from replay_writer import parse_issue_frontmatter, load_from_issue_file


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    import db as db_mod
    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    yield conn
    conn.close()


def _write_issue(issues_dir: Path, date: str, featured_ids: list[str],
                 appendix: dict | None = None) -> None:
    """Write a minimal valid Astro issue MD file."""
    if appendix is None:
        appendix = {"papers": [], "news": [], "blogs": []}
    lines = [
        "---",
        f'date: "{date}"',  # quoted to stay a string in YAML
        "featured:",
    ]
    for item_id in featured_ids:
        lines += [
            f"- id: {item_id}",
            f"  source: arxiv:cs.AI",
            f"  url: https://arxiv.org/abs/{item_id}",
            f"  title: Title for {item_id}",
        ]
    if not featured_ids:
        lines.append("  []")
        lines[-2] = "featured: []"
        del lines[-1]
    lines += [
        "appendix:",
        "  papers: []",
        "  news: []",
        "  blogs: []",
        "---",
        f"Generated {date}.",
    ]
    (issues_dir / f"{date}.md").write_text("\n".join(lines) + "\n")


def _insert_item(
    conn,
    item_id: str,
    section: str = "papers",
    score: int = 8,
    tags: list | None = None,
    raw_text: str = "Abstract here",
    title: str = "Test Paper",
    status: str = "published",
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO items (
            id, source, url, canonical_url, title, author, published_at,
            fetched_at, raw_text, score, tags, why, status, section,
            first_seen_date, last_seen_date, appearances
        ) VALUES (
            ?, 'arxiv:cs.AI', 'https://arxiv.org/abs/' || ?, 'https://arxiv.org/abs/' || ?,
            ?, 'Author', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z',
            ?, ?, ?, 'good', ?, ?,
            '2026-06-01', '2026-06-01', 1
        )
        """,
        (item_id, item_id, item_id, title, raw_text,
         score, json.dumps(tags or []), status, section),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# parse_issue_frontmatter()
# ---------------------------------------------------------------------------

class TestParseIssueFrontmatter:
    def test_parses_valid_frontmatter(self, tmp_path):
        issue = tmp_path / "2026-06-01.md"
        # Use quoted string to keep YAML from converting to datetime.date
        issue.write_text('---\ndate: "2026-06-01"\nfeatured: []\n---\nBody text\n')
        result = parse_issue_frontmatter(issue)
        assert result["date"] == "2026-06-01"
        assert result["featured"] == []

    def test_parses_date_as_yaml_type(self, tmp_path):
        """YAML parses unquoted dates as datetime.date — document the behavior."""
        issue = tmp_path / "2026-06-01.md"
        issue.write_text("---\ndate: 2026-06-01\nfeatured: []\n---\nBody\n")
        result = parse_issue_frontmatter(issue)
        # yaml.safe_load converts bare date strings to datetime.date
        assert result["date"] == datetime.date(2026, 6, 1)

    def test_raises_on_missing_opening_fence(self, tmp_path):
        issue = tmp_path / "bad.md"
        issue.write_text("no frontmatter here\n")
        with pytest.raises(ValueError, match="missing opening frontmatter fence"):
            parse_issue_frontmatter(issue)

    def test_raises_on_missing_closing_fence(self, tmp_path):
        issue = tmp_path / "bad.md"
        issue.write_text("---\ndate: 2026-06-01\n# no closing fence\n")
        with pytest.raises(ValueError, match="missing closing frontmatter fence"):
            parse_issue_frontmatter(issue)

    def test_parses_featured_list(self, tmp_path):
        issue = tmp_path / "2026-06-01.md"
        issue.write_text(
            "---\n"
            'date: "2026-06-01"\n'
            "theme: \"Agents everywhere\"\n"
            "featured:\n"
            "- id: p1\n"
            "  score: 9\n"
            "appendix:\n"
            "  papers: []\n"
            "  news: []\n"
            "  blogs: []\n"
            "---\n"
            "Body\n"
        )
        result = parse_issue_frontmatter(issue)
        assert result["theme"] == "Agents everywhere"
        assert result["featured"][0]["id"] == "p1"
        assert result["featured"][0]["score"] == 9

    def test_body_after_fence_not_parsed(self, tmp_path):
        issue = tmp_path / "2026-06-01.md"
        issue.write_text(
            '---\ndate: "2026-06-01"\nfeatured: []\n---\n'
            "this: looks like yaml but is ignored\n"
        )
        result = parse_issue_frontmatter(issue)
        # Body after second fence is not included in parsed dict
        assert "this" not in result

    def test_returns_dict(self, tmp_path):
        issue = tmp_path / "2026-06-01.md"
        issue.write_text('---\ndate: "2026-06-01"\n---\n')
        result = parse_issue_frontmatter(issue)
        assert isinstance(result, dict)

    def test_empty_frontmatter_returns_none(self, tmp_path):
        issue = tmp_path / "empty.md"
        # Need a blank line between fences for "\n---\n" to match
        issue.write_text("---\n\n---\nBody\n")
        result = parse_issue_frontmatter(issue)
        # yaml.safe_load('') returns None; the function returns whatever yaml gives
        assert result is None


# ---------------------------------------------------------------------------
# load_from_issue_file()
# ---------------------------------------------------------------------------

class TestLoadFromIssueFile:
    def test_raises_when_issue_file_absent(self, db, tmp_path, monkeypatch):
        import replay_writer as rw_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)
        with pytest.raises(FileNotFoundError):
            load_from_issue_file(db, "2026-06-01")

    def test_returns_empty_featured_when_no_db_matches(self, db, tmp_path, monkeypatch):
        """Featured IDs in frontmatter that are missing from state.db are skipped."""
        import replay_writer as rw_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _write_issue(issues_dir, "2026-06-01", ["missing-id"])
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)
        featured, _, _ = load_from_issue_file(db, "2026-06-01")
        assert featured == []

    def test_reconstructs_featured_from_db(self, db, tmp_path, monkeypatch):
        import replay_writer as rw_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _insert_item(db, "p1", section="papers", score=9, title="My Paper")
        _write_issue(issues_dir, "2026-06-01", ["p1"])
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)
        featured, _, _ = load_from_issue_file(db, "2026-06-01")
        assert len(featured) == 1
        assert featured[0]["id"] == "p1"
        assert featured[0]["title"] == "My Paper"
        assert featured[0]["score"] == 9
        assert featured[0]["section"] == "papers"

    def test_featured_item_fields_complete(self, db, tmp_path, monkeypatch):
        import replay_writer as rw_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _insert_item(db, "p1", section="news", score=7, tags=["evals"],
                     raw_text="Short abstract", title="News Article")
        _write_issue(issues_dir, "2026-06-01", ["p1"])
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)
        featured, _, _ = load_from_issue_file(db, "2026-06-01")
        item = featured[0]
        assert "id" in item
        assert "source" in item
        assert "url" in item
        assert "title" in item
        assert "author" in item
        assert "published_at" in item
        assert "raw_text" in item
        assert "score" in item
        assert "tags" in item
        assert "why" in item
        assert "section" in item

    def test_raw_text_truncated_at_max(self, db, tmp_path, monkeypatch):
        import replay_writer as rw_mod
        from write import RAW_TEXT_MAX
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        long_text = "x" * (RAW_TEXT_MAX + 200)
        _insert_item(db, "p1", raw_text=long_text)
        _write_issue(issues_dir, "2026-06-01", ["p1"])
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)
        featured, _, _ = load_from_issue_file(db, "2026-06-01")
        assert featured[0]["raw_text"].endswith("\n...[truncated]")
        assert len(featured[0]["raw_text"]) <= RAW_TEXT_MAX + len("\n...[truncated]")

    def test_raw_text_not_truncated_when_short(self, db, tmp_path, monkeypatch):
        import replay_writer as rw_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _insert_item(db, "p1", raw_text="Short abstract")
        _write_issue(issues_dir, "2026-06-01", ["p1"])
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)
        featured, _, _ = load_from_issue_file(db, "2026-06-01")
        assert featured[0]["raw_text"] == "Short abstract"

    def test_tags_parsed_from_json(self, db, tmp_path, monkeypatch):
        import replay_writer as rw_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _insert_item(db, "p1", tags=["evals", "research"])
        _write_issue(issues_dir, "2026-06-01", ["p1"])
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)
        featured, _, _ = load_from_issue_file(db, "2026-06-01")
        assert featured[0]["tags"] == ["evals", "research"]

    def test_appendix_from_frontmatter(self, db, tmp_path, monkeypatch):
        """Appendix comes from frontmatter (no DB lookup needed)."""
        import replay_writer as rw_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _insert_item(db, "p1")
        # Build issue with appendix entry
        issue_md = (
            '---\ndate: "2026-06-01"\n'
            "featured:\n- id: p1\n  source: arxiv:cs.AI\n  url: https://arxiv.org/abs/p1\n  title: T\n"
            "appendix:\n"
            "  papers:\n"
            "  - id: a1\n    source: arxiv:cs.AI\n    url: https://arxiv.org/abs/a1\n    title: Appendix Paper\n"
            "  news: []\n"
            "  blogs: []\n"
            "---\nBody\n"
        )
        (issues_dir / "2026-06-01.md").write_text(issue_md)
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)
        _, appendix, metadata = load_from_issue_file(db, "2026-06-01")
        assert len(appendix["papers"]) == 1
        assert appendix["papers"][0]["id"] == "a1"
        assert metadata["items_appendix"] == 1

    def test_metadata_items_considered_is_none(self, db, tmp_path, monkeypatch):
        """replay_writer cannot determine items_considered — it is always None."""
        import replay_writer as rw_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _insert_item(db, "p1")
        _write_issue(issues_dir, "2026-06-01", ["p1"])
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)
        _, _, metadata = load_from_issue_file(db, "2026-06-01")
        assert metadata["items_considered"] is None

    def test_featured_sorted_by_section_rank_then_score(self, db, tmp_path, monkeypatch):
        """Papers (rank 0) before news (rank 1), higher score first within section."""
        import replay_writer as rw_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _insert_item(db, "p-lo", section="papers", score=7)
        _insert_item(db, "p-hi", section="papers", score=9)
        _insert_item(db, "n1", section="news", score=8)
        _write_issue(issues_dir, "2026-06-01", ["n1", "p-lo", "p-hi"])
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)
        featured, _, _ = load_from_issue_file(db, "2026-06-01")
        ids = [it["id"] for it in featured]
        # papers (section_rank=0) before news (section_rank=1)
        assert ids.index("p-hi") < ids.index("n1")
        assert ids.index("p-lo") < ids.index("n1")
        # p-hi (score=9) before p-lo (score=7) within papers
        assert ids.index("p-hi") < ids.index("p-lo")

    def test_metadata_section_counts(self, db, tmp_path, monkeypatch):
        import replay_writer as rw_mod
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        _insert_item(db, "p1", section="papers")
        _insert_item(db, "p2", section="papers")
        _insert_item(db, "n1", section="news")
        _write_issue(issues_dir, "2026-06-01", ["p1", "p2", "n1"])
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)
        _, _, metadata = load_from_issue_file(db, "2026-06-01")
        assert metadata["items_featured_papers"] == 2
        assert metadata["items_featured_news"] == 1
        assert metadata["items_featured_blogs"] == 0
        assert metadata["items_featured_total"] == 3

    def test_malformed_tags_json_falls_back_to_empty(self, db, tmp_path, monkeypatch):
        """json.JSONDecodeError for tags falls back to [] (lines 88-89)."""
        import replay_writer as rw_mod
        import sqlite3

        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()

        # Insert item with malformed tags JSON directly
        db.execute("""
            INSERT OR REPLACE INTO items (
                id, source, url, canonical_url, title, author, published_at,
                fetched_at, raw_text, score, tags, why, status, section,
                first_seen_date, last_seen_date, appearances
            ) VALUES (
                'mt1', 'arxiv:cs.AI', 'https://arxiv.org/abs/mt1',
                'https://arxiv.org/abs/mt1', 'Malformed Tags Paper', 'Author',
                '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z',
                'Abstract.', 8, 'NOT VALID JSON', 'good', 'published', 'papers',
                '2026-06-01', '2026-06-01', 1
            )
        """)
        db.commit()

        _write_issue(issues_dir, "2026-06-01", ["mt1"])
        monkeypatch.setattr(rw_mod, "ISSUES_DIR", issues_dir)

        featured, _, _ = load_from_issue_file(db, "2026-06-01")
        assert len(featured) == 1
        assert featured[0]["tags"] == []
