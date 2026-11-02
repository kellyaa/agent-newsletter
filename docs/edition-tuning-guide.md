# Edition Tuning Guide

An edition's first draft is a guess about what its reader wants. Tuning is the loop of running it, reading what came out, and adjusting the few knobs that change the result, until the issues are worth reading. This guide is the workflow. The references stay where they are:

- [edition-yaml-reference.md](edition-yaml-reference.md): every `edition.yaml` field.
- [prefilter-gates-guide.md](prefilter-gates-guide.md): recency and keyword gates.
- [ranking-rubric.md](ranking-rubric.md): what the scores mean.
- [prompt-slots-guide.md](prompt-slots-guide.md): the prompt fragments.
- [adding-feed-sources-guide.md](adding-feed-sources-guide.md): sources.
- CONTRIBUTING.md, "Adding an edition": scaffolding and going live.

## 1. What tuning changes

An item travels fetch, recency gate, keyword gate, dedup, ranker score, per-section threshold and cap, writer. Each stage has one place to tune it, and tuning the wrong stage is the usual mistake.

| Symptom | Stage to look at | Knob |
|---|---|---|
| Too few candidates; on-topic items missing | keyword gate or recency | `keywords`, source `recency_days`, `keyword_gate_bypass` |
| Many candidates but off-topic issues | keyword gate too loose, or ranker rubric | `keywords`, `rank.relevance.md`, `rank.sections.md`, `downweighted` |
| Good items scored, not featured | thresholds and caps | `sections.<name>.featured_min`, `cap` |
| One section crowds out the others | caps and per-source section | `cap`, source `section:` |
| Right items, wrong voice | writer | `write.voice.md`, `write.sections.md`, `write.theme-examples.md` |
| Noise from one source | source | remove it, narrow its query, or remove its bypass |

Rule of thumb: fix **inputs** first (sources, gates), then the **judge** (rubric), then the **thresholds**, then the **voice**. A rubric cannot rescue an empty candidate pool, and a threshold cannot fix a rubric that scores everything 5.

## 2. Before the first real run (no LLM, no cost)

```bash
uv run scripts/new_edition.py my-slug --name "My Edition" --describe "..."   # or --no-llm
uv run scripts/check_edition.py my-slug
```

`check_edition.py` lints the config (including unfilled `TODO`s, which are a warning for a draft and an error for `live`) and then fetches every source into a throwaway database and prints, per source:

```
source        fetched  recency  keyword  section
rss/simonw         30       22       22  blogs:22
hn/hn-agent        40       40        9  news:9
```

Read it as a funnel. A source with `fetched = 0` or `ERROR` is broken (see [source-reliability-matrix.md](source-reliability-matrix.md)). A big drop between `recency` and `keyword` means the gate is doing the work, or is wrong. The last row is the total per section.

Targets are your judgement, not a rule: an issue needs enough candidates per section that the ranker has a choice, but not so many that the ranker call is slow and expensive. Compare against `cap` for the section: you want the post-keyword count to be several times larger than the cap, and in the dozens to low hundreds for a section with an LLM call. The `agents` edition caps papers at 5 and news and blogs at 6, so use its numbers as an order of magnitude only.

Preview the site without publishing: `SHOW_DRAFTS=1 pnpm dev` in `site/`.

## 3. The knobs, with how to use each

### 3.1 Keyword gate (`edition.yaml` `keywords`)

An item passes if **any** pattern matches its title or `raw_text` (case-insensitive; patterns are combined into one alternation). `keyword_gate_bypass: true` on a source skips the gate for that source only.

Diagnose with the funnel:

- **Too strict**: `keyword` is a small fraction of `recency` on sources that should be on-topic, and you can name on-topic titles that were lost. Add a broader pattern. The `engineering-with-ai` edition's `edition.yaml` documents exactly this: bare `\bai\b` and `\bagents?\b` were added after on-topic posts with short RSS summaries failed the gate.
- **Too loose**: nearly everything passes, including items you would never feature. Replace a bare common word with a more specific phrase. The gate is a cheap recall filter; relevance is the ranker's job, so err towards recall and let the ranker discard.

Use `\b` word boundaries (without them `rag` matches "storage"). Quote YAML regexes in single quotes so backslashes survive. Change a few patterns at a time and rerun `check_edition.py` so you can see which change moved the numbers.

Use `keyword_gate_bypass` only for low-volume, hand-picked blogs where nearly every post is relevant. Never on HN, Reddit or arXiv.

### 3.2 Recency (`sources.yaml` `recency_days`)

Default windows are by family (`RECENCY_DAYS` in `scripts/prefilter.py`: arXiv 7, RSS 30, HN and Reddit 3, GitHub 14, html 30, anything else 3). A source that publishes every few weeks may need a longer one. Setting it on a high-volume source mostly adds old items.

### 3.3 Ranking rubric emphasis (`prompts/rank.*.md`)

The ranker scores each item (1-10; see [ranking-rubric.md](ranking-rubric.md)) against the rubric rendered from `prompts/rank.md` plus your fragments:

- `rank.relevance.md`: what scores high and what scores zero for this subject. This is the most influential file. Be concrete, with examples of both extremes, as `editions/agents/prompts/rank.relevance.md` does.
- `rank.sections.md`: what "strong" means for papers, news and blogs. Use this to change how hard a section is graded without touching thresholds (for example "listicles cap at 4").
- `rank.reader.md` and `edition.yaml` `reader_profile` / `downweighted`: who the reader is and what they do not want.

Calibrate against real output: pick five items the ranker scored 7+ and five it scored 4-6, read their `why`, and ask whether you agree. Rewrite the fragment to fix the disagreement you see most. Rewrite one fragment per iteration.

Changing any rank fragment or `prompts/rank.md` changes the **rubric hash**. The next prefilter run then clears cached paper scores so papers are re-scored under the new rubric. That is correct, and it means a rubric change costs one more papers ranker call, so batch rubric edits rather than tweaking one word at a time.

### 3.4 Thresholds and caps (`edition.yaml` `sections`)

Per section: `featured_min`, `appendix_min`, `cap` (and for papers the optional burst settings). An item scoring at least `featured_min` is featured until `cap` is reached; items scoring at least `appendix_min` go to the appendix; the rest are dropped. `appendix_min` may not exceed `featured_min`.

- If good items are being dropped, lower `appendix_min` before lowering `featured_min`.
- If the featured list is padded with mediocre items, raise `featured_min` or lower `cap`. An empty section on a quiet day is better than filler.
- Changing thresholds and caps needs no rank rerun logic of its own, but note that in the current code papers that clear `featured_min` and lose the cap stay in a pool and compete again on later days (bounded by the pool constants in `scripts/prefilter.py`), so a cap change is felt over several days for papers and immediately for news and blogs.

### 3.5 Per-source section (`sources.yaml` `section:`)

Family defaults: arXiv to papers, RSS to blogs, HN, Reddit, html and GitHub to news. Override with `section: papers|news|blogs` when a source belongs elsewhere, for example a vendor blog that is really release notes (`section: news`).

### 3.6 Writer voice (`prompts/write.*.md`)

`write.voice.md` (do / do not lists, length cap), `write.sections.md` and `write.theme-examples.md` (one good and three bad examples of the issue's theme line). You can test a new voice on a past day without re-ranking:

```bash
EDITION=<slug> uv run python scripts/replay_writer.py YYYY-MM-DD
```

It re-runs the writer against that day's featured items and writes the raw output to `logs/` without touching `state.db` or the issue file. It does cost one writer call.

### 3.7 Source curation

Add sources slowly and remove them on evidence. After a few days, count where featured items come from:

```bash
sqlite3 .worktrees/content/editions/<slug>/state.db \
  "SELECT source, COUNT(*) AS seen,
          SUM(status IN ('featured','appendix','published')) AS kept,
          SUM(status = 'published') AS published
   FROM items GROUP BY source ORDER BY seen DESC"
```

A source that supplies many candidates and almost nothing that is kept is noise: remove it or narrow its query. A source with few candidates and a high keep rate is worth protecting (consider a longer `recency_days`). `status = 'published'` includes appendix items; the issue files list which were featured.

## 4. The tuning loop (3 to 5 days)

Repeat for each day the draft runs (drafts run in the daily job and are hidden on the site unless `SHOW_DRAFTS=1`):

1. **Read the issue** (`.worktrees/content/editions/<slug>/issues/YYYY-MM-DD.md`). Mark each featured item: belongs / does not belong. Skim the appendix for items that should have been featured.
2. **Name the failure** using the table in section 1. Pick the one that affects the most items.
3. **Make one change** to the stage that table names. One change per iteration, or you cannot tell what helped.
4. **Check without spending**: `uv run scripts/check_edition.py <slug>` for gate and source changes. For a rubric or voice change there is no free check; use `replay_writer.py` for voice, and the next daily run for the rubric.
5. **Replay today if needed**: `./run.sh --force --edition <slug>` resets today's post-fetch state and re-runs every stage after fetch (spends LLM tokens; fetched data is kept).
6. **Record** what you changed and what moved, in a scratch note. You will need it for the PR.

Stop when two consecutive days need no changes. A typical order: day 1 fix sources and gates, day 2 fix the rubric, day 3 thresholds and caps, day 4 voice, day 5 confirm.

## 5. From draft to live

Follow CONTRIBUTING.md, "Adding an edition", "Going live". In the PR that flips `status: draft` to `status: live`, give the reviewer evidence rather than a claim:

- the final `check_edition.py` table;
- how many days it ran as a draft, and the number of failed attempts (`SELECT outcome, COUNT(*) FROM run_attempts GROUP BY outcome` in the edition's `state.db`);
- for the last few days, items featured per section (`runs.items_papers/news/blogs`) and tokens (`runs.tokens_in`/`tokens_out`, so a reviewer can estimate cost; `runs.cost_usd` may be empty);
- two or three example issues, with your judgement of them;
- `REVIEW.md` deleted, and no `TODO` left (lint errors on a live edition).

Do not set `live` until the slug is final: it is in URLs and permanent.

## 6. Anti-patterns

- **Tuning thresholds to hide a bad rubric.** Lowering `featured_min` until the issue is full just features mediocre items.
- **Chasing one item.** Fix a pattern that affects many items, not the one you saw yesterday.
- **Changing several things at once**, or editing the rubric after every run (each rubric change re-scores papers).
- **Enabling `keyword_gate_bypass` to rescue recall** on a high-volume source. Broaden `keywords` instead.
- **Judging after one day.** Many sources publish weekly; use several days.
- **Gaming `check_edition.py`.** It counts items that pass the cheap gates, not items worth reading. It cannot tell you whether the ranker will like them.
- **Editing `editions/agents/`** to experiment. `tests/test_agents_edition_unchanged.py` pins that edition; copy to a new draft instead.
- **Hard-coding an edition slug in code** to get a behaviour. Behaviour comes from `editions/<slug>/` only.

## 7. Debugging low-quality output

| Observation | Check |
|---|---|
| Issue is thin | `check_edition.py` funnel; `run_attempts` for failed fetches; did `ranked` scores all land below `featured_min`? (`SELECT score, COUNT(*) FROM items WHERE status IN ('featured','appendix','dropped') GROUP BY score`) |
| Items score high but are off-topic | `rank.relevance.md` says what scores high; make the zero cases explicit |
| Ranker scores everything 4-6 | rubric lacks anchors; add concrete high and low examples; see [ranking-rubric.md](ranking-rubric.md) |
| Tags look wrong | `edition.yaml` `tags` is a closed vocabulary; the ranker's tags outside it are discarded (`rank.py`) |
| Prompt behaves oddly | read the rendered prompt in `logs/<slug>/` (written by `prompts.render`) |
| Same story twice | cross-source dedup is by title similarity; see [prefilter-gates-guide.md](prefilter-gates-guide.md) |
| Theme line is generic | `write.theme-examples.md`: add this edition's real good and bad themes |

When it still looks wrong, [debugging-guide.md](debugging-guide.md) has the stage-by-stage view.
