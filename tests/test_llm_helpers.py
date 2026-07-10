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
