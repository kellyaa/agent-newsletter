"""llm.call_llm / _one_shot wiring that the retry and log paths depend on.

Existing tests mock ``_one_shot`` for the retry loop and check ``extra_body``
in isolation, so nothing proved that ``call_llm`` asks for a cache bypass on
retries only (and honours LLM_RETRY_NO_CACHE), that the request omits
``extra_headers`` when none are configured, or that the 2KB cap on logged raw
content holds. A cached bad reply is the failure these guard against: the
retry would get the identical reply back (2026-10-08).
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

import llm
from llm import DEFAULT_TIMEOUT_S, _one_shot, call_llm

SCHEMA = {
    "type": "object",
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_EXTRA_HEADERS", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_RETRY_NO_CACHE", raising=False)
    monkeypatch.delenv("LLM_RESPONSE_FORMAT", raising=False)


def _client(content: str, finish_reason: str = "stop") -> MagicMock:
    choice = MagicMock()
    choice.finish_reason = finish_reason
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = None
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


def _shot(client, **kw):
    args = dict(prompt="p", schema=SCHEMA, schema_name="s", model="m",
                timeout_s=10, headers={}, max_tokens=None, label="lbl")
    args.update(kw)
    return _one_shot(client, **args)


def _bypass_flags(side_effect):
    with patch("llm._one_shot", side_effect=side_effect) as shot:
        try:
            call_llm("p", SCHEMA, "s", model="m", max_attempts=len(side_effect))
        except RuntimeError:
            pass
    return [c.kwargs["bypass_cache"] for c in shot.call_args_list]


# ---- call_llm: cache bypass on retries ------------------------------------

def test_first_attempt_uses_the_cache_and_the_retry_bypasses_it():
    flags = _bypass_flags([RuntimeError("bad"), {"answer": "ok"}])
    assert flags == [False, True]


def test_every_retry_bypasses_the_cache(monkeypatch):
    flags = _bypass_flags([RuntimeError("a"), RuntimeError("b"), RuntimeError("c")])
    assert flags == [False, True, True]


@pytest.mark.parametrize("value", ["0", " 0 "])
def test_retry_no_cache_zero_disables_the_bypass(monkeypatch, value):
    monkeypatch.setenv("LLM_RETRY_NO_CACHE", value)
    assert _bypass_flags([RuntimeError("bad"), {"answer": "ok"}]) == [False, False]


@pytest.mark.parametrize("value", ["1", "", "true"])
def test_retry_no_cache_anything_but_zero_keeps_the_bypass(monkeypatch, value):
    monkeypatch.setenv("LLM_RETRY_NO_CACHE", value)
    assert _bypass_flags([RuntimeError("bad"), {"answer": "ok"}]) == [False, True]


def test_single_attempt_call_is_not_labelled_with_an_attempt_number():
    with patch("llm._one_shot", return_value={"answer": "ok"}) as shot:
        call_llm("p", SCHEMA, "s", model="m", max_attempts=1, label="papers")
    assert shot.call_args.kwargs["label"] == "papers"


def test_multi_attempt_calls_label_each_attempt():
    with patch("llm._one_shot", side_effect=[RuntimeError("x"), {"answer": "ok"}]) as shot:
        call_llm("p", SCHEMA, "s", model="m", max_attempts=2, label="papers")
    assert [c.kwargs["label"] for c in shot.call_args_list] == [
        "papers (attempt 1)", "papers (attempt 2)"]


def test_default_is_two_attempts_and_the_documented_timeout():
    assert DEFAULT_TIMEOUT_S == 1200
    with patch("llm._one_shot", side_effect=RuntimeError("bad")) as shot:
        with pytest.raises(RuntimeError, match="failed after 2 attempts"):
            call_llm("p", SCHEMA, "s", model="m")
    assert shot.call_count == 2
    assert shot.call_args.kwargs["timeout_s"] == DEFAULT_TIMEOUT_S


def test_retry_warning_reports_attempt_numbers(caplog):
    with patch("llm._one_shot", side_effect=[RuntimeError("x"), {"answer": "ok"}]):
        with caplog.at_level(logging.WARNING, logger="llm"):
            call_llm("p", SCHEMA, "s", model="m", max_attempts=2, label="papers")
    retries = [r.message for r in caplog.records if "retry" in r.message]
    assert len(retries) == 1 and "retry 2/2" in retries[0]


def test_start_log_lists_header_names_not_values(monkeypatch, caplog):
    monkeypatch.setenv("LLM_EXTRA_HEADERS", '{"X-Tenant": "acme", "X-API-Key": "hunter2"}')
    with patch("llm._one_shot", return_value={"answer": "ok"}):
        with caplog.at_level(logging.INFO, logger="llm"):
            call_llm("p", SCHEMA, "s", model="m")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "['X-API-Key', 'X-Tenant']" in text
    assert "hunter2" not in text and "acme" not in text


def test_start_log_says_none_without_headers(caplog):
    with patch("llm._one_shot", return_value={"answer": "ok"}):
        with caplog.at_level(logging.INFO, logger="llm"):
            call_llm("p", SCHEMA, "s", model="m")
    assert "extra_headers=none" in " ".join(r.getMessage() for r in caplog.records)


# ---- _one_shot: request shape ---------------------------------------------

def test_empty_headers_are_sent_as_none():
    client = _client('{"answer": "x"}')
    _shot(client, headers={})
    assert client.chat.completions.create.call_args.kwargs["extra_headers"] is None


def test_configured_headers_are_forwarded():
    client = _client('{"answer": "x"}')
    _shot(client, headers={"X-Tenant": "acme"})
    assert client.chat.completions.create.call_args.kwargs["extra_headers"] == {"X-Tenant": "acme"}


# ---- _one_shot: logged raw content is capped at 2KB -----------------------

def test_truncated_response_logs_only_the_first_2000_chars(caplog):
    content = "a" * 2005 + "TAILMARK"
    with caplog.at_level(logging.ERROR, logger="llm"):
        with pytest.raises(RuntimeError, match="truncated"):
            _shot(_client(content, finish_reason="length"))
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "a" * 2000 in logged
    assert "a" * 2001 not in logged
    assert "TAILMARK" not in logged


def test_non_json_response_logs_only_the_first_2000_chars(caplog):
    content = "b" * 2005 + "TAILMARK"
    with caplog.at_level(logging.ERROR, logger="llm"):
        with pytest.raises(RuntimeError, match="non-JSON"):
            _shot(_client(content))
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "b" * 2000 in logged
    assert "b" * 2001 not in logged
    assert "TAILMARK" not in logged
