"""The pipeline's log line format and root-logger setup live only in scripts/logsetup.py.

scripts/run_edition.sh appends every stage to one per-day log, so the stages
must share one line format: a copy that drifts (level, or a dropped
``%(name)s``) makes one stage's lines stop matching the rest of the file.
And configuring the root logger is a process-level side effect. It belongs
to the script run as ``__main__``, not to importing it: backfill imports
rank/write, replay_writer imports write, check_edition imports fetch, and the
tests import all of them (#446/#624 removed the same kind of import-time
binding for EDITION, CONTENT_ROOT and the LLM role config).

This test fails if any script outside logsetup.py calls
``logging.basicConfig`` or spells the format, if a module-level statement
configures logging, or if a script that logs and has a ``__main__`` block
does not call ``logsetup.configure()`` there.
"""
from __future__ import annotations

import ast
import logging
import subprocess
import sys
from pathlib import Path

import logsetup

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
OWNER = "logsetup.py"
FORMAT_FIELDS = ("%(asctime)s", "%(levelname)s", "%(name)s", "%(message)s")


def _scripts() -> list[Path]:
    return sorted(p for p in SCRIPTS.glob("*.py") if p.name != OWNER)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _is_main_guard(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in node.test.comparators)
    )


def _calls(nodes, attr: str, owner: str) -> list[ast.Call]:
    found = []
    for top in nodes:
        for n in ast.walk(top):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == attr
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == owner
            ):
                found.append(n)
    return found


def _basicconfig_calls(tree: ast.AST) -> list[ast.Call]:
    return _calls([tree], "basicConfig", "logging")


def _format_literals(tree: ast.AST) -> list[str]:
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and sum(f in n.value for f in FORMAT_FIELDS) >= 3
    ]


def _imports_logging(tree: ast.Module) -> bool:
    return any(
        isinstance(n, ast.Import) and any(a.name == "logging" for a in n.names)
        for n in tree.body
    )


# ------------------------------------------------------------- the owner

def test_owner_defines_the_format_and_level():
    assert all(f in logsetup.LOG_FORMAT for f in FORMAT_FIELDS)
    assert logsetup.LOG_LEVEL == logging.INFO
    assert len(_basicconfig_calls(_tree(SCRIPTS / OWNER))) == 1


def test_configure_delegates_to_basicconfig(monkeypatch):
    seen = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: seen.append(kw))
    logsetup.configure()
    assert seen == [{"level": logging.INFO, "format": logsetup.LOG_FORMAT}]


def test_configure_applies_format_and_level_in_a_fresh_process():
    code = (
        "import logging, sys; sys.path.insert(0, sys.argv[1]); import logsetup; "
        "logsetup.configure(); r = logging.getLogger(); "
        "print(r.level, r.handlers[0].formatter._fmt)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(SCRIPTS)], capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == f"{logging.INFO} {logsetup.LOG_FORMAT}"


def test_configure_keeps_an_existing_root_configuration():
    code = (
        "import logging, sys; sys.path.insert(0, sys.argv[1]); import logsetup; "
        "logging.basicConfig(level=logging.WARNING, format='X %(message)s'); "
        "logsetup.configure(); r = logging.getLogger(); "
        "print(r.level, len(r.handlers), r.handlers[0].formatter._fmt)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(SCRIPTS)], capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == f"{logging.WARNING} 1 X %(message)s"


# ------------------------------------------------------------- the callers

def test_no_script_calls_basicconfig_or_spells_the_format():
    offenders = {}
    for p in _scripts():
        tree = _tree(p)
        hits = [f"basicConfig@{c.lineno}" for c in _basicconfig_calls(tree)] + _format_literals(tree)
        if hits:
            offenders[p.name] = hits
    assert not offenders, (
        f"logging configured outside scripts/{OWNER}: {offenders}; "
        "call logsetup.configure() under `if __name__ == \"__main__\":` instead"
    )


def test_no_script_configures_logging_at_import_time():
    offenders = {}
    for p in _scripts():
        module_level = [n for n in _tree(p).body if not _is_main_guard(n)
                        and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        hits = _calls(module_level, "configure", "logsetup") + _basicconfig_calls(ast.Module(module_level, []))
        if hits:
            offenders[p.name] = [h.lineno for h in hits]
    assert not offenders, f"logging configured at import time: {offenders}"


def test_every_logging_entry_script_configures_under_main():
    missing = []
    checked = []
    for p in _scripts():
        tree = _tree(p)
        guards = [n for n in tree.body if _is_main_guard(n)]
        if not guards or not _imports_logging(tree):
            continue
        checked.append(p.stem)
        calls = _calls(guards, "configure", "logsetup")
        main_calls = _calls(guards, "main", "sys") or [
            n for g in guards for n in ast.walk(g)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "main"
        ]
        if not calls or not main_calls or calls[0].lineno > main_calls[0].lineno:
            missing.append(p.name)
    # The entry points scripts/run_edition.sh and the operator tools run.
    assert {"fetch", "prefilter", "rank", "write", "publish", "run_attempt",
            "reset_day", "backfill", "replay_writer", "migrate_layout",
            "dead_sources"} <= set(checked)
    assert not missing, (
        f"entry scripts that log but do not call logsetup.configure() before main(): {missing}"
    )


def test_importing_a_stage_leaves_the_root_logger_unconfigured():
    code = (
        "import logging, sys; sys.path.insert(0, sys.argv[1]); "
        "import fetch, prefilter, rank, write, publish, run_attempt, reset_day, "
        "backfill, replay_writer, migrate_layout, dead_sources, check_edition; "
        "print(len(logging.getLogger().handlers))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(SCRIPTS)], capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "0"


# ------------------------------------------------------------- the detectors

def test_detectors_see_regressions(tmp_path):
    p = tmp_path / "x.py"
    p.write_text(
        "import logging, logsetup\n"
        "logging.basicConfig(format='%(asctime)s %(levelname)s %(name)s: %(message)s')\n"
        "logsetup.configure()\n"
        "if __name__ == '__main__':\n    logsetup.configure()\n"
    )
    tree = _tree(p)
    assert len(_basicconfig_calls(tree)) == 1
    assert _format_literals(tree)
    module_level = [n for n in tree.body if not _is_main_guard(n)]
    assert len(_calls(module_level, "configure", "logsetup")) == 1
    assert len(_calls([n for n in tree.body if _is_main_guard(n)], "configure", "logsetup")) == 1
