# LLM Integration and Prompting Guide

This guide explains how to integrate new LLM calls into the pipeline, debug LLM failures, and tune prompt behavior. All LLM communication goes through `scripts/llm.py`, a thin wrapper around OpenAI-compatible chat-completions endpoints.

## Quick Start: Adding an LLM Call

All LLM interactions use `scripts/llm.py`, which wraps the OpenAI SDK and handles the parse/truncation retry, structured output, and token-usage logging. Never import `openai` directly in pipeline code.

Scripts in `scripts/` import each other as top-level modules (`scripts/` is on `sys.path` when a script runs, and `tests/conftest.py` adds it for tests), so write `from llm import ...`, not `from scripts.llm import ...`.

```python
from llm import call_llm, ranker_config

cfg = ranker_config()  # RANKER_MODEL / _TIMEOUT_S / _MAX_TOKENS, read now
response = call_llm(
    "Score these items ...",          # one user message
    {                                  # a JSON Schema dict
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    },
    "my_answer",                       # schema_name sent to the endpoint
    model=cfg.model,
    timeout_s=cfg.timeout_s,
    max_tokens=cfg.max_tokens,
    label="my-stage",                  # shows up in logs and the usage log
)
# Always returns a dict parsed from the model's JSON reply.
```

## Architecture: How `llm.py` Works

### Design Principles

1. **Single point of entry** — all LLM calls use `scripts/llm.py`'s `call_llm()` function
2. **Structured output by default** — the request carries a JSON Schema (`response_format: json_schema`, strict) and the reply is parsed into a dict
3. **One retry on a bad reply** — a truncated (`finish_reason=length`), non-JSON or non-object reply is retried once (`max_attempts=2`), immediately, with no backoff. Network errors and 5xx are retried by the OpenAI SDK itself. A content-filter block raises `ContentFilterError` and is never retried.
4. **Logging** — each call logs model, token counts and failures to the stage's log (the run log under `logs/<slug>/` when run by `run.sh`), and appends token usage to `$LLM_USAGE_LOG`
5. **Environment-driven config** — endpoint and headers are read by `llm.py`; per-role model/timeout/token cap only via `llm.ranker_config()` / `llm.writer_config()`, called per LLM call

### Function Signature

```python
def call_llm(
    prompt: str,
    schema: dict[str, Any],
    schema_name: str,
    *,
    model: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,   # 1200
    label: str = "llm",
    max_tokens: int | None = None,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Send a single-turn prompt and return a dict matching the schema.

    Raises RuntimeError if every attempt fails, ContentFilterError (not a
    RuntimeError) if the endpoint's content filter blocks the reply.
    """
```

`call_llm` takes no defaults from the environment for the role: pass `model` / `timeout_s` / `max_tokens` from `ranker_config()` or `writer_config()`.

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

Every call is structured: the `schema` argument is a JSON Schema dict, sent to the endpoint as `response_format` (unless `LLM_RESPONSE_FORMAT` downgrades it), and the reply is parsed into a dict. There is no plain-text mode.

### Example: the ranker

This is what `rank.invoke_ranker` does (`rank.RANKER_OUTPUT_SCHEMA` is the real schema):

```python
from llm import call_llm, ranker_config
from rank import RANKER_OUTPUT_SCHEMA

cfg = ranker_config()
out = call_llm(
    prompt,                      # rendered rubric + candidates JSON
    RANKER_OUTPUT_SCHEMA,
    schema_name="ranker_output",
    model=cfg.model,
    timeout_s=cfg.timeout_s,
    label="papers",
    max_tokens=cfg.max_tokens,
)
rankings = out.get("rankings")   # the caller checks the shape and sanitises entries
```

### Validation Behavior

- `call_llm()` checks that the reply is JSON and a JSON object. If the content is not a single JSON object, it recovers the last complete object that has the schema's `required` keys. It does **not** validate field types; callers sanitise (`rank.sanitize_rankings`, `llm_ids.resolve_ids`).
- A truncated, unparseable or non-object reply is retried once (`max_attempts=2`); if both attempts fail it raises `RuntimeError("llm failed after 2 attempts ...")`
- The retry sends `cache: {no-cache: true}` (disable with `LLM_RETRY_NO_CACHE=0`) so proxies like LiteLLM don't serve the same cached bad reply

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
| Parse Error | Response not valid JSON | The stage log has `could not parse llm content as JSON` and the first 2KB of the raw reply; the one automatic retry usually fixes it |
| Validation Error | JSON doesn't match schema | Schema is too strict or LLM isn't following instructions; see "Prompt Tuning" below |

### Where to Look

1. **Pipeline logs**: `logs/<slug>/run-<date>.log` (every stage's log lines, including `llm` errors, retries and token counts)
2. **LLM artifacts** in `logs/<slug>/`: `rank.rendered.md` / `write.rendered.md` (the prompt sent), `ranker-output-<label>.json` / `writer-output.json` (a reply with the wrong shape), `write.response.json` (the last parsed writer reply)
3. **Run attempt record**: `sqlite3 editions/<slug>/state.db "SELECT outcome, failed_stage, exit_code FROM run_attempts WHERE date = date('now');"`

### Example: Ranker Fails with Parse Error

```bash
# Check the run log
grep -E " llm: " logs/agents/run-$(date +%F).log | tail -50

# Look for "could not parse llm content as JSON", "response truncated" or
# "retry 2/2 after parse/truncation failure"

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

Patch the name in the module that calls it (`rank.call_llm`, `write.call_llm`), since those modules did `from llm import call_llm`:

```python
# tests/test_my_feature.py
from unittest.mock import patch

import rank

def test_ranker_with_mocked_llm():
    with patch("rank.call_llm", return_value={"rankings": []}):
        # invoke_ranker now gets the canned reply instead of hitting the endpoint
        assert rank.invoke_ranker("prompt", "papers") == []
```

### Testing with Real LLM Calls

The test suite never calls a real endpoint (`tests/e2e/` runs fetch and the gates with no LLM and no internet). To check a prompt or schema change against your configured `LLM_BASE_URL`, replay the writer for a past date (below) or run the pipeline for a draft edition with `./run.sh --edition <slug>`. Note that `rank.py` takes no flags: running it is a real ranking that spends tokens and writes statuses to the edition's `state.db`.

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
import edition
import prompts
from llm import call_llm, writer_config

MY_SCHEMA = {"type": "object", "properties": {...}, "required": [...]}

def my_stage(data: str) -> dict:
    prompt = prompts.render("write", edition.current()) + f"\n\n{data}"
    cfg = writer_config()  # per call, never a module-level constant
    return call_llm(
        prompt, MY_SCHEMA, "my_result",
        model=cfg.model, timeout_s=cfg.timeout_s, max_tokens=cfg.max_tokens,
        label="my-stage",
    )
```

**Don't:**
- Import `openai` directly
- Parse JSON manually (let `llm.py` do it)
- Assume deterministic output (LLM calls are non-deterministic by nature)

### Pattern 2: Fallback / Retry Logic

```python
# If LLM call fails, have a fallback
try:
    result = call_llm(prompt, MY_SCHEMA, "my_result", model=cfg.model)
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

1. **Never put `.env` secrets in logs** — `llm.py` never logs `LLM_API_KEY`, and logs only the *names* of `LLM_EXTRA_HEADERS`; don't log them yourself
2. **Retries are transparent** — if `call_llm()` retries, you won't see it in your code, but the stage log has `retry 2/2 after parse/truncation failure`
3. **Strict schemas are endpoint-enforced** — in `json_schema` mode a schema the endpoint can't satisfy gives an HTTP 400 or a bad reply; check with a mocked unit test and one real draft-edition run
4. **Timeouts accumulate** — `RANKER_TIMEOUT_S` is per-call, so 3 ranker calls (papers, news, blogs) can take up to 3× the timeout
5. **Cost is not computed automatically** — `runs.cost_usd` is NULL; cost is calculated downstream from `tokens_in` and `tokens_out` using your provider's rates

---

## See Also

- [SECURITY-AI.md](../SECURITY-AI.md) — LLM input/output safety rules
- [prompts/rank.md](../prompts/rank.md) — Ranker prompt template
- [prompts/write.md](../prompts/write.md) — Writer prompt template
- [SPEC.md § Ranker and Writer](../SPEC.md#4-ranker--rankpy-openai-compatible-llm) — Design rationale
- [Debugging Guide](debugging-guide.md) — General troubleshooting including LLM stages
