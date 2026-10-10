# Per-newsletter cost — investigation (issue #11)

Filed by scanner agent (ACMM L5, hold-gated). Analysis only — no code changes to the pipeline in this branch. Sole change: this document, so a review surface exists in a PR.

## 1. Where cost comes from

Two LLM stages per day:

| Stage | Model default | Max output cap | Timeout | Calls |
|-------|---------------|----------------|---------|-------|
| `scripts/rank.py` | `RANKER_MODEL`, default `gpt-4o-mini` | `RANKER_MAX_TOKENS=32000` | 1800 s | 1 per section (papers/news/blogs) = **up to 3** |
| `scripts/write.py` | `WRITER_MODEL`, default `gpt-4o-mini` | `WRITER_MAX_TOKENS=16000` | 1200 s | **1** |

Ceiling per healthy publish: **~4 LLM calls**. With `call_llm(max_attempts=2)` the worst case on repetition-loop retries is 8 calls.

## 2. Accuracy — is any cost measured today?

**No.** `scripts/llm.py:92-100` reads `resp.usage`, logs `prompt_tokens / completion_tokens / total_tokens`, then discards the object. Callers (`rank.py:123`, `write.py:175`) only receive the parsed JSON dict.

`runs` declares `tokens_in`, `tokens_out`, `cost_usd`, `duration_seconds` (`scripts/db.py:56-59`). **All four are NULL for every row ever written** — `scripts/publish.py:record_run` inserts only item counts.

Consequence: no per-newsletter cost figure exists to be accurate about. Any dashboard is a fiction until #13 lands.

## 3. Drivers (once we can measure)

Ranked by expected share of total tokens:

1. **Ranker input.** Each section call ships every prefiltered candidate (title + summary + tags). Papers is the long pole; `rank.py:33-39` says healthy heaviest section lands at ~24k completion tokens; input side is likely 30–80k prompt tokens on busy days. **Prompt-side dollars concentrate here.**
2. **Ranker output.** JSON schema requires `id / score / section_hint / why` per candidate; output scales linearly with candidate count.
3. **Writer input.** Up to `RAW_TEXT_MAX=1500` chars per featured item plus `PREV_NEWSLETTER_MAX=4000` chars of yesterday's issue. ~20 featured items ≈ ~35k chars ≈ ~10k prompt tokens.
4. **Writer output.** Typical 6–8k tokens, capped at 16000 (2× observed).
5. **Retry amplification.** `max_attempts=2` doubles the worst case; comment at `write.py:35-37` cites 65k tokens on unparseable truncated JSON.

## 4. Reduction options (cheap → expensive)

**a. Trim ranker input.** Ranker sees every candidate at full length. Add per-candidate char cap (mirror `RAW_TEXT_MAX` from write.py). Config knob only; big prompt-side win. Concrete: `RANKER_ITEM_TEXT_MAX=800` env, applied in `rank.py` before prompt assembly.

**b. Tighten output caps.** `RANKER_MAX_TOKENS=32000` is 33% above observed healthy peak (~24k). Drop to 26000 → fails 2 s faster on repetition loops without shrinking healthy runs. `WRITER_MAX_TOKENS=16000` already conservative (2× observed).

**c. Model-tier the two stages.** Ranker is scoring on structured text; a cheaper model (via `RANKER_MODEL` env, e.g. `gpt-4.1-nano` or local via `LLM_API_BASE`) is likely equivalent at a fraction of the prompt cost. Writer needs quality — keep it mid tier.

**d. Prompt-cache the prev-newsletter block.** The `PREV_NEWSLETTER_MAX=4000` block is identical across a day's calls; free savings if the backend supports prompt caching.

**e. Batch API for ranker.** Offline ranking at ~50% list price if the backend supports it.

## 5. Blocked-on

All of §4 is measurable only after #13 populates `runs.tokens_*` / `cost_usd`. **Do #13 first**, collect 14 days of data, then act on drivers with real numbers instead of estimates.

## 6. Related work in flight

- PR #136 (hold-gated) wires token/cost accounting via a JSON sidecar approach — closes the §2 measurement gap.
- No PR exists yet for any of §4 (a)–(e); this document is the input to that decision.

---
*Filed by scanner agent (ACMM L5 — hold-gated mode). Investigation only; no code changes.*
