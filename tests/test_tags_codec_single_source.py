"""The ``items.tags`` column format has one codec: ``models.encode_tags`` / ``decode_tags``.

rank writes the column; candidates (prescored papers) and write (the writer
input, hence the issue frontmatter) read it back. Each used to spell
``json.loads(...) if ... else []`` inline with different ``except`` clauses, and
neither checked the decoded shape: a JSON string such as ``"agents"`` reached
the frontmatter as six one-letter tags (so the site built ``/tags/a/`` and so on),
and a non-string value crashed write while candidates swallowed it.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import write
from models import decode_tags, encode_tags

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.parametrize("raw", [
    None, "", "{not json", '"agents"', "5", "null", '{"a": 1}', 5, b"", ["x"],
])
def test_decode_tags_degrades_to_empty_list(raw):
    assert decode_tags(raw) == []


def test_decode_tags_keeps_only_string_elements():
    assert decode_tags('[1, "ok", null, ["x"], "two"]') == ["ok", "two"]


def test_encode_decode_round_trip():
    tags = ["agents", "évals", "rag"]
    raw = encode_tags(tags)
    assert json.loads(raw) == tags
    assert decode_tags(raw) == tags
    assert decode_tags(encode_tags([])) == []


def test_encode_tags_drops_non_strings():
    assert decode_tags(encode_tags(["a", 1, None, "b"])) == ["a", "b"]


def _row(tags):
    return {
        "id": "a", "section": "papers", "source": "s", "url": "https://x/a",
        "title": "T", "author": "Au", "published_at": "2026-06-01",
        "raw_text": "body", "score": 7, "tags": tags, "why": "w",
    }


@pytest.mark.parametrize("raw", ['"agents"', 5])
def test_writer_input_never_splits_or_crashes_on_bad_tags(raw):
    assert write.featured_item_from_row(_row(raw))["tags"] == []


def _calls_json_on_tags(tree: ast.AST) -> list[int]:
    """Lines where json.loads/json.dumps is called on something named *tags*."""
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("loads", "dumps")
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "json"):
            continue
        for arg in node.args[:1]:
            names = [n.id for n in ast.walk(arg) if isinstance(n, ast.Name)]
            consts = [n.value for n in ast.walk(arg)
                      if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            if any("tags" in s for s in names + consts):
                hits.append(node.lineno)
    return hits


@pytest.mark.parametrize("path", sorted(SCRIPTS.glob("*.py")), ids=lambda p: p.name)
def test_no_script_encodes_or_decodes_tags_inline(path):
    if path.name == "models.py":
        return
    hits = _calls_json_on_tags(ast.parse(path.read_text()))
    assert not hits, (
        f"{path.name} lines {hits}: use models.encode_tags / decode_tags for items.tags"
    )


def test_guard_detects_inline_codec():
    tree = ast.parse('json.loads(r["tags"])\njson.dumps(clean_tags)\njson.loads(other)\n')
    assert _calls_json_on_tags(tree) == [1, 2]


@pytest.mark.parametrize("module, name", [
    ("rank", "encode_tags"), ("write", "decode_tags"), ("candidates", "decode_tags"),
])
def test_stage_uses_the_codec(module, name):
    tree = ast.parse((SCRIPTS / f"{module}.py").read_text())
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert name in used
