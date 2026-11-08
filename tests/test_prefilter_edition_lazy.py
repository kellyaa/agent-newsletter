"""prefilter resolves the edition and its debug-artifact path at call time, not at import (#446).

prefilter.py used to bind ``_RUNTIME``/``CANDIDATES_OUT`` (from the frozen
``db.CONTENT_ROOT``) plus ``EDITION = edition.current()``, ``KEYWORDS`` and
``KEYWORD_RE`` when it was imported. ``import prefilter`` therefore failed
without ``EDITION`` set, and a caller that switched editions after import kept
gating with the old edition's keywords and rubric and wrote candidates.json
into the old edition's logs unless it reloaded ``db`` and then ``prefilter``.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import edition
import prefilter

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
LAZY = ("EDITION", "CANDIDATES_OUT", "KEYWORDS", "KEYWORD_RE", "_RUNTIME")


def test_prefilter_imports_without_an_edition_selected():
    env = {k: v for k, v in os.environ.items() if k != "EDITION"}
    proc = subprocess.run(
        [sys.executable, "-c", "import prefilter"],
        cwd=SCRIPTS, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_prefilter_binds_no_edition_or_content_root_at_module_level():
    tree = ast.parse((SCRIPTS / "prefilter.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "db":
            names = {a.name for a in node.names}
            assert "CONTENT_ROOT" not in names, (
                f"prefilter.py:{node.lineno} imports db.CONTENT_ROOT (frozen at import); "
                "use db.content_root()"
            )
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Lambda):
                    break  # _LAZY_ALIASES values run on attribute access, not import
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                        and sub.value.id == "edition":
                    raise AssertionError(
                        f"prefilter.py:{node.lineno} reads edition.{sub.attr} at import "
                        "time; resolve it inside a function (see prefilter.candidates_path)"
                    )
            targets = node.targets if isinstance(node, ast.Assign) else \
                [node.target] if isinstance(node, ast.AnnAssign) else []
            for t in targets:
                assert not (isinstance(t, ast.Name) and t.id in LAZY), (
                    f"prefilter.py:{node.lineno} binds {t.id} at import; "
                    "it is a lazy alias (see prefilter._LAZY_ALIASES)"
                )


def test_prefilter_internals_do_not_read_deprecated_aliases():
    """Functions call edition.current()/candidates_path(), not the lazy aliases."""
    tree = ast.parse((SCRIPTS / "prefilter.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in LAZY and isinstance(node.ctx, ast.Load):
            raise AssertionError(
                f"prefilter.py:{node.lineno} reads {node.id}; "
                "use edition.current() / candidates_path()"
            )


def test_scripts_do_not_read_prefilter_edition_alias():
    for path in SCRIPTS.glob("*.py"):
        if path.name == "prefilter.py":
            continue
        src = path.read_text()
        for name in LAZY:
            for prefix in ("prefilter.", "prefilter_mod.", "pf_mod."):
                assert f"{prefix}{name}" not in src, (
                    f"{path.name} reads deprecated prefilter.{name}"
                )


def test_candidates_path_follows_env_without_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    expected = edition.runtime_paths(tmp_path, "agents").candidates
    assert prefilter.candidates_path() == expected
    assert prefilter.CANDIDATES_OUT == expected  # deprecated alias stays in step
    monkeypatch.delenv("EDITION")
    with pytest.raises(edition.EditionError):  # no default edition
        prefilter.candidates_path()


def test_aliases_follow_edition_switch_without_reload(monkeypatch):
    for slug in ("agents", "engineering-with-ai"):
        monkeypatch.setenv("EDITION", slug)
        ed = edition.current()
        assert prefilter.EDITION is ed
        assert prefilter.KEYWORDS == ed.keyword_regexes()
        assert prefilter.KEYWORD_RE.pattern == ed.keyword_re().pattern


def test_unknown_module_attribute_still_raises():
    with pytest.raises(AttributeError):
        prefilter.NO_SUCH_NAME  # noqa: B018


def test_keyword_gate_uses_the_current_edition(monkeypatch):
    """_passes_keyword_gate() follows the edition selected now, not at import."""
    agents = edition._load_cached("agents").keyword_re()
    eng = edition._load_cached("engineering-with-ai").keyword_re()
    assert agents.pattern != eng.pattern
    seen: list = []
    monkeypatch.setattr(prefilter, "keyword_gate", lambda item, rx: seen.append(rx) or True)
    for slug in ("agents", "engineering-with-ai"):
        monkeypatch.setenv("EDITION", slug)
        prefilter._passes_keyword_gate({"title": "t", "raw_text": ""})
    assert [rx.pattern for rx in seen] == [agents.pattern, eng.pattern]


def test_rubric_hash_uses_the_current_edition(monkeypatch):
    """The rendered rank prompt (and so its hash) follows the edition selected now."""
    rendered: list = []
    monkeypatch.setattr(
        prefilter.prompts, "render",
        lambda name, ed, template_path=None: rendered.append(ed.slug) or ed.slug,
    )
    for slug in ("agents", "engineering-with-ai"):
        monkeypatch.setenv("EDITION", slug)
        prefilter._rubric_hash()
    assert rendered == ["agents", "engineering-with-ai"]
