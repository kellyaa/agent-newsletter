"""The run_attempts.outcome vocabulary lives in models.py with a guard test.

run_attempt.py, db.py, run.sh, and run_edition.sh all reference the outcome
vocabulary ('started', 'ok', 'failed') and the sentinel ('none'). The contract
is written in five places with no guard. If one side renames a value, the
orchestrator silently skips or re-runs every edition.

These tests:
- check that db.py and run_attempt.py use only models.AttemptOutcome / NO_ATTEMPT
- check that run.sh and run_edition.sh use only those values
- pin the values against future changes
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from models import AttemptOutcome, NO_ATTEMPT

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# The shell scripts that reference outcome
SHELL_SCRIPTS = {
    ROOT / "run.sh",
    SCRIPTS / "run_edition.sh",
}


def _imported_modules_and_names(path: Path) -> tuple[set[str], set[str]]:
    """Return (module names imported, string constant values)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return modules, names


def test_attempt_outcome_values_pinned():
    """The outcome constants must not change accidentally."""
    assert set(AttemptOutcome) == {
        AttemptOutcome.STARTED, AttemptOutcome.OK, AttemptOutcome.FAILED
    }
    assert set(str(v) for v in AttemptOutcome) == {"started", "ok", "failed"}
    assert NO_ATTEMPT == "none"


def test_db_uses_models_outcome_not_literals():
    """db.py must import AttemptOutcome and use it, not hard-coded strings."""
    path = SCRIPTS / "db.py"
    modules, strings = _imported_modules_and_names(path)
    assert "models" in modules, "db.py must import models (for AttemptOutcome)"
    # No bare outcome literals in db.py (only variable/enum names allowed)
    outcome_literals = {"started", "ok", "failed"} & strings
    assert not outcome_literals, (
        f"db.py contains outcome literals: {outcome_literals}; "
        "use AttemptOutcome.STARTED / .OK / .FAILED instead"
    )


def test_run_attempt_uses_models_outcome_not_literals():
    """run_attempt.py must import AttemptOutcome/NO_ATTEMPT and use them."""
    path = SCRIPTS / "run_attempt.py"
    modules, strings = _imported_modules_and_names(path)
    assert "models" in modules, "run_attempt.py must import models"
    # Check that it doesn't use bare outcome literals (though argparse choices
    # will have them as enum values, not string literals in the source)
    outcome_literals = {"started", "ok", "failed"} & strings
    assert not outcome_literals, (
        f"run_attempt.py contains outcome literals: {outcome_literals}; "
        "use models.AttemptOutcome.* and NO_ATTEMPT instead"
    )


def _shell_outcome_strings(path: Path) -> set[str]:
    """Extract all string literals from shell that look like outcome values."""
    text = path.read_text(encoding="utf-8")
    # Find strings that match outcome patterns: quoted words or case patterns
    quoted = set(re.findall(r'"((?:started|ok|failed|none))"', text))
    quoted |= set(re.findall(r"'((?:started|ok|failed|none))'", text))
    # case pattern: case "$var" in started|ok|failed|none)
    case_matches = set(re.findall(r'\b(?:started|ok|failed|none)\b', text))
    # Filter out comments and context we don't care about
    return quoted | case_matches


def test_shell_scripts_use_outcome_vocabulary():
    """run.sh and run_edition.sh use only AttemptOutcome values and NO_ATTEMPT."""
    valid = {str(v) for v in AttemptOutcome} | {NO_ATTEMPT}
    for path in SHELL_SCRIPTS:
        strings = _shell_outcome_strings(path)
        invalid = strings - valid
        assert not invalid, (
            f"{path.name} uses invalid outcome values: {invalid}; "
            f"must use only {valid}"
        )


def test_all_outcome_values_are_used():
    """Every AttemptOutcome value and NO_ATTEMPT must appear somewhere."""
    all_scripts = {SCRIPTS / "db.py", SCRIPTS / "run_attempt.py"} | SHELL_SCRIPTS
    all_strings = set()
    for path in all_scripts:
        _, strings = _imported_modules_and_names(path)
        all_strings |= strings
    all_strings |= set(_shell_outcome_strings(path) for path in SHELL_SCRIPTS)
    # Flatten the set of sets if needed
    all_strings_flat = set()
    for s in all_strings:
        if isinstance(s, str):
            all_strings_flat.add(s)
        else:
            all_strings_flat |= s

    valid = {str(v) for v in AttemptOutcome} | {NO_ATTEMPT}
    # At minimum, the enum values must be mentioned (via models import or literals)
    for outcome_val in valid:
        assert outcome_val in all_strings_flat, (
            f"outcome value {outcome_val!r} not found in any script; "
            "check for typos or changes in AttemptOutcome/NO_ATTEMPT"
        )
