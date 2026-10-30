# LLM Integration and Prompting Guide

This guide explains how to integrate new LLM calls into the pipeline, debug LLM failures, and tune prompt behavior. All LLM communication goes through `scripts/llm.py`, a thin wrapper around OpenAI-compatible chat-completions endpoints.

## Quick Start: Adding an LLM Call

All LLM interactions use `scripts/llm.py`, which wraps the OpenAI SDK and handles retries, structured output validation, and logging. Never import `openai` directly in pipeline code.

```python
from scripts.llm import call_llm

# Basic usage
response = call_llm(
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is an agent?"},
    ],
    json_schema=None,  # None for plain text, or a Pydantic schema for structured output
)
# Returns: string (plain text) or dict (if json_schema provided)
```

## Architecture: How `llm.py` Works

### Design Principles

1. **Single point of entry** — all LLM calls use `scripts/llm.py`'s `call_llm()` function
2. **Structured output by default** — responses are validated against a JSON schema (`json_schema` parameter)
3. **Automatic retries on failure** — parse errors, truncations, and timeouts are retried with exponential backoff
4. **Transparent logging** — all requests/responses logged to `logs/<slug>/llm-<stage>.log` for debugging
5. **Environment-driven config** — models, endpoints, timeouts all via `.env` variables

### Function Signature

```python
def call_llm(
    messages: list[dict],
    json_schema: type | None = None,
    timeout_seconds: int | None = None,
    max_tokens: int | None = None,
) -> str | dict:
    """
    Make an OpenAI-compatible chat-completions call.
    
    Args:
        messages: List of {"role": "...", "content": "..."} dicts
        json_schema: Pydantic model or TypedDict for response validation (optional)
        timeout_seconds: Override default timeout (RANKER_TIMEOUT_S or WRITER_TIMEOUT_S)
        max_tokens: Override default max_tokens (RANKER_MAX_TOKENS or WRITER_MAX_TOKENS)
    
    Returns:
        str if json_schema is None, else dict (parsed from validated JSON)
    
    Raises:
        RuntimeError on repeated failures after retries
    """
```

### Environment Variables (`.env`)

| Variable | Required | Example | Use |
|----------|----------|---------|-----|
| `LLM_BASE_URL` | Yes | `https://api.openai.com/v1` | API endpoint base URL |
| `LLM_API_KEY` | Yes | `sk-proj-...` | Bearer token |
| `RANKER_MODEL` | Yes | `gpt-4o-mini` | Model for the ranker (fast/cheap is fine) |
| `WRITER_MODEL` | Yes | `gpt-4o` | Model for the writer (quality matters) |
| `RANKER_TIMEOUT_S` | No | `1800` | Ranker per-call timeout (default 1800) |
| `WRITER_TIMEOUT_S` | No | `1200` | Writer per-call timeout (default 1200) |
| `RANKER_MAX_TOKENS` | No | `32000` | Max completion tokens for ranker (default 32000) |
| `WRITER_MAX_TOKENS` | No | `16000` | Max completion tokens for writer (default 16000) |
| `LLM_EXTRA_HEADERS` | No | `{"X-Tenant-Id":"abc"}` | Extra HTTP headers (JSON object string) |
| `LLM_RESPONSE_FORMAT` | No | `json_schema` | Response format: `json_schema` (default), `json_object`, or `none` |
| `LLM_RETRY_NO_CACHE` | No | `1` | Default 1: send `cache: {no-cache: true}` on retries (LiteLLM support) |

---

## Using Structured Output (JSON Schema)

When you need the LLM to return structured data (not free-form text), provide a Pydantic model or TypedDict as the `json_schema` parameter. The response is validated against the schema and automatically parsed into a dict.

### Example: Ranked Items

```python
from typing import TypedDict
from scripts.llm import call_llm

class RankedItem(TypedDict):
    id: str
    score: int  # 0-10
    tags: list[str]
    one_line_why: str

# The LLM receives the schema and must return JSON matching it
response = call_llm(
    messages=[
        {"role": "system", "content": "You are a ranker. Return JSON with scored items."},
        {"role": "user", "content": "Score these papers: ..."},
    ],
    json_schema=RankedItem,
)

# response is now a dict: {"id": "...", "score": 7, "tags": [...], ...}
assert isinstance(response, dict)
```

### Validation Behavior

- If the LLM's response doesn't match the schema, `call_llm()` retries with the schema re-sent
- On repeated validation failures, raises `RuntimeError` after `MAX_RETRIES` attempts
- The retry mechanism sends `cache: {no-cache: true}` (configurable via `LLM_RETRY_NO_CACHE`) to prevent cached bad responses on proxies like LiteLLM

---

## Debugging LLM Failures

### Error Categories

| Error | Typical Cause | Debug Step |
|-------|---------------|-----------|
| HTTP 400 (Bad Request) | Endpoint doesn't support `response_format: json_schema` | Try `LLM_RESPONSE_FORMAT=json_object` or `none` |
| HTTP 401 (Unauthorized) | Invalid `LLM_API_KEY` | Verify token in `.env` (don't paste in logs) |
| HTTP 429 (Rate Limit) | Endpoint throttled | Increase timeout, wait, retry |
| HTTP 500+ (Server Error) | Endpoint down | Check endpoint status page; retry later |
| Timeout | LLM call took too long | Increase `RANKER_TIMEOUT_S` or `WRITER_TIMEOUT_S` in `.env` |
| Parse Error | Response not valid JSON | Check `logs/<slug>/llm-rank.log` for the bad response; retry usually fixes it |
| Validation Error | JSON doesn't match schema | Schema is too strict or LLM isn't following instructions; see "Prompt Tuning" below |

### Where to Look

1. **Pipeline logs**: `logs/<slug>/run-<date>.log` (high-level failure message)
2. **LLM-specific logs**: `logs/<slug>/llm-rank.log` or `llm-write.log` (full request/response)
3. **Run attempt record**: `sqlite3 editions/<slug>/state.db "SELECT outcome, failed_stage, notes FROM run_attempts WHERE date = date('now');"`

### Example: Ranker Fails with Parse Error

```bash
# Check the log
tail -50 logs/agents/llm-rank.log

# Look for lines starting with "ERROR" or "RETRY"
# Example: "ERROR: response is not valid JSON. Retrying..."

# If it happens repeatedly, the endpoint might be unstable
# or the schema is too strict (common with LiteLLM proxies)
```

### Handling LiteLLM / Bedrock Proxies

Some proxies (e.g., LiteLLM fronting Bedrock Claude) reject the `response_format: json_schema` parameter. Workaround:

```bash
# In .env:
LLM_RESPONSE_FORMAT=json_object  # Fallback to older OpenAI format
```

The ranker will still get structured output, but the schema validation is softer (schema hints rather than strict enforcement).

---

## Prompt Tuning and Testing

### Testing Locally Without LLM Calls

Use pytest fixtures to mock LLM responses:

```python
# tests/test_my_feature.py
from unittest.mock import patch

def test_my_feature_with_mocked_llm():
    mock_response = {"id": "123", "score": 8, "tags": ["test"]}
    
    with patch("scripts.llm.call_llm", return_value=mock_response):
        # Now call_llm() returns mock_response instead of hitting the endpoint
        result = my_pipeline_function()
        assert result["score"] == 8
```

### Testing with Real LLM Calls

E2E tests in `tests/e2e/` use real LLM calls against a throwaway database:

```bash
uv run --extra test pytest tests/e2e/ -v -k "test_rank"
```

These tests hit your configured `LLM_BASE_URL` and `RANKER_MODEL`. They're slow but catch real failures (schema mismatches, endpoint issues, etc.). Run them before merging anything that touches LLM behavior.

### Prompt Iteration Workflow

1. **Make a prompt change** in `prompts/rank.md` or `prompts/write.md` or edition-specific fragments
2. **Test with a replay script** (don't re-fetch):
   ```bash
   uv run scripts/replay_writer.py 2026-10-28
   # Output: logs/theme-replay-2026-10-28.json
   ```
3. **Compare the new output** against yesterday's published issue (both in `site/src/content/issues/`)
4. **Merge if satisfied**, or iterate the prompt and replay again

The replay scripts reuse the candidate pool and published item set from a past date, so you can tune prompts without re-fetching or re-ranking.

---

## Common Integration Patterns

### Pattern 1: Adding a New Stage that Calls the LLM

```python
# scripts/my_new_stage.py
from scripts.llm import call_llm
from scripts.edition import Edition

def my_stage(edition: Edition) -> dict:
    # Fetch data
    data = load_data(edition)
    
    # Call LLM
    result = call_llm(
        messages=[
            {"role": "system", "content": "..."},
            {"role": "user", "content": f"Process: {data}"},
        ],
        json_schema=MyResultSchema,
    )
    
    # Save to DB
    save_result(edition, result)
    return result
```

**Don't:**
- Import `openai` directly
- Parse JSON manually (let `llm.py` do it)
- Assume deterministic output (LLM calls are non-deterministic by nature)

### Pattern 2: Fallback / Retry Logic

```python
# If LLM call fails, have a fallback
try:
    result = call_llm(messages=...)
except RuntimeError as e:
    logger.error(f"LLM call failed: {e}")
    # Fallback: return sensible default or propagate error
    result = {"status": "dropped"}  # or re-raise
```

### Pattern 3: Streaming Long Responses (Advanced)

Currently `call_llm()` does not support streaming. If you need streaming (e.g., for UI interactivity), you would:
1. Call the OpenAI SDK directly (break the rule, but justified for UI)
2. Or add a streaming path to `llm.py` (preferred)

File an issue if this is needed.

---

## Tips and Gotchas

1. **Never put `.env` secrets in logs** — `llm.py` scrubs `LLM_API_KEY` from logs automatically
2. **Retries are transparent** — if `call_llm()` retries, you won't see it in your code, but it will be in `logs/<slug>/llm-*.log`
3. **Validation is strict** — if your schema is too narrow, the LLM will retry and eventually fail. Test early with `tests/e2e/`
4. **Timeouts accumulate** — `RANKER_TIMEOUT_S` is per-call, so 3 ranker calls (papers, news, blogs) can take up to 3× the timeout
5. **Cost is not computed automatically** — `runs.cost_usd` is NULL; cost is calculated downstream from `tokens_in` and `tokens_out` using your provider's rates

---

## See Also

- [SECURITY-AI.md](../SECURITY-AI.md) — LLM input/output safety rules
- [prompts/rank.md](../prompts/rank.md) — Ranker prompt template
- [prompts/write.md](../prompts/write.md) — Writer prompt template
- [SPEC.md § Ranker and Writer](../SPEC.md#4-ranker--rankpy-openai-compatible-llm) — Design rationale
- [Debugging Guide](debugging-guide.md) — General troubleshooting including LLM stages
