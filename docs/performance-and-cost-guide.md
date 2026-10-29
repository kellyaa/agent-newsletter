# Performance and Cost Guide

Where a run spends time and money, which settings change that, and how to measure
it. Everything here is derived from the code in `scripts/` and the figures already
recorded in [README.md](../README.md) and [SPEC.md](../SPEC.md); prices are
deliberately not quoted, because they depend on your provider. Price the token
counts below against your own rate card.

Related: [prefilter-gates-guide.md](prefilter-gates-guide.md) (the gates that decide
how many items reach the LLM), [edition-yaml-reference.md](edition-yaml-reference.md)
(`sections:` and `keywords:`), [metrics/README.md](metrics/README.md) (the tables
used for monitoring).

## 1. Where a run spends its time and money

One edition runs these stages in order (`scripts/run_edition.sh`):

| Stage | LLM? | What dominates |
|---|---|---|
| `fetch` | no | Network. Sources are fetched sequentially; arXiv sleeps 3 s between queries and Reddit 2 s between subreddits (`iter_collectors`). Each request has a 20 s timeout. |
| `prefilter` | no | Local SQLite and regex work. Negligible. |
| `rank` | **yes** | One call per section (papers, news, blogs). The papers call is the long pole. |
| `write` | **yes** | One call over the featured items plus a capped appendix. |
| `publish` | no | Local. |

So **all spend is the rank and write calls**; there is no other billable work. The
repo's recorded experience (README "Cost & runtime", SPEC "LLM API lessons
learned") is roughly:

- A typical run is about 10 minutes wall time, mostly the three ranker calls and
  one writer call.
- Ranker calls took roughly 3 to 7 minutes each, and the writer call costs less
  than a ranker call because it sees 12 to 17 featured items rather than 100+
  candidates.

Treat those as the project's observations on its own models, not guarantees.

Editions run **one after another** under a single lock in `run.sh`, so for N
editions expect wall time and cost to scale roughly linearly with N.

### What the ranker is billed for

For each section the prompt is: a security notice, the candidates as indented JSON
(`id`, `source`, `url`, `title`, `author`, `published_at`, `raw_text`), then the
rendered rubric. Input tokens therefore scale with **item count x `raw_text`
length**, plus a fixed rubric cost paid once per call. The output is one ranking
entry per item (`id`, `score`, `tags`, `why` up to 300 chars), so output tokens
scale with item count too. The heaviest healthy papers call is noted in
`scripts/rank.py` at around 24k completion tokens.

Adapters cap `raw_text` at 4000 characters, and the ranker sends it as stored. The
writer trims `raw_text` to 1500 characters and the previous newsletter to 4000
(`RAW_TEXT_MAX`, `PREV_NEWSLETTER_MAX` in `scripts/write.py`); SPEC records that the
trim "was a measurable cost reducer".

### Hidden multipliers

- **Retries.** `call_llm()` makes up to 2 attempts when a response is unparseable or
  truncated. A retry is billed in full, and failed responses are recorded in the
  usage log because they were billed.
- **Bisecting.** If the endpoint's content filter rejects a ranker batch, `rank.py`
  splits it in half and ranks each half separately, which re-sends the rubric for
  every extra call.
- **Re-runs.** Stages are idempotent, so `./run.sh` after a failure resumes without
  repaying finished work. `--force` re-runs everything after fetch and **does** pay
  for rank and write again. `--refetch` also re-pulls sources.
- **Several attempts in a day.** Daily token totals in `runs` include every attempt
  (see Monitoring).

## 2. The knobs

### Models (the biggest lever)

| Variable | Used by | Guidance |
|---|---|---|
| `RANKER_MODEL` | rank | Runs many items through a fixed rubric and emits structured scores. The README says small and cheap is fine. |
| `WRITER_MODEL` | write | Produces the reader-facing prose; the README says quality matters more here. Fewer tokens flow through it, so spending more is cheaper than doing so on the ranker. |

The usual trade is a cheap fast ranker and a better writer. Changing the ranker
model changes scoring behaviour, not just cost: check a few days of output
(`ranked.json`, featured counts) after a change. Both default to `gpt-4o-mini` if
unset, which is a code default, not a recommendation.

### Limits that bound a call

| Setting | Default | Effect |
|---|---|---|
| `RANKER_MAX_TOKENS` | 32000 | Cap on a ranker response. Stops degenerate repetition loops from burning the full output budget. Lowering it below a section's honest need causes truncation, a retry and then an unscored fallback. |
| `WRITER_MAX_TOKENS` | 16000 | Same for the writer. |
| `RANKER_TIMEOUT_S` | 1800 | Per-call timeout. Does not change cost on healthy runs; lowering it only makes slow endpoints fail sooner. SPEC notes 15 minutes was too tight on chatty news days. |
| `WRITER_TIMEOUT_S` | 1200 | Same for the writer. |
| `LLM_RETRY_NO_CACHE` | `1` | On a retry, ask a LiteLLM-style proxy not to serve the cached bad reply. |
| `LLM_RESPONSE_FORMAT` | `json_schema` | Affects reliability, not price; a mode the provider rejects wastes attempts. |

### Volume controls (cheapest place to save)

Tokens scale with how many items reach the ranker, so cut upstream of the LLM.

| Where | Setting | Effect |
|---|---|---|
| `edition.yaml` | `keywords:` | The keyword gate drops unrelated items before ranking (SPEC estimates the prefilter drops 70 to 80%). Tighter keywords mean fewer ranked items, but also a risk of missing good ones. |
| `sources.yaml` | remove or narrow a source; `max_results` (arXiv), `limit` (Reddit), `max_items` (HTML), `min_points` / `hours_back` (HN) | Fewer inputs. |
| `sources.yaml` | `recency_days` | A shorter window means fewer eligible items. |
| `sources.yaml` | `keyword_gate_bypass: true` | Does the opposite: sends every item from that source to the ranker. Use sparingly. |
| `scripts/candidates.py` | `PAPER_PRERANK_CAP` (100) | Hard cap on unscored papers per ranker call. Surplus papers wait and re-compete on a later run. This is a constant in code, not an environment setting. Lowering it shortens the longest call. |
| `scripts/prefilter.py` | `PAPER_POOL_MAX_COMPETES` (7), `PAPER_POOL_MAX_AGE_DAYS` (7) | How long unfeatured papers stay in the pool. Papers already scored are cached and skip the LLM when they compete again. |

### Output-size controls

`sections:` in `edition.yaml` sets `featured_min`, `appendix_min`, `cap` and the
optional papers `burst_cap`. They mostly shape the issue, but they also size the
writer call: more featured items means more writer input and output. The burst cap
raises the paper cap on heavy days (SPEC cites up to 22 featured items), which
raises writer cost on exactly those days. Omit `burst_*` if you want a flat ceiling.

### Prompt size

The rubric is sent with every ranker call, and again for every bisected sub-call.
Long prompt fragments (`editions/<slug>/prompts/rank.*.md`) add to every call. Trimming `rank.*.md` fragments saves a small amount on every call.

### What is not available

`BUDGET_USD` is documented in the README environment table but is **not enforced**; nothing in the
pipeline stops a run at a cost limit. Likewise `runs.cost_usd` is never written. Both
are called out in README and SPEC. Any spending cap has to come from your provider.

## 3. Choosing a trade-off

| Goal | Change | Cost of the change |
|---|---|---|
| Lower cost, same coverage | Cheaper `RANKER_MODEL` | Scores may be noisier; review a week of output. |
| Lower cost | Tighter `keywords:`, fewer sources, smaller per-source limits | Possible missed items. |
| Lower cost | Lower `PAPER_PRERANK_CAP` | Papers wait longer before being scored. |
| Shorter wall time | Lower `PAPER_PRERANK_CAP`; fewer sources; a faster ranker model | As above. |
| Higher quality | Better `WRITER_MODEL` | Modest cost increase, since few tokens go through it. |
| Higher quality | Better `RANKER_MODEL` | Larger cost increase, since it sees every candidate. |
| Fewer failures | Leave `*_MAX_TOKENS` and `*_TIMEOUT_S` at defaults | None. |

Change one thing at a time and compare against the prior days' `runs` rows.

### Example: a lean configuration

A starting point for an operator who wants to minimise spend (not a tested
benchmark; measure it on your data):

```bash
# .env
RANKER_MODEL=<small, inexpensive model your endpoint offers>
WRITER_MODEL=<mid-tier model>
```

```yaml
# editions/<slug>/edition.yaml
sections:
  papers: {featured_min: 7, appendix_min: 6, cap: 4}
  news:   {featured_min: 7, appendix_min: 5, cap: 4}
  blogs:  {featured_min: 7, appendix_min: 5, cap: 4}
```

plus fewer sources, `max_results: 20` on arXiv queries, and no `burst_*` keys. Then
compare the first week's tokens against your current numbers.

## 4. Estimating cost before you run

1. Do a dry run with no LLM: `uv run scripts/check_edition.py <slug>` prints, per
   source, how many items survive the recency and keyword gates. The sum is roughly
   the number of items the ranker will see (papers beyond `PAPER_PRERANK_CAP` are
   deferred).
2. Estimate tokens per ranker section as
   `rubric_tokens + items x (avg_item_tokens)` in, and `items x ~80` out, where
   `avg_item_tokens` is dominated by `raw_text` (up to 4000 characters, about 1000
   tokens). The 80-token output figure is an approximation; check it against real
   `completion_tokens` from the usage log.
3. Add the writer call: input is the featured items (trimmed `raw_text`) plus appendix
   metadata plus the rubric.
4. Multiply by your provider's per-token prices, and by the number of editions.
5. Replace the estimate with real numbers after the first day (Monitoring below).

## 5. Monitoring cost and performance

Everything is recorded per edition in `editions/<slug>/state.db`, on the `content`
branch (see [metrics/README.md](metrics/README.md)).

**Per day**:

```bash
sqlite3 editions/<slug>/state.db \
  "SELECT date, items_candidate, items_featured, duration_seconds,
          tokens_in, tokens_out, notes
   FROM runs ORDER BY date DESC LIMIT 14"
```

`notes` carries `llm_calls=` and `models=` for the day. `cost_usd` is empty.

**Per attempt**, including failures and retries by `run.sh`:

```bash
sqlite3 editions/<slug>/state.db \
  "SELECT date, attempt, outcome, failed_stage, llm_calls,
          tokens_in, tokens_out, tokens_by_model
   FROM run_attempts ORDER BY date DESC, attempt DESC LIMIT 20"
```

`tokens_by_model` is `<model>:<in>/<out>` pairs, which is what you multiply by
per-model prices. A `?` means the endpoint did not report usage for that call.

**Per LLM call**: `logs/<slug>/usage-<date>.jsonl` has one JSON line per response
(`attempt`, `label`, `model`, `prompt_tokens`, `completion_tokens`). Labels name the
section, so you can see which call is expensive, and a repeated label shows a retry
or bisect.

**Wall time by stage**: `logs/<slug>/run-<date>.log` has a start and ok line for each
stage with timestamps.

Signals worth watching:

- `tokens_in` rising while `items_featured` stays flat: more items reaching the
  ranker; check sources and keywords.
- `llm_calls` above the normal 4 per day (3 ranker, 1 writer; fewer when a section
  is empty or papers are all prescored): retries or bisecting.
- `papers_prerank_cut` on `runs`: the pre-rank cap is deferring papers.
- A `run_attempts` row with `outcome = failed` followed by an `ok`: the day was paid
  for more than once.
