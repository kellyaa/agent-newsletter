# AI Agents Daily Newsletter — Design Spec v0.1

A locally-run, Claude-Code-orchestrated pipeline that produces a daily Markdown newsletter covering the practical state of the art in AI agents, targeted at senior engineers and architects.

## Goals & Non-Goals

**Goals**
- One high-signal digest per day (~8-12 featured items + appendix of uncertain items).
- Zero babysitting once tuned: runs unattended, fails loudly when it does fail.
- Editorial voice tuned to "how do I *build/run* agents," not generic AI hype.
- Reproducible: given the same inputs, the same outputs (modulo LLM nondeterminism).

**Non-Goals (v1)**
- Multi-subscriber email delivery.
- A web UI beyond GitHub Pages rendering.
- Real-time/breaking news — a one-day lag is fine.
- Perfect recall. Better to miss a good item than include 10 mediocre ones.

## Audience & Editorial Rubric

Reader profile: senior engineer / staff+ / architect who already knows what an LLM is, writes production code, and cares about:
1. **Building agents** — frameworks, patterns, tool use, memory, planning, evals, cost/latency.
2. **Agents for software work** — code generation, review, refactoring, DevOps, incident response, SRE.
3. **Running agents in production** — observability, safety, failure modes, guardrails, deployment.
4. **State of the art** — papers with concrete techniques, not position pieces.
5. **Using agents for tech work** - Agents for any other non-software development activities that applies to a technology professional - such as managing a fleet of servers, managing a Kubernetes cluster, etc.

Explicitly down-weighted: consumer AI hype, funding announcements, VC takes, "prompt engineering tips," generic ML news without an agent angle, model benchmarks without methodological interest.

## High-Level Architecture

```
  ┌──────────────┐
  │ launchd cron │  (daily, ~06:00 local)
  └──────┬───────┘
         │ invokes
         ▼
  ┌──────────────────────────────────────────────┐
  │  run.sh                                      │
  │    1. python fetch.py      (deterministic)   │
  │    2. python prefilter.py  (deterministic)   │
  │    3. claude -p "rank..."  (LLM, via CC)     │
  │    4. claude -p "summarize & write..." (CC)  │
  │    5. python publish.py    (deterministic)   │
  │    6. git add / commit / push                │
  └──────────────────────────────────────────────┘
         │              │
         ▼              ▼
    state.db       newsletters/YYYY-MM-DD.md
    (SQLite,       + index.md (for Pages)
     committed)
```

Claude Code is invoked twice in headless mode (`claude -p`) for the two tasks that need judgment: ranking and summarizing. Everything else is plain Python.

## Components

### 1. Scheduler — `launchd`

A user-level `~/Library/LaunchAgents/com.kelly.agent-newsletter.plist` running `run.sh` daily at **07:00 local**. Logs stdout/stderr to `logs/YYYY-MM-DD.log`. If the Mac is asleep at the scheduled time, launchd runs it on wake (unlike cron).

Failure mode: `run.sh` exits nonzero → a macOS notification fires (via `osascript`) and the per-day log at `logs/run-YYYY-MM-DD.log` captures the full output. No silent failures.

### 2. Collectors — `fetch.py`

Pure Python, no LLM calls. Pulls from:

| Source type   | Mechanism                          | Examples                                                                                    |
|---------------|------------------------------------|---------------------------------------------------------------------------------------------|
| RSS/Atom      | `feedparser`                       | Simon Willison, Latent Space, Anthropic blog, LangChain, HuggingFace, Sebastian Raschka... |
| arXiv         | arXiv API (Atom)                   | Queries on `cs.AI`, `cs.CL`, `cs.SE` with agent-related terms, last 48h                    |
| Semantic Schl | REST API                           | Backfill citation counts on arxiv hits                                                     |
| HN            | Algolia API                        | `query=agent OR LLM`, min points threshold, last 48h                                       |
| Reddit        | `.json` endpoints                  | `r/LocalLLaMA`, `r/MachineLearning`, min upvotes                                           |
| GitHub        | `gh api` via subprocess            | Trending repos tagged `ai-agents`/`llm-agent`; releases from a watchlist                   |
| HF Daily      | Scrape HTML of daily-papers page   | Pre-curated academic signal                                                                |

Feed list lives in `sources.yaml` — easy to edit without touching code.

**Output:** rows inserted into `state.db` table `items`:
```
id (sha256 of canonical URL), source, url, title, author, published_at,
raw_text (abstract/summary/first N chars), fetched_at, status
```
`status` starts as `new`. Inserts use `INSERT OR IGNORE` on id, so re-fetching is a no-op.

### 3. Pre-filter — `prefilter.py`

Cheap deterministic triage before we spend LLM tokens. Drops ~70-80% of items.

Rules (all configurable in `scripts/prefilter.py`):
- **Recency** (per source family): RSS = 30d, GitHub releases = 14d, arXiv = 7d, HN/Reddit = 3d. RSS is intentionally wide because practitioner blogs publish weekly-or-monthly; the cross-day dedup layer prevents re-featuring already-seen items.
- **Keyword gate:** title or abstract must contain at least one term from a tuned list (`agent`, `agentic`, `tool use`, `mcp`, `LLM`, `RAG`, `eval`, `tool-calling`, `multi-agent`, etc.). **Trusted RSS sources bypass this gate** (see `KEYWORD_GATE_BYPASS` in `prefilter.py`) — a curated set of low-volume practitioner blogs whose every post is plausibly relevant; the LLM ranker scores them downstream.
- **Source reputation floor:** HN items need >50 points; Reddit >100 upvotes; arXiv papers need an abstract (not just title).
- **Dedup across time:** skip any item whose `id` is already `status >= ranked` in the DB. (See Dedup section.)
- **Near-dup within run:** normalize titles (lowercase, strip punctuation), drop items whose title has >0.85 Jaccard similarity to another higher-ranked-source item in this batch. Prefer arxiv > HN > Reddit when collapsing.

Survivors get `status = 'candidate'`.

### 4. Ranker — Claude Code (headless)

```bash
claude -p "$(cat prompts/rank.md)" --output-format=json > ranked.json
```

`prompts/rank.md` instructs CC to:
1. Read `candidates.json`, which contains items already grouped by section (`papers`, `news`, `blogs`) by `prefilter.py`.
2. Score each item 1-10 against the rubric below, applying the **section-specific** axis emphasis and threshold.
3. Return a JSON array: `[{id, score, tags, one_line_why}]`. Section is not emitted — it was set deterministically upstream.

The ranker is invoked with all three buckets in a single call so it has cross-section context (e.g., it can see that today is paper-heavy and hold the bar higher), but it scores each item against its own section's rubric.

**Ranking rubric** (codified in the prompt):

Items are scored and thresholded **independently within each section** (`papers`, `news`, `blogs`). The four axes are the same, but what each axis *means* differs by section, and the featured/appendix thresholds are tuned per section so we don't end up with 12 papers and zero blogs (or vice versa).

The four axes — score each 0 to its max:

- **Relevance to the "building/running agents" beat (0-4).** Item directly advances a reader's ability to build, evaluate, deploy, or debug an agent. Framework deep-dives, eval methodologies, tool-use patterns, memory architectures, production postmortems → high. Generic "AI will change everything" → zero.
- **Technical depth (0-3).** There is a concrete mechanism, benchmark, code, or engineering tradeoff the reader can learn from. Abstract/title must contain specifics. Marketing pages and thought-leadership → zero.
- **Novelty (0-2).** Meaningfully new vs. what a well-read practitioner already knows. A 12th RAG survey is not novel; a new failure mode, pattern, or surprising result is.
- **Source credibility (0-1).** Known practitioner/lab/peer-reviewed = 1. Random Medium post = 0. (Override down if the content is bad.)

Total 0-10. **The score is then interpreted within the item's section, with section-specific thresholds and section-specific reading of each axis:**

| Section | Featured threshold | Appendix range | Drop below | Per-axis emphasis |
|---|---|---|---|---|
| **Papers** | ≥7 | 5-6 | <5 | Novelty and depth weighted strongly. Surveys and incremental benchmarks need ≥8 to feature. Demand a concrete mechanism, ablation, or benchmark — not just a claim. |
| **News** | ≥6 | 4-5 | <4 | Relevance and credibility lead. Concrete (version numbers, deprecation dates, breaking changes, named incidents) beats vague. A "we launched X" with no engineering substance caps at 5. |
| **Blogs** | ≥6 | 4-5 | <4 | Depth and novelty lead. A practitioner's earned take on a real problem can hit ≥7 even without academic rigor. Listicles and "5 things I learned" cap at 4. |

**Rationale:** papers need a higher bar because they're often dense and specialized — featuring a mediocre paper wastes the reader's time more than featuring a mediocre release note. News and blogs run on a 6-threshold because timeliness and practitioner perspective give them value at a lower depth-bar than academic work.

**Per-section caps for the daily output** (to keep the newsletter readable):
- Papers: max 5 featured.
- News: max 6 featured.
- Blogs: max 6 featured.

If more items clear the threshold than the cap allows, take the top-N by score within that section; the remainder spill into the appendix.

Also emit:
- **Tags** from a closed vocabulary: `frameworks`, `tool-use`, `memory`, `planning`, `evals`, `code-agents`, `devops-agents`, `observability`, `safety`, `research`, `infra`, `multi-agent`, `cost-latency`. Tags are now informational (used for the ranker's own reasoning, the topics_covered table, and possible future facets) — they no longer drive section grouping.
- **Section assignment**: `papers` | `news` | `blogs`. See "Section assignment" under the Writer step for the default rules and override criteria.

Writes scores, tags, and section back to `state.db`, sets `status = 'ranked'`.

### 5. Summarizer / Writer — Claude Code (headless)

```bash
claude -p "$(cat prompts/write.md)" > newsletters/$(date +%F).md
```

The prompt gives CC:
- The top ~8-12 featured items (id, url, title, abstract, source, tags, one_line_why).
- The appendix list (title + url only).
- A style guide (see below) and yesterday's newsletter for continuity/tone calibration.

CC writes:
1. **Header** — date, 1-2 sentence "today's theme" if one emerges, else skip.
2. **Featured items, grouped into three top-level sections** in this fixed order:
   1. **Papers** — academic preprints and peer-reviewed work. Items where `source` starts with `arxiv:` or `hf-daily:`. Lead with the contribution, not the title's vocabulary. If the methodology is weak (no baseline, n=1, cherry-picked task), say so.
   2. **News** — releases, launches, incidents, deprecations, vendor announcements. Items where `source` starts with `gh:` (releases), or content from RSS/HN/Reddit that is announcement-shaped (release notes, "we launched X", incident postmortems). Prioritize items with concrete version numbers, deprecation dates, or breaking changes.
   3. **Blogs** — practitioner writeups, deep dives, tutorials, opinion. Items from RSS feeds (Simon Willison, Latent Space, Interconnects, etc.) and HN/Reddit discussions of practitioner posts. This is where the editorial voice should be sharpest.

   Each item within a section:
   - Title as link.
   - Source and author.
   - 2-4 sentence summary with a "why it matters" framing.
   - Optional "⚠ open question" or "💡 takeaway" line when warranted.

   If a section has zero featured items on a given day, omit the section header — don't print "## Papers" with nothing under it.

3. **Appendix** — single bulleted list `[Title](url) — source` for uncertain items, regardless of section. No summaries.
4. **Footer** — run metadata (items considered, items featured per section, LLM cost if available).

**Section assignment** is done deterministically in `prefilter.py` based on the source family — *before* the ranker runs. The ranker then receives three already-bucketed candidate lists and ranks each independently. No LLM judgment on which bucket an item belongs to; the source decides.

Source-family → section default mapping (in `scripts/prefilter.py` `SECTION_BY_FAMILY`):

| Source family | Default section |
|---|---|
| `arxiv:*`, `hf-daily:*` | `papers` |
| `gh:*` (release watchlist) | `news` |
| `hn:*`, `reddit:*` | `news` |
| `rss:*` | `blogs` |

**Per-source override.** Any entry in `sources.yaml` may add a `section:` field that overrides the family default for items emitted by that source. Allowed values: `papers` | `news` | `blogs`. Invalid values are logged and ignored, falling back to the family default. The override is stamped onto each item by `fetch.py` and persisted as `items.section_override` in `state.db`; `prefilter.py` prefers the override over the family default when present.

Use the override when a source's content is consistently shaped differently from its family (e.g., a vendor RSS feed that publishes only release notes → `section: news`, or a Substack of practitioner deep-dives whose family-default would land it in `news` → `section: blogs`).

Rationale and tradeoffs:
- Family default + per-source override is a coarse two-level mapping. An RSS post that's really a release announcement, when published from a feed that's mostly blog content, will still end up in `blogs`. We accept that — the override is per-source, not per-item.
- It removes a fuzzy LLM decision that's easy to get wrong.
- The reader can always click through; the section is a rough nav aid, not a content guarantee.
- We do **not** route HN/Reddit submissions whose linked URL is on arxiv.org into `papers`. The dedup layer already collapses arxiv-on-HN to the canonical arxiv item; what survives in HN/Reddit is the discussion-shaped content, which fits `news` better.

Style guide (embedded in the prompt):
- Assume the reader knows what a transformer is.
- No hedging fluff ("it's worth noting that..."). No "dive into."
- Cite specific mechanisms/numbers from the source, not vague gestures.
- **Opinionated, but earned.** Skepticism and judgment are welcome — but only when grounded in the actual content of the source. Don't manufacture a contrarian take for flavor; if the work is solid, say so plainly. If a claim is overreaching, name the specific weakness (small sample size, cherry-picked benchmark, no ablation, etc.).
- Keep per-item summary under 80 words.

### 6. Publisher — `publish.py`

- Marks featured/appendix items `status = 'published'` in DB.
- Regenerates `index.md` (reverse-chronological list of all newsletters with first-line excerpt).
- Updates `feed.xml` (Atom) if we add it later.
- `git add newsletters/ state.db index.md && git commit && git push`.
- GitHub Pages serves from the repo — no build step needed (Jekyll renders MD).

## Dedup Strategy

The single most important correctness property. Three layers:

**Layer 1: URL-level dedup (trivial).**
- Canonicalize: lowercase host, strip `utm_*`/`ref=` params, strip trailing slash, resolve arxiv abs/pdf variants to the abs form.
- Primary key in `items` is `sha256(canonical_url)`. `INSERT OR IGNORE`.

**Layer 2: Cross-source dedup within a run.**
- arXiv paper posted to HN the same day: one canonical form (prefer arxiv). Detect by checking if an HN item's URL points at arxiv.org/ resolves to one.
- Simon Willison linking to a paper we already have: keep Simon's commentary as context, don't feature the paper twice.
- Implemented in prefilter via title similarity + URL-target resolution.

**Layer 3: Cross-day dedup (the one that bites everyone).**
- The `items` table tracks `status`. Once `status >= 'ranked'`, an item is "known" — it will never be re-ranked or re-summarized even if it's still trending.
- **But:** if an item was appendix-only yesterday and is still buzzing with meaningful new discussion today, we want the option to promote it. Mechanism: appendix items keep `status = 'appendix'`, and prefilter allows them back in for one retry (capped at 2 total appearances total). Featured items are sealed.
- Topic-level dedup: a weekly-rolling "topics covered" list (e.g., "DSPy 2.5 release", "Anthropic's SWE-Bench result") is passed into the ranker prompt so it can down-weight items that are just the 4th take on the same news.

**Tests:** a small suite that replays a fixture day twice and asserts idempotency.

## Data Model (SQLite)

```sql
CREATE TABLE items (
  id TEXT PRIMARY KEY,            -- sha256(canonical_url)
  source TEXT NOT NULL,           -- 'arxiv', 'hn', 'rss:simonw', ...
  url TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT,
  published_at TEXT,              -- ISO8601
  fetched_at TEXT NOT NULL,
  raw_text TEXT,                  -- abstract / summary / excerpt
  score INTEGER,                  -- 0-10, null if not ranked
  tags TEXT,                      -- JSON array
  section TEXT,                   -- 'papers' | 'news' | 'blogs', set by prefilter
  section_override TEXT,          -- per-source override from sources.yaml; null otherwise
  why TEXT,                       -- ranker's one-line rationale
  status TEXT NOT NULL,           -- new|candidate|ranked|featured|appendix|published|dropped
  first_seen_date TEXT NOT NULL,
  last_seen_date TEXT NOT NULL,
  appearances INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE runs (
  date TEXT PRIMARY KEY,
  items_fetched INTEGER,
  items_candidate INTEGER,
  items_featured INTEGER,
  items_papers INTEGER,           -- featured count by section
  items_news INTEGER,
  items_blogs INTEGER,
  duration_seconds INTEGER,
  tokens_in INTEGER,              -- scaffold for cost tracking
  tokens_out INTEGER,
  cost_usd REAL,
  notes TEXT
);

CREATE TABLE topics_covered (  -- for cross-day topic dedup
  topic TEXT NOT NULL,           -- short slug, emitted by ranker
  date TEXT NOT NULL,
  item_id TEXT NOT NULL
);
```

## Repo Layout

```
incubation/
├── SPEC.md                    ← this file
├── README.md
├── run.sh                     ← entry point (pipeline)
├── watchdog.sh                ← stale-commit checker (separate launchd job)
├── sources.yaml               ← feed list, tunable
├── prompts/
│   ├── rank.md
│   └── write.md
├── scripts/
│   ├── fetch.py
│   ├── prefilter.py
│   ├── publish.py
│   └── cost.py                ← parse CC session output → runs.cost_usd
├── newsletters/
│   ├── 2026-05-13.md
│   └── ...
├── state.db                   ← committed
├── index.md                   ← for GitHub Pages
├── logs/
└── tests/
    └── test_dedup.py
```

## Failure Modes & Mitigations

| Failure                           | Mitigation                                                   |
|-----------------------------------|--------------------------------------------------------------|
| A feed is down                    | Per-source try/except; log and continue; skip source for day |
| CC session times out / crashes    | Stage exits nonzero; `run.sh` aborts; macOS notification fires; re-run is idempotent |
| CC produces malformed JSON        | `rank.py` validates with jsonschema; on fail, retry with stricter prompt, then fall back to score-by-source-reputation |
| CC hallucinates a URL             | Summarizer prompt only sees items+URLs from the DB; publish.py verifies every linked URL in output exists in DB |
| SQLite merge conflict (unlikely)  | Single writer (your Mac); but add `busy_timeout` anyway      |
| Newsletter is empty / too short   | Gate in publish.py: if file is below MIN_FILE_SIZE_BYTES or 0 featured + 0 appendix, refuse to publish (nonzero exit) |
| Cost runaway                      | Per-run cost recorded in `runs.cost_usd`; `BUDGET_USD` env var scaffolded but not enforced in v1 (see Cost Budget) |
| No commit in >36h                 | Separate "watchdog" launchd job runs hourly; if `git log -1` is stale, fires a macOS notification |

## Failure Notifications

macOS notifications via `osascript`. No email, no SMTP, no third-party service:

- `run.sh` wraps the pipeline; on nonzero exit, the wrapper invokes `osascript -e 'display notification ...'` with the failed stage and the path to the day's log.
- A separate `watchdog.sh` (its own launchd plist, runs hourly) checks the timestamp of the most recent commit on `main`. If >36h stale, it fires a notification with "newsletter pipeline appears stuck — see logs/".
- The full output of every run lands in `logs/run-YYYY-MM-DD.log` for postmortem (already wired up via `tee` in `run.sh`).
- Once the site is deployed to GitHub Pages, the Pages-build-failure email GitHub sends on broken deploys is a free additional signal.

## Cost Budget

v1 records cost; v2 enforces it. Scaffolding now so we don't have to retrofit:

- `runs` table includes `cost_usd REAL` and `tokens_in`, `tokens_out` columns.
- `claude -p` invocations capture cost from CC's session output (or, if not directly available, estimate from tokens). Helper `scripts/cost.py` parses and writes to the DB.
- `BUDGET_USD` env var is read at the top of `run.sh` and logged. **Not enforced** in v1 — just observed.
- A weekly summary line in the log: "last 7 days: $X.YY". Once we have ~30 days of data we'll set a real cap.

## Iteration Plan

- **v0 (day 1):** fetch.py + prefilter.py produce `candidates.json`. No LLM yet. Eyeball the output.
- **v0.5 (day 2):** wire in the ranker, hand-review scores for a week, tune the rubric.
- **v1 (week 1):** add the writer, publish to repo. First real newsletter.
- **v1.1:** GitHub Pages + Atom feed.
- **v1.2:** topic-level dedup with `topics_covered`.
- **v2:** "this week in agents" weekly rollup; tag-filtered indexes; RSS feed for the site itself.

## Decisions (resolved)

- **Run time:** 07:00 local, daily.
- **Voice:** opinionated and willing to be skeptical, but only on the basis of the actual content. No contrarianism for its own sake; if a piece is solid, say so plainly. Skepticism must cite specifics (sample size, missing ablation, cherry-picked benchmark).
- **Failure notifications:** macOS notifications via `osascript` from `run.sh` and `watchdog.sh`. No email, no third-party service. Run logs in `logs/`.
- **Cost cap:** not enforced in v1, but scaffolded — `runs.cost_usd` recorded every run, `BUDGET_USD` env var read but not gated on.
- **Repo:** public on GitHub. Enables free GitHub Pages hosting for the published newsletter.

## Initial Source List (`sources.yaml` seed)

The operator-supplied seed list, to be fleshed out with concrete feed URLs during implementation. Sources marked **(needs investigation)** don't have an obvious RSS endpoint — `fetch.py` will need either a custom adapter or we drop them.

**Newsletters / blogs (RSS or near-RSS):**
- Latent Space — deep technical dives, practitioner interviews
- Interconnects (Nathan Lambert) — research-focused, accessible
- The AI Exchange (Dan Shipper) — AI workflows analysis
- The Batch (DeepLearning.AI)
- Simon Willison's Weblog — high signal on practical LLM/agent tooling
- LangChain / LangGraph Blog
- Anthropic Engineering Blog
- OpenAI Engineering Blog
- Galileo AI Blog
- newsletter.karuparti.com
- gmicloud.ai/en/blog **(needs investigation — vendor blog, check if RSS exists)**

**Other formats:**
- Substack — generic; needs specific publication handles, not the platform itself **(needs investigation)**
**Archetype sources (not operator-listed but recommended for coverage):**
- arXiv (`cs.AI`, `cs.CL`, `cs.SE` agent-related queries)
- Hugging Face Daily Papers
- Hacker News (Algolia API, score-gated)
- Selected GitHub release watchlist (LangGraph, CrewAI, AutoGen, Claude Agent SDK, MCP servers, DSPy, etc.)

**Out of scope for v1:** Twitter/X, podcasts (no transcript pipeline), YouTube, Discord/Slack communities.

## Open Items

These remain unresolved and will be settled during implementation, not before:

1. **Resolve remaining "needs investigation" sources** above into concrete RSS URLs or drop them (Substack-the-platform, gmicloud blog).
