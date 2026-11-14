# LLM Error Handling and Content Filtering Guide

When LLM calls fail in the ranking and writing stages, the pipeline has sophisticated recovery mechanisms built in. This guide explains what happens when things go wrong, how to diagnose failures, and when to intervene.

---

## Overview: Error Classes

The pipeline distinguishes two types of LLM failures:

| Error | Cause | Recovery | Logged As |
|-------|-------|----------|-----------|
| **ContentFilterError** | Content filter blocked the batch | Bisect the batch; retry subsets | Warning: `content filter blocked a batch` |
| **RuntimeError** | Bad reply: truncated, not JSON, not an object | `call_llm` retries once, immediately; rank then leaves the batch unscored | Error: `ranker call failed for a batch of N items` |
| **`openai.APIError`** (timeout, 429, 5xx, 4xx) | The HTTP call itself failed | The OpenAI SDK retries (default 2 retries, with its own backoff); then it propagates and **fails the stage** | Traceback in the run log; `FAILED at stage=<stage>` |

ContentFilterError and RuntimeError are caught separately because their recovery strategies differ. `openai.APIError` is neither, so `call_llm`'s retry and `rank`'s per-section isolation do not catch it (see #711).

---

## ContentFilterError: Content Filtering

### What It Is

The LLM endpoint's content filter (OpenAI's, Claude's, or the provider's own) rejected the batch of items as a whole. This typically happens when:

1. A paper abstract or title contains flagged content (e.g., violent text, even in an academic context)
2. A news headline or blog post excerpt triggers the filter
3. The batch size is large enough that coincidental phrase overlap trips the filter

### How the Pipeline Recovers

When `rank.py` hits a ContentFilterError, it **bisects the batch**:

```python
except ContentFilterError:
    if len(items) == 1:
        # Single item was filtered; leave it unscored (appendix fallback)
        log.warning("%s: content-filtered item left unscored", section, items[0].title)
        return []
    mid = len(items) // 2
    log.warning("%s: content filter blocked a batch of %d; bisecting into %d + %d",
                section, len(items), mid, len(items) - mid)
    # Recursively rank the two halves
    return rank_section(section, items[:mid], rubric) + rank_section(section, items[mid:], rubric)
```

**Practical effect:** The batch is split in half and sent again. If a half-batch also gets filtered, it is split again. Single items that get filtered are skipped entirely and fallback to appendix (if eligible by score).

### Example Logs

When content filtering happens, you'll see in `logs/<slug>/run-<date>.log`:

```
── rank ── start
papers: ranker returned 45 entries
papers: content filter blocked a batch of 87; bisecting into 43 + 44
papers: ranker returned 22 entries (batch 1 of 2)
papers: ranker returned 23 entries (batch 2 of 2)
news: ranker returned 12 entries
── rank ── ok
```

The run continues normally; you do not need to re-run it.

### When to Investigate

Content filtering is **not a bug** — it is the endpoint's safety mechanism working as designed. You only need to investigate if:

1. **Many items are being filtered** (e.g., >25% of items across runs)
2. **Legitimate content is blocked** (e.g., academic abstracts on AI safety research)
3. **The same sources repeatedly trigger it** (suggests a source is incompatible with your endpoint)

### Solutions

**If a specific source triggers repeated filtering:**

Option A: Temporarily disable the source in `editions/<slug>/sources.yaml` and contact the source maintainer or your endpoint provider.

Option B: Lower the endpoint's content-filter sensitivity (if your provider exposes this; OpenAI does not, but some local endpoints do).

Option C: Switch to a different LLM endpoint that has more permissive filtering (some endpoints allow you to disable the filter entirely in development).

---

## RuntimeError: Transient Failures

### What It Is

`call_llm` raises `RuntimeError` when the endpoint answered (HTTP 200) but the reply is unusable:

| Cause | Log line |
|-------|----------|
| Truncated at `max_tokens` (`finish_reason=length`, usually a repetition loop) | `response truncated (finish_reason=length)` |
| Not JSON, and no complete JSON object with the schema's required keys inside it | `could not parse llm content as JSON` |
| JSON, but not an object | `llm returned non-object` |
| No choices in the response | `llm response had no choices` |

HTTP-level failures (429, 5xx, timeouts, connection errors) are **not** `RuntimeError`: they are `openai.APIError` subclasses, retried by the OpenAI SDK itself (2 retries by default, with its own backoff), and then propagated. See [HTTP failures](#http-failures-openaiapierror) below.

### How the Pipeline Recovers

`llm.py`'s `call_llm()` makes up to `max_attempts` attempts (default 2, so one retry), with no sleep between them:

```python
for attempt in range(1, max_attempts + 1):
    if attempt > 1:
        log.warning("%s: retry %d/%d after parse/truncation failure", label, attempt, max_attempts)
    try:
        return _one_shot(..., bypass_cache=attempt > 1 and retry_no_cache)
    except RuntimeError as e:
        last_err = e
        continue
raise RuntimeError(f"llm failed after {max_attempts} attempts for {label}: {last_err}")
```

**Behavior on retry:**

- The retry adds `cache: {no-cache: true}` to the request body (unless `LLM_RETRY_NO_CACHE=0`). This prevents LiteLLM proxies from serving the same bad cached response.
- If both attempts fail, `RuntimeError("llm failed after 2 attempts ...")` propagates to `rank.py` or `write.py`.
- `ContentFilterError` and `openai.APIError` are not caught by this loop.

### Example Logs

**Successful retry (transparent to the user):**

```
ERROR llm: papers (attempt 1): response truncated (finish_reason=length); first 2KB: ...
WARNING llm: papers: retry 2/2 after parse/truncation failure
INFO llm: papers (attempt 2): prompt_tokens=... completion_tokens=... total=...
```

**Both attempts failed (rank continues with the appendix fallback):**

```
ERROR rank: papers: ranker call failed for a batch of 87 items (llm failed after 2 attempts
        for papers: ...); leaving unscored (appendix fallback) instead of aborting the run
```

### HTTP failures (`openai.APIError`)

A timeout, 429, 5xx or other HTTP error is retried by the OpenAI SDK, then raised as `openai.APITimeoutError`, `RateLimitError`, `InternalServerError`, etc. None of these is a `RuntimeError`, so today:

- `call_llm` does not retry it again;
- `rank.rank_section` does **not** isolate it: the exception leaves `rank.py`, the rank stage fails, and the run is recorded `failed` at stage `rank` (unlike a bad reply, which only costs that section);
- `write.py` fails the write stage the same way.

Re-run with `./run.sh --failed` once the endpoint recovers. Whether rank should isolate HTTP errors per section like bad replies is an open maintainer decision on #711.

### Timeout Configuration

The per-role timeout is set via environment variables (defaults are generous):

| Variable | Default | Use |
|----------|---------|-----|
| `RANKER_TIMEOUT_S` | 1800 (30 min) | Per ranker call; papers can be slow on heavy days |
| `WRITER_TIMEOUT_S` | 1200 (20 min) | Per writer call |

If you see consistent timeouts:

1. **Check your endpoint's latency** (e.g., `curl -w %{time_total}`)
2. **Increase the timeout** in `.env` if you're using a slower but cheaper model
3. **Switch to a faster endpoint** if the endpoint is consistently slow

Example: Using a local vLLM that's slower on your hardware:

```bash
RANKER_TIMEOUT_S=3600   # 1 hour for a slow local model
WRITER_TIMEOUT_S=1800   # 30 min
```

---

## What Happens When Ranking Fails

When `rank.py` catches a RuntimeError (both `call_llm` attempts failed on a bad reply), it **leaves all items in that batch unscored**:

```python
except RuntimeError as e:
    log.error(
        "%s: ranker call failed for a batch of %d items (%s); "
        "leaving unscored (appendix fallback) instead of aborting the run",
        section, len(items), e,
    )
    return []  # No scored items; all will go to appendix if they pass other gates
```

**Practical effect:**

- Items in the failed section are **not dropped**; they move to appendix (if `appendix_min` score is defined).
- The pipeline **does not abort**; other sections and the writer stage continue.
- You get a **newsletter with fewer featured items** in the failed section.
- The run is recorded as `ok` (not `failed`), but the failure is logged as an error and you receive a notification.

### Fallback Logic for Unscored Items

After ranking, `publish.py` checks if there are unscored items and assigns them to appendix if eligible:

```python
# In rank.py's assign_statuses():
if score is None or score < appendix_min:
    status = "dropped"  # Not eligible for appendix
elif score < featured_min:
    status = "appendix"  # Unscored items land here
else:
    status = "featured"
```

---

## What Happens When Writing Fails

If the main writer call fails (a `RuntimeError` after both attempts, a `ContentFilterError`, or an HTTP error), the run **aborts**:

`write.py` does not catch errors from the main writer call: the exception leaves `write.main`, the stage exits non-zero and the issue file is not written. (Only the follow-up call that asks for summaries the writer left out is wrapped; if it fails, the issue goes out without those items.)

Unlike ranking (which is per-section), **writing produces the entire issue**. If the writer fails, there is no fallback — the issue cannot be published without new prose.

**When this happens:**

1. `publish.py` does not run (skipped by `run_edition.sh` because `write.py` exited non-zero).
2. No issue file is written.
3. The run is recorded as `failed` at stage `write`.
4. You receive a notification: `"agents failed at stage write"`
5. The next run (or manual re-run with `./run.sh --edition agents`) will re-attempt from the start of that day.

### Recovery Steps

If writing fails:

1. **Check the log:** `tail -50 logs/agents/run-$(date +%Y-%m-%d).log`
2. **Diagnose:** Is it a rate limit (429), timeout, endpoint down, or malformed response?
3. **Retry manually:**
   ```bash
   ./run.sh --edition agents --force
   ```
   This re-runs from the start of the pipeline (cheaper than `--refetch`), or just:
   ```bash
   ./run.sh --failed
   ```
   to re-run only editions that failed today.

---

## Monitoring and Alerts

### Log Paths

| Path | Contains |
|------|----------|
| `logs/<slug>/run-<date>.log` | Per-edition full output; ranked/written output |
| `logs/<slug>/usage-<date>.jsonl` | Token counts per LLM call (one JSON line per call) |
| `logs/<slug>/rank.rendered.md`, `write.rendered.md` | The rendered prompt each stage sent |
| `logs/<slug>/ranker-output-<label>.json`, `writer-output.json` | A reply that parsed but had the wrong shape |
| `logs/<slug>/write.response.json` | The last parsed writer reply |

### Notifications

- **On ranking failure (section):** Logged as a warning; pipeline continues. No notification.
- **On writing failure (entire issue):** Logged as an error; pipeline aborts. You get a macOS/Linux notification.
- **On all retries exhausted:** Error logged; run marked `failed at stage write`.

### Custom Monitoring

To alert on specific patterns, query `run_attempts` in `state.db`:

```sql
SELECT date, stage, error_code FROM run_attempts
WHERE outcome = 'failed' AND date >= datetime('now', '-7 days')
ORDER BY date DESC;
```

---

## Configuration: Tuning Retry & Content-Filter Behavior

### LLM_RETRY_NO_CACHE

Default: `1`

When `llm.py` retries after a failure, it sends:

```json
{
  "extra_body": {
    "cache": {"no-cache": true}
  }
}
```

This tells LiteLLM proxies not to serve a cached bad response on the retry.

**Set to `0`** if your endpoint rejects the `cache` parameter:

```bash
LLM_RETRY_NO_CACHE=0
```

### LLM_RESPONSE_FORMAT

Default: `json_schema`

Controls the response format mode sent to the endpoint:

| Value | Behavior |
|-------|----------|
| `json_schema` | Strict JSON schema (OpenAI 2024+, vLLM, llama.cpp). Default. |
| `json_object` | Loose JSON mode (some Bedrock/LiteLLM setups). Use if you get HTTP 400 on `json_schema`. |
| `none` | No structured-output mode; rely on the prompt to produce JSON. Risky; may get invalid JSON. |

**Example: LiteLLM-fronted Bedrock that rejects `json_schema`:**

```bash
LLM_RESPONSE_FORMAT=json_object
```

---

## Troubleshooting Flowchart

**The pipeline ran but featured items are missing:**

1. Check rank log: `grep "content filter" logs/<slug>/run-<date>.log`
   - If present: Content filtering bisected the batch; items may have moved to appendix. **Not an error.**
2. Check rank log: `grep "ranker call failed" logs/<slug>/run-<date>.log`
   - If present: Ranker failed after retries; items unscored. Check endpoint status and re-run.
3. Check featured count in the issue: `sqlite3 .worktrees/content/state.db "SELECT items_featured FROM runs WHERE date = '$(date +%Y-%m-%d)'"`
   - If 0: Writer may have failed silently. Check `run_attempts`.

**The run failed at stage `write`:**

1. Check the writer error: `grep -A 5 "writer call failed" logs/<slug>/run-<date>.log`
2. If HTTP 429: Rate limited. Wait a few minutes; re-run with `./run.sh --failed`.
3. If HTTP 500+: Endpoint down. Check the endpoint's status page; retry in a few minutes.
4. If timeout: Endpoint is slow. Increase `WRITER_TIMEOUT_S` in `.env`.

---

## Further Reading

- [Debugging and Diagnostics Playbook](debugging-guide.md) — full stage-by-stage troubleshooting
- [LLM Integration and Prompting Guide](llm-integration-guide.md) — adding new LLM calls, structured output schemas
- [Environment Configuration Reference](environment-configuration-reference.md) — all env vars
