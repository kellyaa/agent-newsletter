"""The row -> writer-input projection has one home: scripts/write.py.

replay_writer.py used to carry its own copy of write.load_today_items'
row mapping (tag JSON decode, RAW_TEXT_MAX truncation, FeaturedItem fields)
and of the metadata counts, so a change to what production sends the writer
would silently not reach the replay tool it exists to reproduce. These tests
pin write.featured_item_from_row / write.writer_metadata as the shared helpers
and fail if a script re-implements them.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import write
from models import Section

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _row(**over):
    base = {
        "id": "a", "section": "papers", "source": "s", "url": "https://x/a",
        "title": "T", "author": "Au", "published_at": "2026-06-01",
        "raw_text": "body", "score": 7, "tags": json.dumps(["x"]), "why": "w",
    }
    base.update(over)
    return base


def test_featured_item_from_row_maps_all_fields():
    it = write.featured_item_from_row(_row())
    assert it == {
        "id": "a", "section": "papers", "source": "s", "url": "https://x/a",
        "title": "T", "author": "Au", "published_at": "2026-06-01",
        "raw_text": "body", "score": 7, "tags": ["x"], "why": "w",
    }


def test_featured_item_from_row_bad_or_empty_tags_and_truncation():
    assert write.featured_item_from_row(_row(tags="{not json"))["tags"] == []
    assert write.featured_item_from_row(_row(tags=None))["tags"] == []
    assert write.featured_item_from_row(_row(raw_text=None))["raw_text"] == ""
    long = "z" * (write.RAW_TEXT_MAX + 10)
    raw = write.featured_item_from_row(_row(raw_text=long))["raw_text"]
    assert raw == "z" * write.RAW_TEXT_MAX + "\n...[truncated]"


def test_writer_metadata_counts():
    featured = [write.featured_item_from_row(_row(id=i, section=s))
                for i, s in [("1", "papers"), ("2", "papers"), ("3", "news")]]
    appendix = {s.value: [] for s in Section}
    appendix["blogs"] = [{"id": "b", "section": "blogs", "source": "s",
                          "url": "https://x/b", "title": "B"}]
    assert write.writer_metadata(featured, appendix, None) == {
        "items_considered": None,
        "items_featured_total": 3,
        "items_featured_papers": 2,
        "items_featured_news": 1,
        "items_featured_blogs": 0,
        "items_appendix": 1,
    }


def _offenders(predicate):
    hits = []
    for path in sorted(SCRIPTS.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if predicate(path, node):
                hits.append(f"{path.name}:{node.lineno}")
    return hits


def _func_lines(name: str) -> range:
    """Line span of write.<name> (where the logic is allowed to live)."""
    tree = ast.parse((SCRIPTS / "write.py").read_text())
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == name:
            return range(fn.lineno, fn.end_lineno + 1)
    raise AssertionError(f"write.{name} not found")


def _is_name(node, name):
    return (isinstance(node, ast.Name) and node.id == name) or (
        isinstance(node, ast.Attribute) and node.attr == name
    )


def test_only_featured_item_from_row_truncates_raw_text():
    """RAW_TEXT_MAX is used only inside write.featured_item_from_row."""
    allowed = _func_lines("featured_item_from_row")

    def pred(path, node):
        if not (isinstance(node, (ast.Name, ast.Attribute)) and _is_name(node, "RAW_TEXT_MAX")):
            return False
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            return False  # the definition itself
        return not (path.name == "write.py" and node.lineno in allowed)

    assert _offenders(pred) == [], "truncate raw_text via write.featured_item_from_row"


def test_only_writer_metadata_builds_featured_counts():
    """The items_featured_* metadata keys are built only by write.writer_metadata."""
    allowed = _func_lines("writer_metadata")

    def pred(path, node):
        if not isinstance(node, ast.Dict):
            return False
        keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
        if "items_featured_total" not in keys:
            return False
        return not (path.name == "write.py" and node.lineno in allowed)

    assert _offenders(pred) == [], "build writer metadata via write.writer_metadata"


def test_replay_writer_uses_shared_helpers():
    src = (SCRIPTS / "replay_writer.py").read_text()
    assert "featured_item_from_row(" in src
    assert "writer_metadata(" in src
