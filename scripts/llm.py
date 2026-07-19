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

Structured output defaults to `response_format={"type": "json_schema", strict:
true}`, which OpenAI and most compatible servers (vLLM, llama.cpp, LM Studio)
honor. Set LLM_RESPONSE_FORMAT=json_object (or none) for providers that reject
json_schema — e.g. LiteLLM-fronted Bedrock Claude, which 400s on the translated
output_config.format. We json.loads the message content regardless of mode; if
that fails, the caller sees a JSONDecodeError with the raw text logged.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

log = logging.getLogger("llm")

DEFAULT_TIMEOUT_S = 1200

# Module-level usage accumulator. Every successful call_llm() adds its
# usage row here so callers (rank.py, write.py) can flush the totals to a
# sidecar file at the end of a stage. publish.py then aggregates the
# sidecars into a single runs-table row.
_USAGE_LOG: list[dict[str, Any]] = []


def record_usage(*, label: str, model: str, prompt_tokens: int,
                 completion_tokens: int, total_tokens: int) -> None:
    """Append a usage row. Callers usually don't call this directly — the
    normal call_llm() path records automatically. Exposed for tests and for
    non-call_llm paths that still want to be counted."""
    _USAGE_LOG.append({
        "label": label,
        "model": model,
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "total_tokens": int(total_tokens),
    })


def get_usage_log() -> list[dict[str, Any]]:
    """Return a shallow copy of the accumulated usage entries."""
    return list(_USAGE_LOG)


def reset_usage_log() -> None:
    """Clear the accumulator. Primarily used by tests."""
    _USAGE_LOG.clear()


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


def _one_shot(
    client: OpenAI,
    *,
    prompt: str,
    schema: dict[str, Any],
    schema_name: str,
    model: str,
    timeout_s: int,
    headers: dict[str, str],
    max_tokens: int | None,
    label: str,
) -> dict[str, Any]:
    """Single attempt: call the API, parse, validate. Raises on any failure."""
    kwargs: dict[str, Any] = dict(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        timeout=timeout_s,
        extra_headers=headers or None,
    )

    # Structured-output mode is provider-dependent. LiteLLM-fronted Bedrock
    # Claude rejects json_schema (translated to output_config.format, a 400),
    # so LLM_RESPONSE_FORMAT lets the operator downgrade. json.loads runs on the
    # content regardless, so json_object/none still yield a dict.
    #   json_schema (default) — OpenAI/vLLM/llama.cpp strict structured output
    #   json_object           — ask for a JSON object without a schema
    #   none                  — omit response_format; rely on the prompt
    response_mode = os.environ.get("LLM_RESPONSE_FORMAT", "json_schema").strip().lower()
    if response_mode == "json_schema":
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": schema,
                "strict": True,
            },
        }
    elif response_mode == "json_object":
        kwargs["response_format"] = {"type": "json_object"}
    elif response_mode == "none":
        pass
    else:
        raise RuntimeError(
            f"LLM_RESPONSE_FORMAT must be one of json_schema|json_object|none, "
            f"got {response_mode!r}"
        )
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    resp = client.chat.completions.create(**kwargs)

    usage = getattr(resp, "usage", None)
    if usage is not None:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        log.info(
            "%s: prompt_tokens=%s completion_tokens=%s total=%s",
            label,
            prompt_tokens if prompt_tokens is not None else "?",
            completion_tokens if completion_tokens is not None else "?",
            total_tokens if total_tokens is not None else "?",
        )
        # Feed the module-level accumulator. Callers flush this to a
        # sidecar file so publish.py can persist run totals (issue #13).
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            record_usage(
                label=label,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens if isinstance(total_tokens, int)
                              else prompt_tokens + completion_tokens,
            )

    finish = getattr(resp.choices[0], "finish_reason", None)
    content = resp.choices[0].message.content or ""
    txt = content.strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[1] if "\n" in txt else txt
        if txt.endswith("```"):
            txt = txt.rsplit("```", 1)[0]

    if finish == "length":
        # Truncated mid-response — almost always a degenerate repetition loop
        # hitting max_tokens. Don't bother trying to parse; surface a clear
        # error so the retry path takes over.
        log.error("%s: response truncated (finish_reason=length); first 2KB: %s",
                  label, content[:2000])
        raise RuntimeError(f"llm response truncated at max_tokens for {label}")

    try:
        parsed = json.loads(txt)
    except json.JSONDecodeError as e:
        log.error("%s: could not parse llm content as JSON: %s", label, e)
        log.error("%s: raw content (first 2KB): %s", label, content[:2000])
        raise RuntimeError(f"llm returned non-JSON content for {label}") from e

    if not isinstance(parsed, dict):
        raise RuntimeError(f"llm returned non-object for {label}: {type(parsed).__name__}")
    return parsed


def call_llm(
    prompt: str,
    schema: dict[str, Any],
    schema_name: str,
    *,
    model: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    label: str = "llm",
    max_tokens: int | None = None,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Send a single-turn prompt and return a dict matching the schema.

    `max_tokens` caps the response length per attempt. When the model goes
    into a degenerate repetition loop, this fails fast at the cap instead of
    burning the model's full output budget.

    `max_attempts` controls how many times we'll retry on parse / truncation
    failures. The SDK already retries network and 5xx errors itself; this
    handles the case where the API returned 200 OK but the content was
    unparseable (truncation, repetition loop). Default is 2 (one retry).

    Raises RuntimeError if every attempt fails.
    """
    client = _client()
    headers = _extra_headers()
    log.info(
        "invoking llm (%s, model=%s, extra_headers=%s, max_tokens=%s, max_attempts=%d)",
        label, model, sorted(headers.keys()) or "none",
        max_tokens if max_tokens is not None else "unset",
        max_attempts,
    )

    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            log.warning("%s: retry %d/%d after parse/truncation failure",
                        label, attempt, max_attempts)
        try:
            return _one_shot(
                client,
                prompt=prompt,
                schema=schema,
                schema_name=schema_name,
                model=model,
                timeout_s=timeout_s,
                headers=headers,
                max_tokens=max_tokens,
                label=f"{label} (attempt {attempt})" if max_attempts > 1 else label,
            )
        except RuntimeError as e:
            last_err = e
            continue

    # All attempts exhausted.
    raise RuntimeError(
        f"llm failed after {max_attempts} attempts for {label}: {last_err}"
    ) from last_err
