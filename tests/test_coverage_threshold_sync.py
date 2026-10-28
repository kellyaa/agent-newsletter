"""Keep the documented coverage gate in sync with the enforced one.

``pyproject.toml`` enforces the gate via ``--cov-fail-under``; the same number
is mirrored in ``.coverage-thresholds.json`` (read by tooling) and quoted in
AGENTS.md / CLAUDE.md. The JSON file says "raise both together, never lower",
but nothing checked it, so the copies could silently drift apart.
"""
import json
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _enforced_gate() -> int:
    cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    addopts = cfg["tool"]["pytest"]["ini_options"]["addopts"]
    m = re.search(r"--cov-fail-under=(\d+)", addopts)
    assert m, "pyproject.toml addopts has no --cov-fail-under"
    return int(m.group(1))


def test_thresholds_file_mirrors_pyproject_gate():
    data = json.loads((REPO_ROOT / ".coverage-thresholds.json").read_text())
    assert data["total"] == _enforced_gate()


def test_agent_docs_quote_the_enforced_gate():
    gate = _enforced_gate()
    for name in ("AGENTS.md", "CLAUDE.md"):
        text = (REPO_ROOT / name).read_text()
        m = re.search(r"coverage gate (?:is )?(\d+)%", text)
        assert m, f"{name} does not quote the coverage gate"
        assert int(m.group(1)) == gate, name


def test_ci_does_not_hardcode_its_own_gate():
    # tests.yml must defer to pyproject.toml; a second number would drift.
    workflow = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text()
    uncommented = "\n".join(
        line for line in workflow.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--cov-fail-under" not in uncommented
