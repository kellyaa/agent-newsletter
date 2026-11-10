"""The issue-file frontmatter contract is defined once, on the write side.

The frontmatter is what ``write`` hands the Astro site and what
``replay_writer`` reads back as the record of what shipped. The item keys were
already pinned to the site's zod schema (#536), but the top-level keys and the
``metadata`` block were plain dict literals, and replay_writer re-implemented
the ``---`` fence. Renaming a metadata key passed pytest and only failed the
Astro build at deploy time. These tests pin the remaining pieces:

* ``models.IssueMetadata`` is what ``write.writer_metadata`` returns, and its
  keys are exactly the zod ``metadata`` keys, apart from the site-only
  optional ones the pipeline does not write.
* ``write.FRONTMATTER_KEYS`` is exactly what ``assemble_issue`` emits and
  exactly the zod top-level ``schema`` keys.
* the appendix sections in the zod schema are ``models.Section``.
* ``write.emit_yaml_frontmatter`` / ``write.parse_frontmatter`` round-trip,
  ``replay_writer`` parses through the latter, and no other script spells
  the fence.
"""
from __future__ import annotations

import ast
import re
import typing
from pathlib import Path

import pytest

import replay_writer
import write
from models import AppendixItem, FeaturedItem, IssueMetadata, Section

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
CONTENT_CONFIG = REPO / "site" / "src" / "content.config.ts"

# Declared optional (.nullable().optional()) by the site. Nothing writes them
# yet: runs.cost_usd is unpopulated (#441 closed) and the duration is recorded
# in state.db by publish, after write has emitted the file.
SITE_ONLY_METADATA = {"cost_usd", "duration_seconds"}


def _zod_block(anchor: str, indent: int) -> str:
    src = CONTENT_CONFIG.read_text()
    pad = " " * indent
    m = re.search(rf"{re.escape(anchor)}\{{\n(.*?)\n{pad}\}}\)", src, re.S)
    assert m, f"{anchor!r} not found in {CONTENT_CONFIG}"
    return m.group(1)


def _zod_keys(anchor: str, indent: int) -> set[str]:
    key_pad = " " * (indent + 2)
    return set(re.findall(rf"^{key_pad}(\w+):", _zod_block(anchor, indent), re.M))


def _featured(i: int, section: str = "papers") -> FeaturedItem:
    return FeaturedItem(
        id=f"id-{i}", section=section, source="arxiv:cs.AI",
        url=f"https://arxiv.org/abs/2401.{i:05d}", title=f"Paper {i}",
        author=None, published_at=None, raw_text="x", score=7,
        tags=["agents"], why="w",
    )


def _issue() -> str:
    featured = [_featured(0), _featured(1, "news")]
    appendix = {s.value: [] for s in Section}
    appendix["blogs"].append(AppendixItem(
        id="a", section="blogs", source="rss:x", url="https://a.com/a", title="A",
    ))
    metadata = write.writer_metadata(featured, appendix, 12)
    out = {"theme": "t", "items": [{"id": it["id"], "summary": "s"} for it in featured]}
    return write.assemble_issue("2026-07-09", featured, appendix, metadata, out)


# --- metadata block -------------------------------------------------------------

def test_writer_metadata_is_annotated_with_issue_metadata():
    assert typing.get_type_hints(write.writer_metadata)["return"] is IssueMetadata


def test_writer_metadata_emits_exactly_the_declared_keys():
    md = write.writer_metadata([_featured(0)], {s.value: [] for s in Section}, None)
    assert set(md) == set(IssueMetadata.__annotations__)


def test_declared_metadata_matches_site_schema():
    site = _zod_keys("metadata: z.object(", 4)
    assert set(IssueMetadata.__annotations__) == site - SITE_ONLY_METADATA
    assert SITE_ONLY_METADATA <= site, "stale SITE_ONLY_METADATA entry"


def test_metadata_has_a_featured_count_per_section():
    per_section = {k for k in IssueMetadata.__annotations__ if k.startswith("items_featured_")}
    assert per_section == {"items_featured_total"} | {f"items_featured_{s}" for s in Section}


# --- top-level keys and sections ------------------------------------------------

def test_assemble_issue_emits_exactly_frontmatter_keys():
    fm = write.parse_frontmatter(_issue())
    assert tuple(fm) == write.FRONTMATTER_KEYS
    assert set(fm["metadata"]) == set(IssueMetadata.__annotations__)


def test_frontmatter_keys_match_site_schema():
    assert set(write.FRONTMATTER_KEYS) == _zod_keys("schema: z.object(", 2)


def test_site_appendix_sections_are_models_sections():
    assert _zod_keys("appendix: z.object(", 4) == {s.value for s in Section}
    m = re.search(r"const SECTIONS = \[(.*?)\]", CONTENT_CONFIG.read_text())
    assert m and re.findall(r'"(\w+)"', m.group(1)) == [s.value for s in Section]


# --- framing -------------------------------------------------------------------

def test_emit_and_parse_round_trip():
    data = {"date": "2026-07-09", "theme": 'a "q" \\ b\n---\nc', "featured": []}
    assert write.parse_frontmatter(write.emit_yaml_frontmatter(data) + "\nbody\n") == data


@pytest.mark.parametrize("text,msg", [
    ("no fence\n", "opening"),
    ("---\ndate: x\n", "closing"),
])
def test_parse_frontmatter_rejects_unframed_text(text, msg):
    with pytest.raises(ValueError, match=f"missing {msg} frontmatter fence"):
        write.parse_frontmatter(text)


def test_replay_writer_reads_what_write_wrote(tmp_path):
    p = tmp_path / "2026-07-09.md"
    p.write_text(_issue())
    assert replay_writer.parse_issue_frontmatter(p) == write.parse_frontmatter(p.read_text())


def test_replay_writer_error_names_the_file(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("nope\n")
    with pytest.raises(ValueError, match=r"bad\.md: missing opening"):
        replay_writer.parse_issue_frontmatter(p)


# Prompt builders separate the input JSON from the rubric with a markdown
# rule (a ``---`` line between blank lines); that is prompt text, not issue
# frontmatter.
_MARKDOWN_RULE = "\n\n---\n\n"
_FENCE_RE = re.compile(r"(^|\n)---(\n|$)")


def _fence_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and _FENCE_RE.search(n.value.replace(_MARKDOWN_RULE, ""))
    ]


@pytest.mark.parametrize("path", sorted(SCRIPTS.glob("*.py")), ids=lambda p: p.name)
def test_no_other_script_spells_the_fence(path):
    found = _fence_literals(path)
    if path.name == "write.py":
        assert found == [write.FRONTMATTER_FENCE]
    else:
        assert found == [], f"{path.name} spells the frontmatter fence; use write.parse_frontmatter"


def test_fence_detector_sees_the_old_replay_parser(tmp_path):
    p = tmp_path / "x.py"
    p.write_text('t.startswith("---\\n")\nt.find("\\n---\\n", 4)\nf"x\\n\\n---\\n\\n{y}"\n')
    assert _fence_literals(p) == ["---\n", "\n---\n"]
