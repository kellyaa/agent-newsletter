"""Keep .env.template in sync with the environment variables the code reads.

Operators copy .env.template to .env and edit it.  A variable the scripts read
but the template omits is undiscoverable, and a documented default that drifts
from the code default misleads the operator.  Neither fails any other test.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / ".env.template"
SCRIPTS = REPO_ROOT / "scripts"

# Variables set by run.sh / run_attempt.py for internal plumbing, not by the
# operator in .env, so they intentionally do not appear in the template.
INTERNAL_VARS = {
    "CONTENT_ROOT",
    "LLM_USAGE_LOG",
    "RUN_ATTEMPT",
    "RUN_DATE",
    "RUN_STARTED_AT",
}

_ENV_READ = re.compile(r"""os\.environ\.get\(\s*["']([A-Z][A-Z0-9_]*)["']""")
# `NAME=value` or commented-out `# NAME=value` (the template's two forms).
_ASSIGN = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=(.*)$")


def _vars_read_by_code() -> set[str]:
    found: set[str] = set()
    for path in SCRIPTS.glob("*.py"):
        found.update(_ENV_READ.findall(path.read_text()))
    return found


def _template_assignments() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in TEMPLATE.read_text().splitlines():
        m = _ASSIGN.match(line)
        if m:
            out.setdefault(m.group(1), m.group(2).strip())
    return out


def test_every_operator_variable_is_documented_in_template():
    missing = _vars_read_by_code() - INTERNAL_VARS - set(_template_assignments())
    assert not missing, f".env.template does not document: {sorted(missing)}"


def test_template_documents_no_unused_variables():
    stale = set(_template_assignments()) - _vars_read_by_code()
    assert not stale, f".env.template lists variables no script reads: {sorted(stale)}"


def test_internal_vars_list_has_no_stale_entries():
    assert INTERNAL_VARS <= _vars_read_by_code()


def test_template_documented_numeric_defaults_match_code():
    source = (SCRIPTS / "rank.py").read_text() + (SCRIPTS / "write.py").read_text()
    documented = _template_assignments()
    for name in (
        "RANKER_TIMEOUT_S", "WRITER_TIMEOUT_S",
        "RANKER_MAX_TOKENS", "WRITER_MAX_TOKENS",
    ):
        m = re.search(
            rf"""os\.environ\.get\(\s*["']{name}["']\s*,\s*["'](\d+)["']""", source
        )
        assert m, f"{name} default not found in scripts"
        assert documented[name] == m.group(1), name


def test_template_model_and_endpoint_defaults_are_usable():
    documented = _template_assignments()
    assert documented["LLM_BASE_URL"].startswith("https://")
    assert not documented["LLM_BASE_URL"].endswith("/")
    assert documented["RANKER_MODEL"] and documented["WRITER_MODEL"]
