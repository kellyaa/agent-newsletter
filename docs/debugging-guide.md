# Debugging and Diagnostics Playbook

When a run fails or produces unexpected output, this guide walks you through systematic diagnosis and recovery. All stages emit debug artifacts that tell you exactly what happened.

## Quick Reference

```
Daily pipeline fails?
  → Check logs/run-$(date +%Y-%m-%d).log (orchestrator summary)
  → Per-edition logs: logs/<slug>/run-$(date +%Y-%m-%d).log (ending in DONE or FAILED)

Issue has too few items?
  → Prefilter was too tight: check logs/<slug>/prefilter per-source counts

Ranker hangs or times out?
  → Check LLM endpoint status; adjust RANKER_TIMEOUT_S

Can't tell why an item ranked 4 instead of 8?
  → Use scripts/replay_writer.py to re-run ranker against that date
```

---

## Stage-by-Stage Debugging

### Stage 1: Fetch

**What it does:** Collects ~500-1000 items from RSS, arXiv, HN, Reddit, GitHub

**Log location:** `logs/<slug>/run-<date>.log`

**What to look for:**
```
── fetch ── start
fetch: rss 45 items, arxiv 23 items, hn 67 items, ...
── fetch ── ok
```

**Common failures:**

| Error | Meaning | Fix |
|-------|---------|-----|
| `collector failed: ... 429` | Rate limit on arXiv API | Wait 10+ min, re-run with `./run.sh --edition <slug>` |
| `feed parse error: unexpected end of file` | Malformed RSS feed | The source is broken; temporarily remove or wait for source to fix |
| `connection timeout` | Network issue | Check internet connection; retry |

**Debug:** Check how many items came from each source:
```bash
tail -20 logs/<slug>/run-$(date +%Y-%m-%d).log | grep "fetch:"
```

If a source shows 0 items:
- Source may be broken
- Or that source's URL/credentials expired

### Stage 2: Prefilter

**What it does:** Applies recency, keyword, and dedup gates to reduce 500-1000 items → 50-150 candidates

**Log location:** `logs/<slug>/run-<date>.log`

**What to look for:**
```
── prefilter ── start
prefilter: 450 fetched → 390 recency → 125 keyword → 120 dedup → 120 candidates
── prefilter ── ok
```

**Interpreting the counts:**
- `450 fetched` — items from fetch stage
- `390 recency` — items passing age gate (47% passed; 10% old/stale)
- `125 keyword` — items matching keywords (32% passed; gate working)
- `120 dedup` — items after duplicate removal (99% passed; almost no duplicates)
- `120 candidates` — sent to ranker

**Common issues:**

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Candidates = 0 | Keyword gate too tight (no items matched regex) | Loosen keywords in edition.yaml; re-run check_edition.py |
| Candidates = all fetched | Keyword gate too loose (all items pass) | Tighten keywords; check keyword_gate_bypass sources |
| Candidates dropped 90% | High dedup (recent issue was covered yesterday) | Normal; prefilter is working |

**Debug:** Check per-source breakdown:
```bash
# (From check_edition.py output, or manually inspect state.db)
uv run scripts/check_edition.py <slug>
# Output shows: hn-ai: 45 fetched → 23 keyword passed; reddit-ml: ...
```

If a source drops too many items:
- Keyword gate may not match that source's content
- Try `keyword_gate_bypass: true` for high-signal, low-volume sources

### Stage 3: Rank

**What it does:** LLM scores each candidate 1-10 per section (papers, news, blogs = 3 LLM calls)

**Log location:** `logs/<slug>/run-<date>.log`

**What to look for:**
```
── rank ── start
rank: 120 candidates
  rank.papers: 40 items → 8 featured, 18 appendix, 14 dropped
  rank.news: 45 items → 6 featured, 12 appendix, 27 dropped
  rank.blogs: 35 items → 5 featured, 8 appendix, 22 dropped
── rank ── ok
rank: featured=19, appendix=38, dropped=63, tokens_in=45000, tokens_out=8000
```

**Interpreting:**
- `40 items` — papers candidates sent to LLM
- `8 featured` — scored ≥ featured_min (typically 7)
- `18 appendix` — scored >= appendix_min but < featured_min (typically 4-6)
- `14 dropped` — scored < appendix_min
- `tokens_in/tokens_out` — LLM API usage (important for cost tracking)

**Common failures:**

| Error | Meaning | Fix |
|-------|---------|-----|
| `LLM HTTP 400: bad request` | Schema mismatch or API key expired | Check `LLM_RESPONSE_FORMAT=json_object` (try instead of `json_schema` if Bedrock) |
| `LLM timeout after 1800s` | Ranker call took >30 min | Model is slow; consider faster model or increase RANKER_TIMEOUT_S |
| `JSON parse error: ... not valid` | LLM returned invalid JSON | Rare; usually means model is struggling; retry or use different model |
| `rank: 0 featured, 0 appendix` | All items dropped (score < appendix_min) | Issue will be empty; keyword gate or reader_profile mismatch |

**Debug:** Check token usage and cost:
```bash
# (From rank log line above)
# tokens_in: 45000, tokens_out: 8000
# Cost (GPT-4): (45000 * $0.00003 + 8000 * $0.00006) = ~$1.81
```

If cost is unexpectedly high:
- Too many candidates passed prefilter
- Consider tightening keyword gate

If ranker produces 0 featured items:
- Featured_min threshold too high, OR
- Keyword gate/reader_profile mismatch
- Check logs/<slug>/ for ranked.json debug artifact

**Inspect ranked items:**
```bash
# (If logs/<slug>/ranked.json exists)
jq '.papers[] | select(.score >= 7) | {title, score}' logs/<slug>/ranked.json
# Shows papers that ranked ≥7 (featured)
```

### Stage 4: Write

**What it does:** LLM generates editorial prose summarizing 19 featured items into a newsletter

**Log location:** `logs/<slug>/run-<date>.log`

**What to look for:**
```
── write ── start
write: 19 featured → editions/<slug>/issues/2026-06-15.md
── write ── ok
write: tokens_in=12000, tokens_out=8500, duration_seconds=45
```

**Common failures:**

| Error | Meaning | Fix |
|-------|---------|-----|
| `HTTP 429: rate limit` | Too many API calls too fast | Retry after 1 min; space editions out |
| `LLM timeout` | Writer call took >20 min | Usually means model is stuck; retry with different model |
| `Issue file invalid markdown` | Writer generated bad YAML frontmatter | Rare; check logs/<slug>/theme-replay-*.json for LLM output |

**Debug:** Inspect generated issue:
```bash
head -50 editions/<slug>/issues/2026-06-15.md
# Should show valid YAML frontmatter: date, featured, appendix, metadata
```

If issue looks wrong:
- Prose is bad: ranker scores were off (bad items featured)
- Markdown is broken: writer LLM generated invalid YAML (rare)
- Use `scripts/replay_writer.py` to test new prompts

### Stage 5: Publish

**What it does:** Commits the issue to `content` branch, updates state.db, pushes to GitHub

**Log location:** `logs/<slug>/run-<date>.log`

**What to look for:**
```
── publish ── start
publish: 19 featured, 38 appendix, 63 dropped
publish: state.db updated, 1 row in runs table
── publish ── ok
publish: git commit: "newsletter: 2026-06-15 daily run"
publish: git push to origin/content
```

**Common failures:**

| Error | Meaning | Fix |
|-------|---------|-----|
| `git push failed: non-fast-forward` | Someone else pushed to content branch | Rare in single-user setup; pull and retry |
| `state.db locked` | Database is busy | Retry; usually transient |

---

## Common Diagnostics Workflows

### Workflow 1: "My issue has no featured items"

**Step 1:** Check prefilter
```bash
tail logs/<slug>/run-$(date +%Y-%m-%d).log | grep prefilter
# Look for: "candidates = 0"
```
If candidates = 0 → keyword gate too tight

**Step 2:** If candidates > 0, check rank
```bash
tail logs/<slug>/run-$(date +%Y-%m-%d).log | grep rank
# Look for: "featured=0"
```

**Step 3:** Inspect ranked.json
```bash
# (If logs/<slug>/ranked.json exists)
jq '.papers' logs/<slug>/ranked.json | jq 'sort_by(.score) | reverse | .[0:10]'
# Shows top 10 papers with scores
```

If all scores < featured_min:
- Candidates are low quality (prefilter let through weak items)
- Or featured_min threshold is too high
- Options:
  1. Lower featured_min: `featured_min: 6` instead of 7
  2. Tighten keyword gate (quality over quantity)
  3. Check reader_profile matches items

### Workflow 2: "Ranker is taking 10 minutes; it used to take 3"

**Step 1:** Check candidate count
```bash
tail logs/<slug>/run-$(date +%Y-%m-%d).log | grep "prefilter: ... candidates"
# If 200+ candidates, that's the issue
```

**Step 2:** Tighten keyword gate
```bash
# Edit editions/<slug>/edition.yaml
keywords:
  - '\bagent'    # Keep only the highest-signal patterns
# Remove low-specificity patterns like '\bAI' or '\bmodel'

uv run scripts/check_edition.py <slug>
# Re-run; aim for 50-80 candidates
```

**Step 3:** Re-run
```bash
./run.sh --edition <slug> --force
# Time should return to normal
```

### Workflow 3: "Yesterday's issue looked fine; today's has 80 appendix items"

**Step 1:** Compare candidate counts
```bash
# Yesterday
tail logs/<slug>/run-2026-06-14.log | grep prefilter
# Output: candidates = 50

# Today
tail logs/<slug>/run-2026-06-15.log | grep prefilter
# Output: candidates = 200
```

**Step 2:** Debug why prefilter changed
- A new high-volume source was added?
- Keyword gate regex is broken?
- Dedup is failing (old issues not marked as published)?

Check state.db:
```bash
sqlite3 .worktrees/content/state.db \
  "SELECT published_date, items_featured FROM runs ORDER BY published_date DESC LIMIT 5;"
# Shows the last 5 runs and item counts
```

### Workflow 4: "I want to understand why paper X ranked 6 instead of 8"

**Step 1:** Find the paper in state.db
```bash
sqlite3 .worktrees/content/state.db \
  "SELECT id, title, score, why FROM items WHERE title LIKE '%paper X%';"
# Shows the ranker's scoring and rationale
```

**Step 2:** Replay the ranker with new prompts
```bash
# If you suspect the prompt is wrong, edit editions/<slug>/prompts/rank.*.md
# Then replay:
uv run scripts/replay_writer.py 2026-06-15
# Output: logs/theme-replay-2026-06-15.json

# Inspect:
jq '.papers[] | select(.title | contains("paper X")) | .score' logs/theme-replay-2026-06-15.json
# Shows the new score with your updated prompt
```

**Step 3:** If replay score looks good, deploy
- Push the updated prompt file
- Re-run the pipeline

---

## Debug Artifacts

### Logfiles

| File | Contents |
|------|----------|
| `logs/run-<date>.log` | Orchestrator summary; one line per edition, fail/ok status |
| `logs/<slug>/run-<date>.log` | Full pipeline output; stage-by-stage log lines ending in DONE or FAILED |

### JSON artifacts (in `logs/<slug>/`)

| File | Purpose |
|------|---------|
| `candidates.json` | Items after prefilter; input to ranker |
| `ranked.json` | Items after ranking; scored 1-10 per section |
| `theme-replay-<date>.json` | Replay run output (from scripts/replay_writer.py) |

### Database queries

```bash
# Check run history
sqlite3 .worktrees/content/state.db \
  "SELECT date, items_featured, items_papers, items_news, items_blogs, duration_seconds FROM runs ORDER BY date DESC LIMIT 10;"

# Check item details
sqlite3 .worktrees/content/state.db \
  "SELECT title, score, status, section FROM items WHERE first_seen_date = '2026-06-15' ORDER BY score DESC LIMIT 20;"

# Check failures
sqlite3 .worktrees/content/state.db \
  "SELECT date, outcome, failed_stage, exit_code FROM run_attempts WHERE outcome = 'failed' ORDER BY date DESC LIMIT 5;"
```

---

## Recovery Strategies

### Strategy 1: Retry a failed edition
```bash
./run.sh --edition <slug>
# Picks up where it left off (idempotent)
```

### Strategy 2: Force a clean re-run (post-fetch)
```bash
./run.sh --force --edition <slug>
# Keeps fetched items; re-runs prefilter, rank, write, publish
# (Faster than --refetch; reuses yesterday's items)
```

### Strategy 3: Backfill a missed day
```bash
# Only if the run failed catastrophically
git worktree add .worktrees/content content
uv run scripts/backfill.py --date 2026-06-15 --apply-published
# Reconstructs issue from candidate pool; marks featured items as published
```

### Strategy 4: Inspect a specific stage (no LLM)
```bash
# Test just prefilter on a new source:
uv run scripts/check_edition.py <slug>
# Fetches all sources, applies gates (no LLM, fast)
```

---

## When to Escalate

These are not diagnostic issues; file an issue or contact maintainers:

- Persistent 429s from arXiv (may need backoff tuning)
- LLM endpoint returns 500+ errors (endpoint issue, not pipeline)
- `state.db` is corrupted (rare; suggests filesystem corruption)
- Git push fails with "permission denied" (token/credentials issue)

---

## Monitoring

For continuous operations, script the checks:

```bash
#!/bin/bash
# Daily check after run.sh completes
YESTERDAY=$(date -d yesterday +%Y-%m-%d)
LOG=logs/<slug>/run-$YESTERDAY.log

if ! grep -q "DONE" "$LOG"; then
  echo "Pipeline failed; see $LOG"
  exit 1
fi

FEATURED=$(grep "featured=" "$LOG" | grep -o "featured=[0-9]*" | cut -d= -f2)
if [ "$FEATURED" -lt 5 ]; then
  echo "Warning: only $FEATURED featured items"
fi

echo "Pipeline OK: $FEATURED items featured"
```

---

## See Also

- `README.md` — troubleshooting section
- `docs/prefilter-gates-guide.md` — gate mechanics
- `scripts/replay_writer.py` — test prompt changes
- `scripts/backfill.py` — reconstruct missed issues
