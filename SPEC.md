# AI Agents Daily Newsletter — Design Spec v0.1

A locally-run pipeline that produces a daily Markdown newsletter covering the practical state of the art in AI agents, targeted at senior engineers and architects.

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
  │ launchd cron │  (daily, ~07:00 local)
  └──────┬───────┘
         │ invokes
         ▼
  ┌──────────────────────────────────────────────────────┐
  │  run.sh                                              │
  │    1. uv run scripts/fetch.py      (no LLM)          │
  │    2. uv run scripts/prefilter.py  (no LLM)          │
  │    3. uv run scripts/rank.py       (LLM via llm.py)  │
  │    4. uv run scripts/write.py      (LLM via llm.py)  │
  │    5. uv run scripts/publish.py    (no LLM)          │
  │    6. git commit/push → content branch               │
  └──────────────────────────────────────────────────────┘
         │              │
         ▼              ▼
  .worktrees/     .worktrees/content/
  content/        site/src/content/issues/YYYY-MM-DD.md
  state.db        (Astro content collection; git push →
  (SQLite,         GitHub Actions → Astro build → Pages)
   content branch)
```

`scripts/llm.py` is a thin wrapper around OpenAI-compatible chat-completions HTTP calls (`openai.OpenAI.chat.completions.create()`). It replaced the original `claude -p` (Claude Code headless) invocations as of v1.2. Everything except the two LLM calls is plain Python.

**Two-branch model.** `run.sh` commits machine-authored artifacts (`state.db`, issue files) to an orphan `content` branch via a worktree at `.worktrees/content`, keeping `main`'s history clean. See README §Repository layout and §6 Publisher below for details.

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
| HN            | Algolia API                        | `query=agent OR LLM`, min points threshold, last 48h                                       |
| Reddit        | `.json` endpoints                  | `r/LocalLLaMA`, `r/MachineLearning`, min upvotes                                           |
| GitHub        | `gh api` via subprocess            | Trending repos tagged `ai-agents`/`llm-agent` (releases watchlist removed 2026-05-14 — see §Source list realities) |

**Removed / not yet implemented sources:**
- **Semantic Scholar** REST API (citation-count backfill for arXiv hits) — designed but never implemented; no adapter in `fetch.py`.
- **HF Daily** HTML scrape (https://huggingface.co/papers) — configured in `sources.yaml` but no `fetch_hf_daily()` adapter exists in `fetch.py`. Tracked in #96 for removal. The `hf-daily` section prefix in `sources.yaml` and the `hf-daily → papers` mapping in `prefilter.py` are dead code pending that cleanup.

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
- **Recency** (per source family): RSS = 30d, arXiv = 7d, HN/Reddit = 3d. RSS is intentionally wide because practitioner blogs publish weekly-or-monthly; the cross-day dedup layer prevents re-featuring already-seen items. (GitHub releases had a 14d recency window but the source was removed 2026-05-14 — see §Source list realities.)
- **Keyword gate:** title or abstract must contain at least one term from a tuned list (`agent`, `agentic`, `tool use`, `mcp`, `LLM`, `RAG`, `eval`, `tool-calling`, `multi-agent`, etc.). **Trusted RSS sources bypass this gate** (see `KEYWORD_GATE_BYPASS` in `prefilter.py`) — a curated set of low-volume practitioner blogs whose every post is plausibly relevant; the LLM ranker scores them downstream.
- **Source reputation floor:** HN items need ≥40 points (most HN sources; the `hn-mcp` query uses ≥30 — see `sources.yaml`); Reddit ≥100 upvotes; arXiv papers need an abstract (not just title).
- **Dedup across time:** skip any item whose `id` is already at a terminal status (`featured`, `published`, `dropped`) or in the papers pool (`candidate` with a score). Only items with `status = 'new'` or `status = 'appendix'` (with `appearances < limit`) are eligible to re-enter. (See Dedup section. Note: `ranked` is never a real status value — `rank.py` transitions directly to `featured`/`appendix`/`dropped`/`candidate`.)
- **Near-dup within run:** normalize titles (lowercase, strip punctuation), drop items whose title has >0.85 Jaccard similarity to another higher-ranked-source item in this batch. Prefer arxiv > HN > Reddit when collapsing.

Survivors get `status = 'candidate'`.

### 4. Ranker — `rank.py` (OpenAI-compatible LLM)

`rank.py` makes one OpenAI-compatible chat-completions call per section (papers / news / blogs) via `scripts/llm.py`. The model and endpoint are configured via `RANKER_MODEL` and `LLM_BASE_URL` in `.env`.

`prompts/rank.md` instructs the ranker to:
1. Receive candidate items grouped by section (`papers`, `news`, `blogs`), loaded from `state.db` by `scripts/candidates.py` via `load_candidates_from_db()`.
2. Score each item 1-10 against the rubric below, applying the **section-specific** axis emphasis and threshold.
3. Return a JSON array: `[{id, score, tags, one_line_why}]`. Section is not emitted — it was set deterministically upstream.

`prefilter.py` also writes a `candidates.json` debug artifact (same content, file format) for manual inspection, but `rank.py` reads from `state.db` directly via `candidates.py` — it does not depend on the file.

The ranker makes three separate calls — one per section (`papers`, `news`, `blogs`) — so each section's candidates are scored in isolation against that section's rubric. (A single 150-item call risks timeouts and quality degradation; see §LLM API lessons.)

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
- Papers: max 5 featured by default; **burst cap of 10** kicks in on heavy days (when today's count of score-10 papers reaches 10 — see `effective_cap` in `scripts/rank.py`).
- News: max 6 featured.
- Blogs: max 6 featured.

If more items clear the threshold than the cap allows, take the top-N by score within that section; the remainder spill into the appendix.

**Adaptive papers cap (deployed 2026-06-09).** Motivated by a simulation that found ~63% score-10 miss rate under sustained score inflation with the static cap=5. The burst trigger fires on the score-10 count specifically (not the score-7+ count the simulator used) so it activates exactly when top-quality supply is the problem. **Burn-in review pending (due ~2026-07-07, not yet completed as of 2026-07-14 — see issue #121 for tracking). To review: query `SELECT date, items_papers FROM runs ORDER BY date DESC LIMIT 30;` and check whether the burst cap of 10 fires appropriately on heavy arXiv days. If `burst_trigger_count` (currently `10`) fires too rarely or too often, tune it in `scripts/rank.py` and update this note with the review outcome.**

Also emit:
- **Tags** from a closed vocabulary: `frameworks`, `tool-use`, `memory`, `planning`, `evals`, `code-agents`, `devops-agents`, `observability`, `safety`, `research`, `infra`, `multi-agent`, `cost-latency`. Tags are now informational (used for the ranker's own reasoning and possible future facets) — they no longer drive section grouping. (The `topics_covered` table is a reserved stub for future cross-day topic dedup — see #4; it is not currently written to or read from.)
- **Section assignment**: `papers` | `news` | `blogs`. See "Section assignment" under the Writer step for the default rules and override criteria.

Writes scores, tags, and section back to `state.db`. Sets `status` to `featured`, `appendix`, `dropped`, or (for papers losing the cap) `candidate` — **`ranked` is never written as a status value**; `rank.py` transitions directly to the final disposition without an intermediate `ranked` state.

### 5. Summarizer / Writer — `write.py` (OpenAI-compatible LLM)

`write.py` makes one OpenAI-compatible chat-completions call via `scripts/llm.py` and writes the output to `CONTENT_ROOT/site/src/content/issues/YYYY-MM-DD.md` (the `content` branch worktree). The model is configured via `WRITER_MODEL` in `.env`.

The prompt gives the writer:
- The top ~8-12 featured items (id, url, title, abstract, source, tags, one_line_why).
- The appendix list (title + url only).
- A style guide (see below) and yesterday's newsletter for continuity/tone calibration.

The writer produces:
1. **Header** — date, 1-2 sentence "today's theme" if one emerges, else skip.
2. **Featured items, grouped into three top-level sections** in this fixed order:
   1. **Papers** — academic preprints and peer-reviewed work. Items where `source` starts with `arxiv:` or `hf-daily:`. Lead with the contribution, not the title's vocabulary. If the methodology is weak (no baseline, n=1, cherry-picked task), say so.
   2. **News** — releases, launches, incidents, deprecations, vendor announcements. Items from `gh:*` (trending repos), or content from RSS/HN/Reddit that is announcement-shaped (release notes, "we launched X", incident postmortems). Prioritize items with concrete version numbers, deprecation dates, or breaking changes.
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
| `gh:*` (trending repos; releases watchlist removed 2026-05-14) | `news` |
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
- Writes the issue file to `CONTENT_ROOT/site/src/content/issues/YYYY-MM-DD.md`. `CONTENT_ROOT` defaults to the `content`-branch worktree at `.worktrees/content` (set by `run.sh`).
- Does **not** generate `index.md` or `feed.xml` — Astro's build step handles navigation automatically.
- `run.sh` then commits `state.db` and `site/src/content/issues/` to the `content` branch (via the worktree) and pushes. `main` is never touched by the daily run.
- GitHub Actions `deploy.yml` triggers on push to either `main` or `content`, checks out both branches, copies `content`'s issue files into the Astro source tree, builds with Astro 5, and deploys to GitHub Pages. The Astro build is the deploy gate — a malformed issue file fails the build.

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
- The `items` table tracks `status`. Once an item reaches a terminal status (`featured`, `published`, or `dropped`) it will never be re-ranked or re-summarized even if it's still trending. (`ranked` is never written as a status — `rank.py` transitions items directly from `candidate` to `featured`/`appendix`/`dropped`, never via an intermediate `ranked` step.)
- **But:** if an item was appendix-only yesterday and is still buzzing with meaningful new discussion today, we want the option to promote it. Mechanism: appendix items keep `status = 'appendix'`, and prefilter allows them back in for one retry (capped at 2 total appearances total). Featured items are sealed.
- Topic-level dedup (**not yet implemented — see #4**): the design intent is a weekly-rolling "topics covered" list (e.g., "DSPy 2.5 release", "Anthropic's SWE-Bench result") passed into the ranker prompt so it can down-weight items that are just the 4th take on the same news. The `topics_covered` table schema exists in `db.py` as a reserved stub, but no pipeline stage currently writes to or reads from it.

**Papers multi-day candidate pool (issue #16, score-once semantics).**

arXiv supply is highly bursty (0 papers Sat/Sun; 90+ on a heavy weekday) but the daily newsletter wants ~3–5 papers every issue. The fix is to amortize supply across the week:

- Papers that pass prefilter sit in `status = 'candidate'` for up to `PAPER_POOL_MAX_AGE_DAYS = 7` days from their `published_at`, *or* until they have lost `PAPER_POOL_MAX_COMPETES = 7` competitions, whichever comes first. Prefilter ages out anything past either ceiling at the start of each run.
- The papers ranker scores each paper exactly once. After that first scoring, the score stays on the row and the LLM is not invoked for it again — the rubric in `prompts/rank.md` is absolute (0–10 against fixed thresholds), not relative to the batch, so re-scoring would be redundant cost.
- Each daily run, `candidates.py` (`load_candidates_from_db()`) provides two papers buckets to `rank.py`: `papers` (unscored newcomers, capped at `PAPER_PRERANK_CAP = 50` via a recency × keyword-density heuristic) and `papers_prescored` (everything in the pool that already has a score). `rank.py` calls the LLM only on `papers`, then merges the LLM output with `papers_prescored` and applies `featured_min`/`appendix_min`/`cap=5` against the union. `prefilter.py` also writes a `candidates.json` debug artifact with the same content for manual inspection.
- Papers with `score >= featured_min` that *lose the featured cap* on a heavy day stay at `status = 'candidate'` to re-compete the next day. Today (pre-issue-16) those papers get sealed to `appendix` and never reappear, even if they scored 9 or 10 — on a 30-paper day, the bottom 25 of the would-be-featured set are wasted. The pool flips this: a strong paper appears in the issue at most once, on whichever day it actually wins a featured slot, and is held back from the appendix until then.
- Mid-band papers (`appendix_min <= score < featured_min`) still go to `appendix` (terminal). They're not strong enough to ever win featured, so leaving them in the pool would just bloat it without ever surfacing them. Papers with `score < appendix_min` go to `dropped` (same as today).
- A per-row `times_competed` counter (incremented on each run where a paper competes and stays in the pool) caps a single paper's pool lifetime independently of wall-clock age. Featured and appendix items are sealed, so the counter is gated on `status = 'candidate'` to make the increment a no-op for them.
- The cached score is only valid against the rubric it was scored under. When `prompts/rank.md` changes, prefilter detects the new file hash and bulk-resets `score = NULL` for all papers candidates so they get re-scored on the next run. The previous hash is persisted in `.rubric_hash`.
- Cost impact: papers ranking only runs on days with new arrivals, and only against those new arrivals (typically 5–30 abstracts on weekdays, 0 on weekends — the LLM call is skipped entirely on weekends). News and blogs are unchanged.

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
  keyword_gate_bypass INTEGER NOT NULL DEFAULT 0,  -- per-source: skip prefilter keyword gate
  recency_days_override INTEGER,  -- per-source: recency window override, in days
  why TEXT,                       -- ranker's one-line rationale
  status TEXT NOT NULL,           -- new|candidate|featured|appendix|published|dropped (note: 'ranked' is never written; rank.py transitions directly to featured/appendix/dropped)
  first_seen_date TEXT NOT NULL,
  last_seen_date TEXT NOT NULL,
  appearances INTEGER NOT NULL DEFAULT 1,
  times_competed INTEGER NOT NULL DEFAULT 0  -- papers multi-day pool counter (issue #16)
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

CREATE TABLE topics_covered (  -- reserved stub for future cross-day topic dedup; not yet written to (see #4)
  topic TEXT NOT NULL,           -- short slug (design intent: emitted by ranker when implemented)
  date TEXT NOT NULL,
  item_id TEXT NOT NULL
);
```

## Repo Layout

```
.                              ← repo root (main branch)
├── SPEC.md                    ← this file
├── README.md
├── run.sh                     ← entry point (pipeline); sets up content worktree
├── watchdog.sh                ← stale-commit checker (separate launchd job)
├── sources.yaml               ← feed list, tunable
├── prompts/
│   ├── rank.md                ← ranker rubric + JSON output schema
│   └── write.md               ← writer voice/style + JSON output schema
├── scripts/
│   ├── fetch.py               ← collectors (no LLM)
│   ├── prefilter.py           ← recency + keyword + dedup gates
│   ├── candidates.py          ← shared candidate pool query (DB → grouped dict)
│   ├── rank.py                ← per-section LLM ranker (3× calls via llm.py)
│   ├── write.py               ← LLM writer (1× call via llm.py)
│   ├── publish.py             ← promote items; record runs row
│   ├── llm.py                 ← thin wrapper around OpenAI-compatible chat-completions
│   ├── db.py                  ← schema, URL canonicalization
│   ├── models.py              ← TypedDict definitions for pipeline stage boundaries; Status and Section StrEnum constants
│   ├── backfill.py            ← reconstruct missed daily runs from candidate pool
│   └── replay_writer.py       ← replay writer against past issues (prompt verification)
├── site/                      ← Astro 5 static site scaffold
│   └── src/content/issues/    ← issue files on the 'content' branch (see below)
├── .worktrees/
│   └── content/               ← orphan-branch worktree; auto-created by run.sh
│       ├── state.db           ← SQLite pipeline state (content branch only)
│       └── site/src/content/issues/YYYY-MM-DD.md
├── .github/workflows/
│   ├── deploy.yml             ← Astro build + Pages deploy (triggers on main or content push)
│   └── tests.yml              ← pytest CI
├── .rubric_hash               ← sha256 of prompts/rank.md; prefilter.py uses this to detect rubric changes and bulk-reset cached paper scores (gitignored)
├── candidates.json            ← debug artifact: candidate pool snapshot written by prefilter.py each run (gitignored)
├── ranked.json                ← debug artifact: raw LLM ranker output written by rank.py each run (gitignored)
├── launchd/                   ← macOS plists + install.sh
├── logs/                      ← per-day run logs (gitignored)
└── tests/                     ← pytest suite
```

**Note on branch layout.** `main` holds code and config (everything above). The `content` orphan branch holds only machine-authored artifacts: `state.db` and `site/src/content/issues/*.md`. `run.sh` writes to `content` via the `.worktrees/content` worktree and pushes on every daily run. See README §Repository layout for the full rationale.

## Failure Modes & Mitigations

| Failure                           | Mitigation                                                   |
|-----------------------------------|--------------------------------------------------------------|
| A feed is down                    | Per-source try/except; log and continue; skip source for day |
| LLM call times out / errors       | Stage exits nonzero; `run.sh` aborts; macOS notification fires; re-run is idempotent (completed stages are skipped) |
| LLM produces malformed JSON       | `rank.py` validates output schema; on fail, retries with stricter prompt, then falls back to score-by-source-reputation |
| LLM hallucinates a URL            | Writer LLM produces prose only — URLs are spliced from the DB by `write.py`. URL hallucination is mechanically impossible at the writer step; Astro content-schema validates frontmatter at build time as a second gate |
| SQLite merge conflict (unlikely)  | Single writer (your Mac); but add `busy_timeout` anyway      |
| Newsletter is empty / too short   | Gate in publish.py: if file is below MIN_FILE_SIZE_BYTES or 0 featured + 0 appendix, refuse to publish (nonzero exit) |
| Cost runaway                      | Per-run cost recorded in `runs.cost_usd`; `BUDGET_USD` env var scaffolded but not enforced in v1 (see Cost Budget) |
| No newsletter commit in >36h      | Separate "watchdog" launchd job runs hourly; checks main-branch HEAD for `newsletter:` commit pattern. **Current limitation:** since all pipeline commits land on the `content` branch, the watchdog always skips and never fires. Use the `runs` table to verify pipeline health. |

## Failure Notifications

macOS notifications via `osascript`. No email, no SMTP, no third-party service:

- `run.sh` wraps the pipeline; on nonzero exit, the wrapper invokes `osascript -e 'display notification ...'` with the failed stage and the path to the day's log.
- A separate `watchdog.sh` (its own launchd plist, runs hourly) checks the timestamp of the most recent `newsletter:` commit in the main-branch repo (at `REPO_ROOT`, resolved from `watchdog.sh`'s own location). If >36h stale, it fires a notification with "newsletter pipeline appears stuck — see logs/". If the HEAD commit is not a `newsletter:` commit (e.g., a code edit or merge commit), watchdog prints "skipping check". **Known limitation:** all daily pipeline commits land on the `content` branch (via `run.sh`'s `-C "$CONTENT_WORKTREE"` git ops), so main-branch HEAD is never a `newsletter:` commit. As a result, `watchdog.sh` always prints "skipping check" and never fires stale-pipeline notifications. A code fix would check the content worktree HEAD (`git -C .worktrees/content log -1 ...`) — see the issue tracker. For now, verify pipeline runs via the `runs` table: `sqlite3 .worktrees/content/state.db "SELECT date FROM runs ORDER BY date DESC LIMIT 1;"`.
- The full output of every run lands in `logs/run-YYYY-MM-DD.log` for postmortem (already wired up via `tee` in `run.sh`).
- Once the site is deployed to GitHub Pages, the Pages-build-failure email GitHub sends on broken deploys is a free additional signal.

## Cost Budget

v1 records cost; v2 enforces it. Scaffolding now so we don't have to retrofit:

- `runs` table includes `cost_usd REAL` and `tokens_in`, `tokens_out` columns.
- `llm.py` captures token usage from the OpenAI API response (`usage.prompt_tokens`, `usage.completion_tokens`) and passes it back to `rank.py` / `write.py`, which write it to the `runs` row.
- `BUDGET_USD` env var is defined as a future budget cap. **Not yet implemented in `run.sh` or enforced** in v1 — the column scaffolding is in place for when enforcement is added.
- A weekly summary line in the log: "last 7 days: $X.YY". Once we have ~30 days of data we'll set a real cap.

## Iteration Plan

- **v0 (shipped):** fetch.py + prefilter.py produce `candidates.json`. No LLM yet.
- **v0.5 (shipped):** ranker wired up, three section calls per day, JSON-schema-validated output via `--json-schema`.
- **v1 (shipped):** writer + publisher, plain-Markdown daily newsletter.
- **v1.1 — Path B Stage 1 (shipped):** visual upgrades inside the Markdown — score bars, tag chips, callouts, collapsible appendix grouped by section.
- **v1.2 — Path B Stage 2 (shipped):** SSG migration to Astro 5. The writer now emits YAML-frontmatter MD into `site/src/content/issues/`; URLs are spliced from the DB rather than written by the LLM (URL hallucination is mechanically impossible). Site deploys to GitHub Pages on push to `main`.
- **v1.3 — Unattended delivery (shipped):** `run.sh` orchestrator with `--force` and `--refetch` flags; per-stage idempotency for resume-after-failure; launchd plists for the daily pipeline + hourly watchdog; macOS notifications on failure (no email).

### Live status (as of 2026-07-11)

The pipeline is running daily. Site is live at `https://kellyaa.github.io/agent-newsletter/`. A typical run takes ~10 min wall time, produces 12-17 featured items + ~50-100 appendix items. Cost depends on the configured models; typical per-run cost is ~$0.30-1.20 per ranker section call × 3 sections, plus ~$0.30-0.50 for the writer call (see §LLM API lessons learned for details).

### Up next (unstarted)

Tracked as GitHub issues at https://github.com/kellyaa/agent-newsletter/issues. The issue tracker is the source of truth for prioritization and definition-of-done; this spec stays focused on architecture and lessons learned.

## Decisions (resolved)

- **Run time:** 07:00 local, daily.
- **Voice:** opinionated and willing to be skeptical, but only on the basis of the actual content. No contrarianism for its own sake; if a piece is solid, say so plainly. Skepticism must cite specifics (sample size, missing ablation, cherry-picked benchmark).
- **Failure notifications:** macOS notifications via `osascript` from `run.sh` and `watchdog.sh`. No email, no third-party service. Run logs in `logs/`.
- **Cost cap:** not enforced in v1, but scaffolded — `runs.cost_usd` recorded every run, `BUDGET_USD` env var defined for future enforcement.
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
- GitHub trending repos tagged `ai-agents`/`llm-agent` (release watchlist removed 2026-05-14 — see §Source list realities)

**Out of scope for v1:** Twitter/X, podcasts (no transcript pipeline), YouTube, Discord/Slack communities.

## Lessons learned (operational)

Non-obvious things discovered during real runs that future-you should know without rediscovering them.

### LLM API (OpenAI-compatible via `scripts/llm.py`)

The pipeline migrated from Claude Code headless (`claude -p`) to direct OpenAI-compatible chat-completions calls as of v1.2. Current lessons:

- Per-section ranking calls (one per `papers`/`news`/`blogs`) work much better than one big call. ~$0.30-1.20 per section, 3-7 minutes each. A single 150-item call risks timeouts and quality degradation.
- Writer call ($0.30-0.50) is cheaper than ranker calls because it processes only ~12 featured items, not 100+ candidates.
- Set `RANKER_TIMEOUT_S=1800` (30 min). 15 min was too tight on chatty news days with verbose release-note `raw_text`. Truncating `raw_text` to ~1500 chars in `write.py` was a measurable cost reducer.
- `llm.py` reads `LLM_EXTRA_HEADERS` from the environment — useful for endpoints that require additional auth headers (e.g., `RITS_API_KEY`, `X-Tenant-Id`).
- The structured output schema must have a **top-level type of `object`**, not `array`. Wrap arrays in `{"items": [...]}` if needed.

### URL hallucination

- The pre-Astro writer occasionally invented URLs (e.g., a Mozilla blog post with a fabricated `hacks.mozilla.org` path) by reading content like a Simon Willison post that *talked about* Mozilla.
- The fix wasn't a tighter prompt rule — those failed. The fix was structural: in the Astro split, **the writer LLM no longer emits URLs at all.** It produces only prose; URLs/titles/scores/tags are spliced from the DB by `write.py` into the YAML frontmatter. URL hallucination is now mechanically impossible at the writer step.
- Astro's content-collection schema (in `site/src/content.config.ts`) validates frontmatter at build time as a second line of defense.

### arXiv

- arXiv enforces ≥3 sec between API calls per IP. With 3 sequential collectors firing in <1 sec we tripped 429s repeatedly. `time.sleep(3.0)` between arxiv calls in `fetch.py` resolves it.
- Even with the sleep, fast retries (`--refetch` then `--refetch` again) can still 429. Wait 10+ minutes between forced refetches.
- arXiv URLs come in `/abs/`, `/pdf/`, and `/html/` variants with `vN` suffixes. `db.canonicalize_url()` collapses all of these to `/abs/<id>`. Critical for cross-source dedup (HN often links to `/pdf/`).

### Idempotency design

- Every stage skips its work if the work product already exists in DB or filesystem. A pipeline failure → fix → `./run.sh` resume model only works because of this. Without idempotency, a transient timeout would force re-running $1+ of LLM work.
- `--force` resets *post-fetch* state (sets featured/appendix/published items back to `candidate`, drops today's runs row, deletes today's issue file). It does **not** re-fetch.
- `--refetch` additionally deletes today's items from the DB before invoking `--force`. Use this rarely; arxiv won't be happy.
- `fetch.py` exits 0 if any source produced data, even with errors logged for individual sources. Earlier behavior (exit nonzero on any error) was wrong — a single arxiv 429 shouldn't taint a 400+-item fetch.

### Source list realities

- Half the "obvious" RSS feeds for AI publications are dead, moved, or were never published. Anthropic, OpenAI, every.to, deeplearning.ai, langchain.com — none expose working RSS as of 2026-05-14. The README's source-count claim should be read as a snapshot, not a stable promise.
- Practitioner blogs (Simon Willison, Latent Space, Interconnects, Eugene Yan, Hamel, Raschka, Karuparti) are reliable. Vendor blogs are not.
- GitHub releases were initially fetched (LangGraph, Claude Code, OpenAI Agents, etc.) but **removed 2026-05-14**: release-note dumps were verbose enough to push the ranker past its old timeout, and most release notes don't carry editorial value at this audience tier. Re-add the `github_releases:` block if needed.

### Voice/format calibration

- The "today's read" theme line is genuinely useful when the LLM finds a real cross-item thread. Keep the prompt's "or null" escape hatch; don't force a theme on scattered days.
- TAKEAWAY/OPEN_QUESTION blockquotes work well *when used sparingly*. The prompt rule "at most one per item, both null is fine" is load-bearing; without it the LLM tries to put one on every item.
- Score caps (papers=5, news=6, blogs=6) feel about right for daily reading. Tags in the closed vocabulary (13 entries) are unchanged from initial design and feel sufficient.

