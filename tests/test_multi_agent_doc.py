"""docs/multi-agent.md documents agent orchestration (ACMM acmm:multi-agent-orchestration).

Its relative links must resolve so the page does not rot silently.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "multi-agent.md"


def test_multi_agent_doc_exists():
    assert DOC.read_text().startswith("# Multi-agent orchestration")


def test_multi_agent_doc_links_resolve():
    links = re.findall(r"\]\((\.\.?/[^)#]+|[a-z][^):]*\.md)\)", DOC.read_text())
    assert links
    broken = [l for l in links if not (DOC.parent / l).exists()]
    assert not broken, f"broken links: {broken}"
