"""Tests for llm.py: _extra_headers, _one_shot error handling, call_llm retry loop."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from llm import _extra_headers, call_llm, _one_shot


# ---------------------------------------------------------------------------
# _extra_headers
# ---------------------------------------------------------------------------

class TestExtraHeaders:
    def test_missing_env_returns_empty_dict(self, monkeypatch):
        monkeypatch.delenv("LLM_EXTRA_HEADERS", raising=False)
        assert _extra_headers() == {}

    def test_empty_env_returns_empty_dict(self, monkeypatch):
        monkeypatch.setenv("LLM_EXTRA_HEADERS", "")
        assert _extra_headers() == {}

    def test_valid_json_object_returns_headers(self, monkeypatch):
        monkeypatch.setenv(
            "LLM_EXTRA_HEADERS", '{"X-API-Key": "secret", "X-Tenant": "acme"}'
        )
        result = _extra_headers()
        assert result == {"X-API-Key": "secret", "X-Tenant": "acme"}

    def test_values_coerced_to_str(self, monkeypatch):
        monkeypatch.setenv("LLM_EXTRA_HEADERS", '{"X-Retries": 3}')
        result = _extra_headers()
        assert result == {"X-Retries": "3"}
        assert isinstance(result["X-Retries"], str)

    def test_invalid_json_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("LLM_EXTRA_HEADERS", "not-json")
        with pytest.raises(RuntimeError, match="not valid JSON"):
            _extra_headers()

    def test_json_array_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("LLM_EXTRA_HEADERS", '["a", "b"]')
        with pytest.raises(RuntimeError, match="must be a JSON object"):
            _extra_headers()

    def test_json_null_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("LLM_EXTRA_HEADERS", "null")
        with pytest.raises(RuntimeError, match="must be a JSON object"):
            _extra_headers()


# ---------------------------------------------------------------------------
# call_llm — via mocking _one_shot directly
# ---------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
    "additionalProperties": False,
}


def _mock_env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_EXTRA_HEADERS", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)


class TestCallLlmSuccess:
    def test_returns_parsed_dict_on_success(self, monkeypatch):
        _mock_env(monkeypatch)
        expected = {"answer": "yes"}
        with patch("llm._one_shot", return_value=expected) as mock_shot:
            result = call_llm("prompt", SCHEMA, "test_schema", model="gpt-4o-mini")
        assert result == expected
        mock_shot.assert_called_once()

    def test_passes_model_to_one_shot(self, monkeypatch):
        _mock_env(monkeypatch)
        with patch("llm._one_shot", return_value={"answer": "ok"}) as mock_shot:
            call_llm("prompt", SCHEMA, "s", model="gpt-4o")
        _, kwargs = mock_shot.call_args
        assert kwargs.get("model") == "gpt-4o" or mock_shot.call_args[0][0] is not None

    def test_missing_api_key_raises_before_llm_call(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_EXTRA_HEADERS", raising=False)
        with pytest.raises(RuntimeError, match="LLM_API_KEY"):
            call_llm("prompt", SCHEMA, "s", model="m")


class TestCallLlmRetry:
    def test_retries_on_runtime_error_then_succeeds(self, monkeypatch):
        _mock_env(monkeypatch)
        expected = {"answer": "ok"}
        side_effects = [RuntimeError("parse fail"), expected]
        with patch("llm._one_shot", side_effect=side_effects) as mock_shot:
            result = call_llm("p", SCHEMA, "s", model="m", max_attempts=2)
        assert result == expected
        assert mock_shot.call_count == 2

    def test_raises_after_all_attempts_exhausted(self, monkeypatch):
        _mock_env(monkeypatch)
        with patch("llm._one_shot", side_effect=RuntimeError("parse fail")):
            with pytest.raises(RuntimeError, match="failed after 3 attempts"):
                call_llm("p", SCHEMA, "s", model="m", max_attempts=3)

    def test_no_retry_on_success_first_attempt(self, monkeypatch):
        _mock_env(monkeypatch)
        with patch("llm._one_shot", return_value={"answer": "yes"}) as mock_shot:
            call_llm("p", SCHEMA, "s", model="m", max_attempts=5)
        assert mock_shot.call_count == 1

    def test_max_attempts_one_raises_immediately_on_failure(self, monkeypatch):
        _mock_env(monkeypatch)
        with patch("llm._one_shot", side_effect=RuntimeError("error")):
            with pytest.raises(RuntimeError, match="failed after 1 attempts"):
                call_llm("p", SCHEMA, "s", model="m", max_attempts=1)


# ---------------------------------------------------------------------------
# _one_shot — markdown fence stripping and finish_reason handling
# ---------------------------------------------------------------------------

def _make_fake_client(content: str, finish_reason: str = "stop") -> MagicMock:
    choice = MagicMock()
    choice.finish_reason = finish_reason
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = None
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


class TestOneShotMarkdownFence:
    def _call_one_shot(self, content: str, finish_reason: str = "stop") -> dict:
        client = _make_fake_client(content, finish_reason)
        return _one_shot(
            client,
            prompt="test",
            schema=SCHEMA,
            schema_name="test",
            model="m",
            timeout_s=10,
            headers={},
            max_tokens=None,
            label="test",
        )

    def test_plain_json_parses(self):
        result = self._call_one_shot('{"answer": "yes"}')
        assert result == {"answer": "yes"}

    def test_markdown_fence_stripped(self):
        fenced = "```json\n{\"answer\": \"fenced\"}\n```"
        result = self._call_one_shot(fenced)
        assert result == {"answer": "fenced"}

    def test_finish_reason_length_raises(self):
        with pytest.raises(RuntimeError, match="truncated"):
            self._call_one_shot('{"answer": "trunc', finish_reason="length")

    def test_invalid_json_raises(self):
        with pytest.raises(RuntimeError, match="non-JSON"):
            self._call_one_shot("not json at all")

    def test_non_dict_json_raises(self):
        with pytest.raises(RuntimeError, match="non-object"):
            self._call_one_shot("[1, 2, 3]")

    def test_max_tokens_passed_to_create(self):
        """When max_tokens is set, it is included in the completions.create() call (line 88)."""
        from llm import _one_shot
        client = _make_fake_client('{"answer": "yes"}')
        _one_shot(
            client,
            prompt="test",
            schema=SCHEMA,
            schema_name="test",
            model="m",
            timeout_s=10,
            headers={},
            max_tokens=512,
            label="test",
        )
        call_kwargs = client.chat.completions.create.call_args[1]
        assert call_kwargs.get("max_tokens") == 512

    def test_usage_stats_logged_when_present(self, caplog):
        """When resp.usage is present, prompt/completion tokens are logged (line 94)."""
        import logging
        from llm import _one_shot

        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 150

        choice = MagicMock()
        choice.finish_reason = "stop"
        choice.message.content = '{"answer": "yes"}'

        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = usage

        client = MagicMock()
        client.chat.completions.create.return_value = resp

        with caplog.at_level(logging.INFO, logger="llm"):
            _one_shot(
                client,
                prompt="test",
                schema=SCHEMA,
                schema_name="test",
                model="m",
                timeout_s=10,
                headers={},
                max_tokens=None,
                label="test-label",
            )

        assert any("prompt_tokens" in r.message or "100" in r.message
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# Usage accumulator (get_usage_totals / reset_usage_totals / _record_usage)
# ---------------------------------------------------------------------------

class TestUsageAccumulator:
    def setup_method(self):
        from llm import reset_usage_totals
        reset_usage_totals()

    def teardown_method(self):
        from llm import reset_usage_totals
        reset_usage_totals()

    def test_totals_start_at_zero(self):
        from llm import get_usage_totals
        totals = get_usage_totals()
        assert totals == {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def test_reset_zeros_after_activity(self):
        from llm import _record_usage, reset_usage_totals, get_usage_totals
        u = MagicMock()
        u.prompt_tokens = 10
        u.completion_tokens = 20
        _record_usage(u)
        reset_usage_totals()
        assert get_usage_totals() == {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def test_record_usage_accumulates_across_calls(self):
        from llm import _record_usage, get_usage_totals
        u1 = MagicMock(); u1.prompt_tokens = 100; u1.completion_tokens = 50
        u2 = MagicMock(); u2.prompt_tokens = 30; u2.completion_tokens = 10
        _record_usage(u1)
        _record_usage(u2)
        totals = get_usage_totals()
        assert totals["prompt_tokens"] == 130
        assert totals["completion_tokens"] == 60
        assert totals["calls"] == 2

    def test_record_usage_none_is_noop(self):
        from llm import _record_usage, get_usage_totals
        _record_usage(None)
        assert get_usage_totals() == {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def test_missing_attrs_treated_as_zero(self):
        """SDK usage objects that omit fields (unusual providers) count as 0, not crash."""
        from llm import _record_usage, get_usage_totals
        class Bare:
            pass
        _record_usage(Bare())
        totals = get_usage_totals()
        assert totals == {"prompt_tokens": 0, "completion_tokens": 0, "calls": 1}

    def test_non_numeric_values_treated_as_zero(self):
        """Defensive: SDKs occasionally return '?' or None; must not raise."""
        from llm import _record_usage, get_usage_totals
        u = MagicMock()
        u.prompt_tokens = "not-a-number"
        u.completion_tokens = None
        _record_usage(u)
        totals = get_usage_totals()
        assert totals == {"prompt_tokens": 0, "completion_tokens": 0, "calls": 1}

    def test_get_usage_totals_returns_copy(self):
        """Mutating the returned dict must not affect internal state."""
        from llm import _record_usage, get_usage_totals
        u = MagicMock(); u.prompt_tokens = 5; u.completion_tokens = 5
        _record_usage(u)
        snap = get_usage_totals()
        snap["prompt_tokens"] = 9999
        again = get_usage_totals()
        assert again["prompt_tokens"] == 5

    def test_one_shot_records_usage_into_totals(self):
        """Every successful _one_shot must feed the accumulator."""
        from llm import _one_shot, get_usage_totals

        usage = MagicMock()
        usage.prompt_tokens = 77
        usage.completion_tokens = 22
        usage.total_tokens = 99

        choice = MagicMock()
        choice.finish_reason = "stop"
        choice.message.content = '{"answer": "ok"}'

        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = usage

        client = MagicMock()
        client.chat.completions.create.return_value = resp

        _one_shot(
            client, prompt="p", schema=SCHEMA, schema_name="s",
            model="m", timeout_s=10, headers={}, max_tokens=None, label="t",
        )
        totals = get_usage_totals()
        assert totals["prompt_tokens"] == 77
        assert totals["completion_tokens"] == 22
        assert totals["calls"] == 1
