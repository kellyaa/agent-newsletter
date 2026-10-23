"""Keep the closed tag vocabulary consistent across code, prompt and spec.

rank.VALID_TAGS is the filter applied to the ranker's output (rank.persist drops
any tag not in the set).  prompts/rank.md tells the LLM which tags it may emit
and SPEC.md documents them.  If these drift apart, tags the prompt advertises are
silently discarded (or the docs lie), with no error anywhere.
"""
import re
from pathlib import Path

import prompts
from rank import VALID_TAGS

REPO_ROOT = Path(__file__).resolve().parents[1]


def _backticked_tags(text: str, anchor: str) -> set[str]:
    """Backticked tokens on the first line containing *anchor* (and the next
    non-empty line if the anchor line has none)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if anchor in line:
            found = re.findall(r"`([a-z][a-z-]*)`", line)
            j = i + 1
            while not found and j < len(lines):
                found = re.findall(r"`([a-z][a-z-]*)`", lines[j])
                j += 1
            return set(found)
    raise AssertionError(f"anchor {anchor!r} not found")


def test_valid_tags_has_thirteen_kebab_case_entries():
    assert len(VALID_TAGS) == 13
    assert all(re.fullmatch(r"[a-z]+(-[a-z]+)*", t) for t in VALID_TAGS)


def test_rank_prompt_lists_exactly_valid_tags():
    prompt = prompts.render("rank")
    assert _backticked_tags(prompt, "Closed-vocabulary tags") == VALID_TAGS


def test_spec_lists_exactly_valid_tags():
    spec = (REPO_ROOT / "SPEC.md").read_text()
    assert _backticked_tags(spec, "**Tags** from a closed vocabulary") == VALID_TAGS


def test_writer_prompt_example_tags_are_valid():
    prompt = prompts.render("write")
    example = re.findall(r'"tags":\s*\[([^\]]*)\]', prompt)
    assert example, "write.md no longer shows a tags example"
    for group in example:
        for tag in re.findall(r'"([^"]+)"', group):
            assert tag in VALID_TAGS, f"write.md example uses unknown tag {tag!r}"
