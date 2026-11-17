# Performance and Cost Optimization Playbook

Practical strategies to reduce pipeline costs and improve performance. This guide complements [performance-and-cost-guide.md](performance-and-cost-guide.md) with real-world optimization patterns and before/after examples.

---

## When to Optimize

Before making changes, establish a baseline:

```bash
# Last 14 days of costs and token usage per edition:
sqlite3 editions/agents/state.db \
  "SELECT date, items_featured, cost_usd, tokens_in + tokens_out as total_tokens
   FROM runs ORDER BY date DESC LIMIT 14"
```

**Good candidates for optimization:**
- Cost per issue is rising without reason
- Ranker calls are timing out or hitting token limits
- Too many items are reaching the ranker (prefilter is too loose)
- Featured item count is inconsistent day-to-day

---

## Quick Wins (No Tuning Required)

### 1. Use a Cheaper Ranker Model

**Effect:** 30–60% cost reduction for the ranker (the biggest expense).

The ranker scoring task is straightforward (classify and score items against a rubric). Smaller, cheaper models often score as well as larger ones.

**Before:**
```bash
RANKER_MODEL=gpt-4o        # ~$15–30/1M input tokens
WRITER_MODEL=gpt-4o        # ~$15–30/1M input tokens
```

**After:**
```bash
RANKER_MODEL=gpt-4o-mini   # ~$0.15–0.60/1M input tokens
WRITER_MODEL=gpt-4-turbo   # Better quality, still cheaper than gpt-4o
```

**Testing:** Set the new model, run one edition, inspect `logs/<slug>/ranked.json` to confirm scoring makes sense:
```bash
uv run scripts/check_edition.py agents
RANKER_MODEL=gpt-4o-mini ./run.sh --edition agents
# Check logs/agents/ranked.json for sanity
```

If featured item count or distribution looks reasonable, the model is a good fit.

### 2. Reduce Max Tokens Conservatively

**Effect:** 5–15% cost reduction if set too high.

High `RANKER_MAX_TOKENS` and `WRITER_MAX_TOKENS` don't cause problems until they're **too low** and trigger truncation/retries. Conservative reductions save without risk.

**Current defaults:**
```bash
RANKER_MAX_TOKENS=32000   # Usually only ~3–7k used per section
WRITER_MAX_TOKENS=16000   # Usually ~2–4k used
```

**Safe reductions:**
```bash
RANKER_MAX_TOKENS=16000   # Still 2–3x typical usage
WRITER_MAX_TOKENS=8000    # Still 2x typical usage
```

**Test:** Run a full day and check logs:
```bash
tail -50 logs/agents/run-$(date +%Y-%m-%d).log | grep -i "token\|truncat"
```

If no `truncation` or `retry` messages appear, the limit is safe.

### 3. Enable Caching (if your endpoint supports it)

**Effect:** 10–40% cost reduction on retry or re-runs (depends on hit rate).

If your LLM endpoint supports prompt caching (e.g., Claude Batch API, some vLLM configs), cached prompts are cheaper or free.

**Setup:**
```bash
LLM_EXTRA_HEADERS='{"anthropic-beta": "prompt-caching-2024-07-16"}'   # Claude
# or with vLLM:
LLM_EXTRA_HEADERS='{"Cache-Control": "cache_me"}'
```

Check your provider's caching docs. The ranker's rubric is a perfect caching candidate (same for every call, sent 3 times per run).

### 4. Use a Smaller Writer Model (Careful)

**Effect:** 20–40% cost reduction if quality is acceptable.

The writer sees only 12–17 featured items (not 100+ candidates like the ranker), so a smaller model might produce acceptable editorial prose. **Test first**—quality degradation here is user-visible.

**Before:**
```bash
WRITER_MODEL=gpt-4        # Higher quality prose; slower, more expensive
```

**After (test carefully):**
```bash
WRITER_MODEL=gpt-4o-mini  # Faster, cheaper; results vary
```

**Testing:** Run one day, read the output:
```bash
# After write.py finishes, check the issue file
cat .worktrees/content/editions/agents/issues/$(date +%Y-%m-%d).md | head -100
```

If the prose is acceptable, cost-effective; if it's too terse or grammatically odd, revert to a better model.

---

## Medium Effort: Tuning for Your Audience

### 5. Tighten the Keyword Gate

**Effect:** 20–50% token savings by reducing candidate volume.

Fewer candidates = fewer items to rank = lower cost. The keyword gate is the most direct upstream control.

**Check current performance:**
```bash
uv run scripts/check_edition.py agents 2>&1 | grep -A 10 "per-source"
```

Look for which sources contribute the most volume. If one source sends 200 items but only 5 end up featured, its keyword gate is too loose.

**Tighten by:**

1. **Add more specific keywords** in `edition.yaml`:
   ```yaml
   keywords:
     - "(?i)agent|multi-agent|autonomy"      # Before
     - "(?i)(?:multi-agent|autonomous|swarm)" # After: more specific
   ```

2. **Increase per-source overrides** to reject low-signal sources:
   ```yaml
   sources:
     reddit:
       - subreddit: MachineLearning
         keyword_gate_override: "(?i)(?:agent|autonomy|agentic)"
   ```

3. **Use `keyword_gate_bypass: false`** on low-signal sources instead of `bypass: true`

**Test:**
```bash
uv run scripts/check_edition.py agents
# Output should show candidate count dropping
```

A 20–30% candidate reduction is normal; 50%+ is aggressive and may hurt quality.

### 6. Increase Prefilter Caps (the Opposite)

**Effect:** Improves quality if too few items are reaching the ranker.

If featured item counts are below target (e.g., you want 15 but get 8), the prefilter may be rejecting too many candidates.

**Check:**
```bash
sqlite3 editions/agents/state.db "SELECT date, items_candidate FROM runs ORDER BY date DESC LIMIT 7"
```

If `items_candidate` is consistently <30, the prefilter is too tight.

**Loosen by:**

1. **Increase the recency window** in `edition.yaml`:
   ```yaml
   prefilter:
     recency_days: 3  # Before: catch more recent items
     # After:
     recency_days: 5  # Catch a wider time window
   ```

2. **Broaden the keyword regex**:
   - Add more synonyms: `agent|autonomous|orchestration|LLM-driven`
   - Use word boundaries less strictly

3. **Enable `keyword_gate_bypass`** on high-quality sources that need coverage:
   ```yaml
   sources:
     arxiv:
       - query: "cat:cs.AI"
         keyword_gate_bypass: true  # Always include; gate still applies
   ```

**Test:**
```bash
uv run scripts/check_edition.py agents
```

Aim for 80–150 candidates; above that, the ranker's token bill rises without much benefit.

---

## Advanced: Batching Multiple Runs

### 7. Run Multiple Editions in Parallel

**Effect:** Better throughput if you have multiple editions (not cost savings, but speed).

By default, `run.sh` runs editions **sequentially** (one finishes, next starts). If you're okay with higher concurrent load on your LLM endpoint, you can parallelize:

**Before (sequential):**
```bash
./run.sh  # Edition 1: 10 min, Edition 2: 10 min → 20 min total
```

**After (parallel):**
```bash
./run.sh --edition agents &
./run.sh --edition research &
wait
# Both run at once → ~10 min total if endpoint can handle it
```

**Warning:** This doubles your concurrent LLM requests. Check your endpoint's rate limits:
```bash
# OpenAI: https://platform.openai.com/account/rate-limits
# Groq: Check your dashboard
# Self-hosted: Confirm your GPU/vLLM can handle it
```

**Setup:** Add to a cron or systemd timer:
```bash
0 7 * * * /path/to/agent-newsletter/run.sh --edition agents &
0 7 * * * /path/to/agent-newsletter/run.sh --edition research &
```

or modify `run.sh` to `run_in_parallel()` (custom logic; not provided).

---

## Advanced: Custom Retry and Recovery Strategies

### 8. Lower Timeouts for Faster Failure Recovery

**Effect:** Smaller wall-time impact when LLM endpoint is slow or down.

If your endpoint is flaky, aggressive timeouts let you retry faster:

**Before:**
```bash
RANKER_TIMEOUT_S=1800   # 30 min — waits a long time for a slow endpoint
WRITER_TIMEOUT_S=1200   # 20 min
```

**After (if endpoint is flaky):**
```bash
RANKER_TIMEOUT_S=300    # 5 min — fail fast, retry sooner
WRITER_TIMEOUT_S=180    # 3 min
```

**Tradeoff:** If your endpoint is just slow (not down), lower timeouts will cause false failures. Use only if you:
1. Know your endpoint sometimes hangs
2. Have retry logic (watchdog running `./run.sh --failed`)
3. Are okay with ~5–10% retries on bad days

### 9. Aggressive Content-Filter Bisection for Reliability

**Effect:** Works around overly strict content filters; trades cost for reliability.

If the ranker's content filter rejects a batch ("This violates our policy"), `rank.py` splits the batch and re-sends. Each split re-sends the rubric (extra tokens).

**Understand the cost:**
- First call: 100 items + rubric → rejected → 50k tokens wasted
- Bisection: 50 items + rubric (x2) → both pass → 30k tokens for retry

**Tradeoff:** Bisection costs more but works around filters. If you see frequent content-filter rejections:
```bash
tail -20 logs/agents/run-$(date +%Y-%m-%d).log | grep -i "content.filter"
```

If this happens >1 per week, consider:
1. Switching to an endpoint with looser filters
2. Lowering `RAW_TEXT_MAX` in `scripts/write.py` (fewer words, less likely to trigger filter)
3. Using a less aggressive rubric (remove strong adjectives from `prompts/rank.md`)

---

## Monitoring Your Optimizations

### Track savings over time

After making changes, compare cost/tokens across weeks:

```bash
sqlite3 editions/agents/state.db \
  "SELECT 
     strftime('%Y-W%W', date) AS week,
     COUNT(*) as runs,
     AVG(cost_usd) as avg_cost,
     SUM(tokens_in + tokens_out) as total_tokens
   FROM runs
   WHERE date >= date('now', '-4 weeks')
   GROUP BY week
   ORDER BY week DESC"
```

**Healthy baseline:** <5% week-to-week variation (normal fluctuation in item counts/topics).

### Set alerts

Automate cost tracking in your monitoring system:

```bash
# Example: Alert if daily cost exceeds threshold
sqlite3 editions/agents/state.db \
  "SELECT MAX(cost_usd) FROM runs WHERE date = date('now')" \
  | awk '{ if ($1 > 5.0) print "High cost alert: $" $1 }'
```

---

## Before/After: Real Optimization Example

**Scenario:** Running two editions (agents, research); costs are $200/month and rising.

**Baseline (week 1):**
- RANKER_MODEL=gpt-4o, WRITER_MODEL=gpt-4o
- RANKER_MAX_TOKENS=32000, WRITER_MAX_TOKENS=16000
- Loose keyword gate: all AI/ML topics
- ~250 candidates/run
- Cost: $80/week (~$320/month for 4 weeks)

**Optimization (week 2-3):**

1. Switch to `gpt-4o-mini` ranker (-60%)
2. Tighten keyword gate to exclude blog recaps (-30% candidates)
3. Drop max tokens to 16k / 8k (-10%)
4. Test writer quality with `gpt-4o-mini` (acceptable; -40%)

**Result (week 4):**
- Cost: ~$18–20/week (~$72–80/month)
- **75% cost reduction**
- Featured item count: stable 12–15 (no quality regression)
- Total optimization time: ~4 hours (testing, tuning, monitoring)

---

## Gotchas and Anti-Patterns

| Mistake | Why It Hurts | Fix |
|---------|-------------|-----|
| **Set max tokens too low** | Truncation → retry → double cost | Leave 2x headroom above observed usage |
| **Tighten keyword gate too much** | Too few candidates → poor featured quality | Tune in increments; test daily |
| **Lower timeouts without monitoring** | False failures on slow (not broken) endpoints | Check endpoint SLAs; keep timeouts generous |
| **Forget to test model changes** | Wrong model for your task (e.g., ranker producing prose) | Always run one full day with new model |
| **Change multiple things at once** | Can't tell which change helped/hurt | One change per week; monitor baseline |

---

## Further Reading

- [performance-and-cost-guide.md](performance-and-cost-guide.md) — Technical details on where tokens go
- [prefilter-gates-guide.md](prefilter-gates-guide.md) — Deep dive on the keyword and recency gates
- [edition-yaml-reference.md](edition-yaml-reference.md) — All config options and their effects
- [metrics/README.md](metrics/README.md) — How to query cost and token metrics from state.db

