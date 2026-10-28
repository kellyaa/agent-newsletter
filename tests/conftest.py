import os
from pathlib import Path
import sys

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# The pipeline has no default edition (see scripts/edition.py): every stage
# module reads its editorial config from the edition named by $EDITION at
# import time. Tests run against the real `agents` edition.
os.environ.setdefault("EDITION", "agents")

REPO_ROOT = Path(__file__).resolve().parents[1]


def base_template(stage: str) -> str:
    """The real prompts/<stage>.md base template (with its {{slots}}).

    Tests that run a stage's main() against a temp prompt file use this so the
    template uses every fragment the edition provides.
    """
    return (REPO_ROOT / "prompts" / f"{stage}.md").read_text()


@pytest.fixture(autouse=True)
def _no_inherited_run_date(monkeypatch):
    """Stages key the day on $RUN_DATE (db.run_date). Don't let a value from the
    developer's shell leak in; tests that need a run date set it themselves."""
    monkeypatch.delenv("RUN_DATE", raising=False)


@pytest.fixture(autouse=True)
def _isolated_edition_logs(tmp_path, monkeypatch):
    """Rendered prompts are debug output under logs/<slug>/; keep tests out of the repo's logs/."""
    import edition

    monkeypatch.setattr(edition, "LOGS_ROOT", tmp_path / "edition-logs")
