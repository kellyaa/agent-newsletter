"""rank resolves the edition and its debug-artifact path at call time, not at import (#446).

rank.py used to bind ``EDITION = edition.current()`` plus ``SECTION_RULES``,
``VALID_TAGS``, ``KEYWORD_RE`` and ``RANKED_PATH`` (from the frozen
``db.CONTENT_ROOT``) when it was imported. ``import rank`` therefore failed
without ``EDITION`` set, and a caller that switched editions after import
kept ranking with the old edition's thresholds, tag vocabulary and keyword
gate unless it reloaded ``db`` and then ``rank`` in that order.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import edition
import rank
from models import Status

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
LAZY = ("EDITION", "RANKED_PATH", "SECTION_RULES", "VALID_TAGS", "KEYWORD_RE")


def test_rank_imports_without_an_edition_selected():
    env = {k: v for k, v in os.environ.items() if k != "EDITION"}
    proc = subprocess.run(
        [sys.executable, "-c", "import rank"],
        cwd=SCRIPTS, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_rank_binds_no_edition_or_content_root_at_module_level():
    tree = ast.parse((SCRIPTS / "rank.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "db":
            names = {a.name for a in node.names}
            assert "CONTENT_ROOT" not in names, (
                f"rank.py:{node.lineno} imports db.CONTENT_ROOT (frozen at import); "
                "use db.content_root()"
            )
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Lambda):
                    break  # _LAZY_ALIASES values run on attribute access, not import
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                        and sub.value.id == "edition":
                    raise AssertionError(
                        f"rank.py:{node.lineno} reads edition.{sub.attr} at import time; "
                        "resolve it inside a function (see rank.ranked_path)"
                    )
            targets = node.targets if isinstance(node, ast.Assign) else \
                [node.target] if isinstance(node, ast.AnnAssign) else []
            for t in targets:
                assert not (isinstance(t, ast.Name) and t.id in LAZY), (
                    f"rank.py:{node.lineno} binds {t.id} at import; "
                    "it is a lazy alias (see rank._LAZY_ALIASES)"
                )


def test_rank_internals_do_not_read_deprecated_aliases():
    """Functions call edition.current()/ranked_path(), not the lazy aliases."""
    tree = ast.parse((SCRIPTS / "rank.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in LAZY and isinstance(node.ctx, ast.Load):
            raise AssertionError(
                f"rank.py:{node.lineno} reads {node.id}; use edition.current() / ranked_path()"
            )


def test_backfill_does_not_read_rank_edition_alias():
    src = (SCRIPTS / "backfill.py").read_text()
    for name in LAZY:
        assert f"rank_mod.{name}" not in src, f"backfill.py reads deprecated rank.{name}"


def test_ranked_path_follows_env_without_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    expected = edition.runtime_paths(tmp_path, "agents").ranked
    assert rank.ranked_path() == expected
    assert rank.RANKED_PATH == expected  # deprecated alias stays in step
    monkeypatch.delenv("EDITION")
    with pytest.raises(edition.EditionError):  # no default edition
        rank.ranked_path()


def test_aliases_follow_edition_switch_without_reload(monkeypatch):
    for slug in ("agents", "engineering-with-ai"):
        monkeypatch.setenv("EDITION", slug)
        ed = edition.current()
        assert rank.EDITION is ed
        assert rank.SECTION_RULES == ed.section_rules()
        assert rank.VALID_TAGS == ed.valid_tags()
        assert rank.KEYWORD_RE.pattern == ed.keyword_re().pattern


def test_unknown_module_attribute_still_raises():
    with pytest.raises(AttributeError):
        rank.NO_SUCH_NAME  # noqa: B018


def _first_differing_tag() -> tuple[str, str]:
    a = edition._load_cached("agents").valid_tags()
    b = edition._load_cached("engineering-with-ai").valid_tags()
    return sorted(a - b)[0], sorted(b - a)[0]


def test_persist_filters_tags_with_the_current_edition(monkeypatch):
    """persist() keeps tags from the edition selected now, not at import."""
    only_agents, only_eng = _first_differing_tag()
    rows: list = []
    monkeypatch.setattr(rank, "apply_rank_decisions", lambda conn, r: rows.extend(r))
    decision = {"x": {"score": 8, "tags": [only_agents, only_eng], "why": "w",
                      "status": Status.FEATURED}}
    monkeypatch.setenv("EDITION", "agents")
    rank.persist(None, decision)
    monkeypatch.setenv("EDITION", "engineering-with-ai")
    rank.persist(None, decision)
    assert rows[0][2] == f'["{only_agents}"]'
    assert rows[1][2] == f'["{only_eng}"]'


def test_effective_cap_uses_the_current_edition_rules(monkeypatch):
    """The papers burst cap exists in agents but not engineering-with-ai."""
    monkeypatch.setenv("EDITION", "agents")
    rules = edition.current().section_rules()["papers"]
    burst_day = [{"score": rules["burst_trigger_score"]}] * rules["burst_trigger_count"]
    assert rank.effective_cap("papers", burst_day) == rules["burst_cap"]
    monkeypatch.setenv("EDITION", "engineering-with-ai")
    assert rank.effective_cap("papers", burst_day) == edition.current().section_rules()["papers"]["cap"]
