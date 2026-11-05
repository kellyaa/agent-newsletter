"""docs/agent-tasks/ documents task traceability (ACMM aef:task-traceability).

Its relative links must resolve so the page does not rot silently.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "agent-tasks" / "README.md"


def test_agent_tasks_doc_exists():
    assert DOC.read_text().startswith("# Agent task traceability")


def test_agent_tasks_doc_links_resolve():
    links = re.findall(r"\]\((\.\./[^)#]+)\)", DOC.read_text())
    assert links
    broken = [l for l in links if not (DOC.parent / l).exists()]
    assert not broken, f"broken links: {broken}"
