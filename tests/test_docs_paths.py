"""Keep README.md / CONTRIBUTING.md in sync with the files that exist.

The README's "What's here" tree is the map new contributors rely on.  Nothing
else fails when a script is added without being listed, or when a documented
path is renamed or deleted, so the docs rot silently.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = [REPO_ROOT / "README.md", REPO_ROOT / "CONTRIBUTING.md"]

# Directories whose every file must be mentioned (by basename) in the README.
LISTED_DIRS = ["scripts", "prompts", "launchd"]
LISTED_TOP_LEVEL = ["run.sh", "watchdog.sh", "editions/agents/sources.yaml"]

_PATH = re.compile(
    r"(?<![\w./-])((?:scripts|prompts|launchd|tests|docs|site)/[A-Za-z0-9_./\[\]-]+"
    r"\.(?:py|sh|md|yaml|plist|ts|astro|mjs|js))"
)
# Placeholders / files that live on another branch or are generated.
_SKIP_MARKERS = ("YYYY", "<", "*")


def _listed_files() -> list[Path]:
    files = [REPO_ROOT / name for name in LISTED_TOP_LEVEL]
    for d in LISTED_DIRS:
        files.extend(
            p for p in sorted((REPO_ROOT / d).iterdir())
            if p.is_file() and not p.name.startswith(".") and p.suffix != ".pyc"
        )
    return files


def test_listed_dirs_exist_and_nonempty():
    assert len(_listed_files()) > len(LISTED_TOP_LEVEL)


def test_every_script_prompt_and_plist_is_mentioned_in_readme():
    readme = (REPO_ROOT / "README.md").read_text()
    missing = [
        str(p.relative_to(REPO_ROOT))
        for p in _listed_files()
        if p.name not in readme
    ]
    assert not missing, f"README.md does not mention: {missing}"


def test_paths_referenced_in_docs_exist():
    broken: list[str] = []
    for doc in DOCS:
        for ref in sorted(set(_PATH.findall(doc.read_text()))):
            if any(m in ref for m in _SKIP_MARKERS):
                continue
            if not (REPO_ROOT / ref).exists():
                broken.append(f"{doc.name}: {ref}")
    assert not broken, f"documented paths that do not exist: {broken}"


def test_path_regex_ignores_placeholders_and_plain_prose():
    text = "see site/src/content/issues/YYYY-MM-DD.md and scripts/fetch.py."
    found = _PATH.findall(text)
    assert "scripts/fetch.py" in found
    assert any("YYYY" in f for f in found)  # matched, then skipped by marker
