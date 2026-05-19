"""Thin wrapper around an OpenAI-compatible chat-completions endpoint.

This is the replacement for the headless `claude -p` calls that previously
lived in rank.py and write.py. The contract is: give me a prompt and a JSON
Schema; get back a dict that conforms to the schema.

Provider is selected via env:
  LLM_BASE_URL        e.g. https://api.openai.com/v1, http://localhost:8000/v1
  LLM_API_KEY         bearer token (some local servers accept any non-empty value)
  LLM_EXTRA_HEADERS   optional JSON object of additional headers sent on every
                      request. Use for endpoints that require extra auth/routing
                      headers in addition to the bearer token. Example:
                        LLM_EXTRA_HEADERS='{"RITS_API_KEY": "xyz"}'
                        LLM_EXTRA_HEADERS='{"X-Tenant-Id": "abc"}'

Model id is passed in by the caller (WRITER_MODEL / RANKER_MODEL env vars in
the existing scripts), and is interpreted by the target endpoint — there's no
provider-side translation here.

Structured output uses `response_format={"type": "json_schema", strict: true}`,
which OpenAI and most compatible servers (vLLM, llama.cpp, LM Studio) honor.
For providers that ignore `strict`, we still json.loads the message content;
if that fails, the caller sees a JSONDecodeError with the raw text logged.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

log = logging.getLogger("llm")

DEFAULT_TIMEOUT_S = 1200


def _extra_headers() -> dict[str, str]:
    raw = os.environ.get("LLM_EXTRA_HEADERS")
    if not raw:
        return {}
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM_EXTRA_HEADERS is not valid JSON: {e}") from e
    if not isinstance(extra, dict):
        raise RuntimeError("LLM_EXTRA_HEADERS must be a JSON object")
    return {str(k): str(v) for k, v in extra.items()}


def _client() -> OpenAI:
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("LLM_API_KEY (or OPENAI_API_KEY) is not set")
    return OpenAI(base_url=base_url, api_key=api_key)


def call_llm(
    prompt: str,
    schema: dict[str, Any],
    schema_name: str,
    *,
    model: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    label: str = "llm",
) -> dict[str, Any]:
    """Send a single-turn prompt and return a dict matching the schema.

    Raises RuntimeError if the response cannot be parsed into a dict.
    """
    client = _client()
    headers = _extra_headers()
    log.info(
        "invoking llm (%s, model=%s, extra_headers=%s)",
        label, model, sorted(headers.keys()) or "none",
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": schema,
                "strict": True,
            },
        },
        timeout=timeout_s,
        extra_headers=headers or None,
    )

    usage = getattr(resp, "usage", None)
    if usage is not None:
        log.info(
            "%s: prompt_tokens=%s completion_tokens=%s total=%s",
            label,
            getattr(usage, "prompt_tokens", "?"),
            getattr(usage, "completion_tokens", "?"),
            getattr(usage, "total_tokens", "?"),
        )

    content = resp.choices[0].message.content or ""
    txt = content.strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[1] if "\n" in txt else txt
        if txt.endswith("```"):
            txt = txt.rsplit("```", 1)[0]
    try:
        parsed = json.loads(txt)
    except json.JSONDecodeError as e:
        log.error("%s: could not parse llm content as JSON: %s", label, e)
        log.error("%s: raw content (first 2KB): %s", label, content[:2000])
        raise RuntimeError(f"llm returned non-JSON content for {label}") from e

    if not isinstance(parsed, dict):
        raise RuntimeError(f"llm returned non-object for {label}: {type(parsed).__name__}")
    return parsed
