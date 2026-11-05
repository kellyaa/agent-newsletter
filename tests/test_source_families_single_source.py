"""sources.yaml families and their required keys are defined once, in models.

fetch.iter_collectors (walks the families), check_edition (lints them),
new_edition (scaffolds them) and tests/test_sources_config.py each used to
carry their own literal copy of ("rss", "arxiv", "hn", "reddit", "html",
"github_releases") and of the per-family required keys. Adding a family to
fetch without the linter would make check_edition reject a valid edition as
"unknown source families"; the reverse would let a family pass lint that fetch
silently never collects.
"""
from __future__ import annotations

import ast
from pathlib import Path

import check_edition
import new_edition
from models import SOURCE_REQUIRED_KEYS, SourceFamily

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FAMILY_VALUES = {f.value for f in SourceFamily}


def test_required_keys_cover_every_family():
    assert set(SOURCE_REQUIRED_KEYS) == set(SourceFamily)
    assert all(keys for keys in SOURCE_REQUIRED_KEYS.values())


def test_check_edition_families_come_from_models():
    assert check_edition.FAMILIES == tuple(f.value for f in SourceFamily)
    assert all(type(f) is str for f in check_edition.FAMILIES)
    assert check_edition.REQUIRED_KEYS == {
        f.value: set(k) for f, k in SOURCE_REQUIRED_KEYS.items()
    }


def test_new_edition_partitions_families():
    assert set(new_edition.LLM_FAMILIES) | set(new_edition.MANUAL_FAMILIES) == FAMILY_VALUES
    assert not set(new_edition.LLM_FAMILIES) & set(new_edition.MANUAL_FAMILIES)


def test_new_edition_schema_offers_exactly_the_llm_families():
    src = new_edition.DRAFT_SCHEMA["properties"]["sources"]
    assert set(src["properties"]) == set(new_edition.LLM_FAMILIES)
    assert src["required"] == list(new_edition.LLM_FAMILIES)


def test_rendered_sources_yaml_lists_every_family():
    import yaml

    text = new_edition.render_sources_yaml("X", {}, [])
    assert set(yaml.safe_load(text)) == FAMILY_VALUES


def test_fetch_walks_every_family(monkeypatch):
    import fetch

    seen: list[str] = []

    def fake(name):
        def _gen(src, *a, **kw):
            seen.append(name)
            return iter(())
        return _gen

    for fn in ("fetch_rss", "fetch_arxiv", "fetch_hn", "fetch_reddit", "fetch_html"):
        monkeypatch.setattr(fetch, fn, fake(fn.removeprefix("fetch_")))
    monkeypatch.setattr(fetch, "fetch_github_releases", fake("github_releases"))
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    sources = {f.value: [{"id": "x", "owner": "o", "repo": "r"}] for f in SourceFamily}
    for _label, gen in fetch.iter_collectors(sources, http_client=None):
        list(gen)
    assert seen == [f.value for f in SourceFamily]  # declaration order is fetch order


def _family_literals(path: Path) -> list[tuple[int, str]]:
    """String constants equal to a family name used as a dict key/subscript/get arg."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        cands: list[ast.AST] = []
        if isinstance(node, ast.Subscript):
            cands.append(node.slice)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "get" and node.args):
            cands.append(node.args[0])
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)) and len(node.elts) >= 2:
            vals = [e.value for e in node.elts if isinstance(e, ast.Constant)]
            if len(vals) == len(node.elts) and set(vals) <= FAMILY_VALUES:
                hits.append((node.lineno, repr(tuple(vals))))
        for c in cands:
            if isinstance(c, ast.Constant) and c.value in FAMILY_VALUES:
                hits.append((c.lineno, c.value))
    return hits


def test_fetch_and_check_edition_have_no_family_literals():
    for name in ("fetch.py", "check_edition.py"):
        hits = _family_literals(SCRIPTS / name)
        assert not hits, f"{name} hard-codes source families {hits}; use models.SourceFamily"


def test_new_edition_has_no_family_tuples():
    # new_edition still builds per-family entries by name; the family *lists*
    # (schema required, coercion, rendering) must come from LLM_FAMILIES.
    tuples = [h for h in _family_literals(SCRIPTS / "new_edition.py") if h[1].startswith("(")]
    assert not tuples, f"new_edition.py hard-codes family lists {tuples}; use LLM_FAMILIES"


def test_detector_flags_literals(tmp_path):
    p = tmp_path / "x.py"
    p.write_text('s.get("rss")\ns["hn"]\nF = ("rss", "arxiv")\nok = s.get("other")\n')
    assert sorted(_family_literals(p)) == [(1, "rss"), (2, "hn"), (3, "('rss', 'arxiv')")]
