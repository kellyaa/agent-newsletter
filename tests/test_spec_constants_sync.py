"""Keep numeric constants quoted in SPEC.md / README.md in sync with the code.

SPEC.md names several tunables literally (``PAPER_POOL_MAX_AGE_DAYS = 7``) and
the docs state the watchdog's default staleness window and re-fire throttle.
Changing the code without the docs (or vice versa) leaves operators with wrong
guidance and nothing fails, so pin the values here.
"""
import re
from pathlib import Path

import candidates
import prefilter

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = (REPO_ROOT / "SPEC.md").read_text()
README = (REPO_ROOT / "README.md").read_text()
WATCHDOG = (REPO_ROOT / "watchdog.sh").read_text()


def test_spec_pool_constants_match_code():
    for mod, name in (
        (prefilter, "PAPER_POOL_MAX_AGE_DAYS"),
        (prefilter, "PAPER_POOL_MAX_COMPETES"),
        (candidates, "PAPER_PRERANK_CAP"),
    ):
        m = re.search(rf"`{name} = (\d+)`", SPEC)
        assert m, f"SPEC.md does not document {name} = N"
        assert int(m.group(1)) == getattr(mod, name), name


def test_spec_appendix_retry_cap_matches_prefilter():
    assert "capped at 2 total appearances" in SPEC
    assert prefilter.APPENDIX_MAX_APPEARANCES == 2


def test_watchdog_default_stale_hours_documented():
    code = re.search(r'STALE_HOURS="\$\{STALE_HOURS:-(\d+)\}"', WATCHDOG)
    assert code, "watchdog.sh STALE_HOURS default not found"
    hours = code.group(1)
    assert re.search(rf"default `{hours}`", README)
    assert f">{hours}h stale" in SPEC


def test_watchdog_throttle_documented_as_code_value():
    code = re.search(r"-lt \$\(\(\s*(\d+) \* 3600\s*\)\)", WATCHDOG)
    assert code, "watchdog.sh throttle window not found"
    hours = code.group(1)
    for doc in (SPEC, README):
        assert re.search(rf"\b(?:every|for) {hours} hours", doc)
