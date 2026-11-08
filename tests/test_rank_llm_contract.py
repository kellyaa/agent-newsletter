"""Ranker <-> LLM contract details that were not pinned.

Mutation probing of rank.py, llm.py and run_attempt.py showed that these
edits changed no test result: the ranker output-schema limits (score 0-10,
at most 5 tags, ``why`` up to 300 chars), the 2-space indent and literal
non-ASCII in the item JSON sent to the model, the 2-space indent of the
ranked.json and ranker-output debug files, how many unknown ids the warning
lists, the "unset" wording of the max_tokens log line, and the 30 s timeout on
run_attempt's git calls.
"""
from __future__ import annotations

import json
import logging
import subprocess
from unittest.mock import patch

import pytest

import edition
import llm
import rank
import run_attempt


# --- RANKER_OUTPUT_SCHEMA limits ---------------------------------------------

def _entry_props():
    return rank.RANKER_OUTPUT_SCHEMA["properties"]["rankings"]["items"]["properties"]


def test_schema_score_range_matches_what_sanitize_accepts():
    score = _entry_props()["score"]
    assert (score["type"], score["minimum"], score["maximum"]) == ("integer", 0, 10)
    kept = rank.sanitize_rankings(
        [{"id": "lo", "score": score["minimum"]}, {"id": "hi", "score": score["maximum"]},
         {"id": "under", "score": score["minimum"] - 1},
         {"id": "over", "score": score["maximum"] + 1}],
        {"lo", "hi", "under", "over"}, "papers",
    )
    assert [e["id"] for e in kept] == ["lo", "hi"]


def test_schema_caps_tags_at_five_and_why_at_300_chars():
    props = _entry_props()
    assert props["tags"]["maxItems"] == 5
    assert props["why"]["minLength"] == 1
    assert props["why"]["maxLength"] == 300


# --- prompt and debug-file formatting ----------------------------------------

def test_build_prompt_embeds_items_with_two_space_indent_and_literal_unicode():
    prompt = rank.build_prompt("news", [{"id": "i1", "title": "Café ✓"}], "RUBRIC")
    assert '```json\n[\n  {\n    "id": "i1",\n    "title": "Café ✓"\n  }\n]\n```' in prompt
    assert "\\u00e9" not in prompt


def test_ranked_json_is_two_space_indented_with_literal_unicode(tmp_path, monkeypatch):
    monkeypatch.setattr(rank, "invoke_ranker", lambda prompt, label: [
        {"id": "a", "score": 9, "tags": [], "why": "café"}])
    grouped = {"papers": [{"id": "a", "title": "t"}], "papers_prescored": [],
               "news": [], "blogs": []}
    out = tmp_path / "ranked.json"
    rank.score_candidates(grouped, "rubric", ranked_path=out)
    text = out.read_text()
    assert text == json.dumps(
        {"papers": [{"id": "a", "score": 9, "tags": [], "why": "café"}], "news": [], "blogs": []},
        indent=2, ensure_ascii=False)
    assert '\n  "papers": [\n    {\n      "id": "a"' in text
    assert "café" in text


def test_ranker_debug_file_is_two_space_indented_json():
    bad = {"rankings": 1}
    with patch("rank.call_llm", return_value=bad):
        with pytest.raises(RuntimeError, match="output at"):
            rank.invoke_ranker("prompt", label="dbg")
    f = edition.LOGS_ROOT / "agents" / "ranker-output-dbg.json"
    assert f.read_text() == json.dumps(bad, indent=2)
    assert f.read_text().startswith('{\n  "rankings"')


# --- unknown-id warning ------------------------------------------------------

def test_unknown_id_warning_counts_all_but_lists_only_the_first_three(caplog):
    items = [{"id": "real", "title": "t"}]
    raw = [{"id": f"ghost{n}", "score": 5, "tags": [], "why": "x"} for n in range(5)]
    with patch("rank.invoke_ranker", return_value=raw):
        with caplog.at_level(logging.WARNING, logger="rank"):
            rank.rank_section("news", items, "rubric")
    (msg,) = [r.getMessage() for r in caplog.records if "not sent" in r.getMessage()]
    assert "returned 5 ids" in msg
    assert "['ghost0', 'ghost1', 'ghost2']" in msg
    assert "ghost3" not in msg


# --- llm.call_llm max_tokens log line ----------------------------------------

@pytest.mark.parametrize("max_tokens, shown", [(None, "max_tokens=unset"), (512, "max_tokens=512")])
def test_call_llm_logs_max_tokens_or_unset(monkeypatch, caplog, max_tokens, shown):
    monkeypatch.setenv("LLM_API_KEY", "k")
    with patch("llm._one_shot", return_value={"ok": 1}):
        with caplog.at_level(logging.INFO, logger="llm"):
            llm.call_llm("p", {"type": "object"}, "s", model="m", max_tokens=max_tokens)
    assert any("invoking llm" in r.getMessage() and shown in r.getMessage()
               for r in caplog.records)


# --- run_attempt.code_state git timeout --------------------------------------

def test_code_state_bounds_each_git_call_to_thirty_seconds(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(run_attempt.subprocess, "run", fake_run)
    assert run_attempt.code_state() == ("abc123", 1)
    assert len(calls) == 2
    assert all(kw["timeout"] == 30 and kw["check"] is True for _cmd, kw in calls)
    assert all(cmd[:3] == ["git", "-C", str(run_attempt.REPO_ROOT)] for cmd, _kw in calls)


def test_code_state_survives_a_git_timeout(monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(run_attempt.subprocess, "run", boom)
    assert run_attempt.code_state() == (None, None)
