# Edition Configuration Reference

This document is the authoritative reference for `editions/<slug>/edition.yaml`, the configuration file that defines an edition's identity, audience, and filtering thresholds.

## Quick Start

The simplest way to create an edition:

```bash
uv run scripts/new_edition.py my-edition --name "My Edition" --describe "One paragraph: subject, audience, what matters."
```

This scaffolds `editions/my-edition/edition.yaml` with all required fields. Fill in the TODOs and run:

```bash
uv run scripts/check_edition.py my-edition
```

to validate and dry-run your sources.

---

## Complete Field Reference

### `slug` (required, immutable)

**Type:** string (lowercase, digits, hyphens only)

**Constraints:**
- Must match the folder name: `editions/<slug>/`
- Permanent once a live edition (it appears in URLs)
- No uppercase, spaces, or special chars (enforced by `scripts/edition.py`)

**Example:**
```yaml
slug: ai-agents
```

---

### `name` (required)

**Type:** string

**Usage:** Display name on the site homepage, in the `<title>` tag, and in prompts.

**Constraints:** No length limit, but 30-50 characters recommended.

**Example:**
```yaml
name: AI Agents Daily
```

---

### `tagline` (required)

**Type:** string

**Usage:** One-sentence tagline on the site homepage card (featured items section) and filled into prompts as `{{tagline}}`.

**Constraints:** One sentence, roughly 80-120 characters.

**Example:**
```yaml
tagline: "A daily digest on building and running AI agents for senior engineers."
```

---

### `status` (required)

**Type:** enum: `draft`, `live`, or `paused`

**Meanings:**

| Status | Behavior |
|--------|----------|
| `draft` | Runs daily; sends failure notifications; hidden on site unless `SHOW_DRAFTS=1`. Use for ~1 week while tuning. A draft may contain `TODO` placeholders. |
| `live` | Runs daily; published on the homepage. Must have zero `TODO` placeholders (enforced by `check_edition.py`). Permanent slug (see above). |
| `paused` | Listed on the homepage but labelled "paused"; skipped by the default `./run.sh` (but included by `./run.sh --edition <slug>`). Still sends failure notifications for debugging. |

**Lifecycle:**
1. Start with `status: draft`
2. Run for ~1 week, tune keyword gates and prompts
3. Delete `REVIEW.md` and change `status: live` in a one-line PR

**Example:**
```yaml
status: draft
```

---

### `order` (required)

**Type:** integer

**Usage:** Sort key for homepage cards and edition run order (lower runs first).

**Constraints:**
- Positive integer (typically 10, 20, 30, ...)
- Leave gaps (10-unit increments) so you can insert editions later
- Editions with the same `order` sort alphabetically by slug

**Example:**
```yaml
order: 10    # Runs first; appears first on homepage
```

---

### `reader_profile` (required)

**Type:** string (multi-line, in YAML "folded" format)

**Usage:** Describes the reader to the ranker and writer LLMs. Filled into prompts as `{{reader_profile}}`.

**Guidelines:**
- Write as plain prose (not a list)
- Include: seniority level, what they already know, the 4-6 topics they care about
- Include negatives: what they DON'T care about (complementary to `downweighted`)

**Example:**
```yaml
reader_profile: >-
  Senior engineer or staff architect who writes production code and cares about:
  building agents, observability, performance and cost, safety, working code
  examples. Already knows what an LLM is and what a vector database does.
```

---

### `downweighted` (required, can be empty)

**Type:** list of strings

**Usage:** Topics the ranker should score down, even if they pass keyword gates. Each item is a regex (case-insensitive). Filled into prompts as `{{downweighted}}`.

**Constraints:** Empty list `[]` is valid if your edition has no downweighted topics.

**Example:**
```yaml
downweighted:
  - "funding|VC|venture capital"
  - "benchmarks without methodological interest"
  - "consumer AI hype"
```

---

### `keywords` (required, can be empty)

**Type:** list of strings

**Usage:** Prefilter gate: an item must match at least one regex (case-insensitive) in its title or text to pass, *unless* its source sets `keyword_gate_bypass: true`. Filled into prompts as `{{keywords}}`.

**Constraints:**
- Each item is a compiled regex; invalid regex raises an error in `check_edition.py`
- Prefer 5-10 broad patterns over 30 narrow ones
- Use `\b` for word boundaries to avoid partial matches
- Empty list `[]` means no keyword gate (all items pass)

**How to tune:**
1. Run `uv run scripts/check_edition.py <slug>` to see per-source counts
2. Adjust regex patterns
3. Re-run `check_edition.py` to see changes in item counts
4. Iterate until you have roughly:
   - papers: 100-300 candidates/day
   - news: 100-200 candidates/day
   - blogs: 50-100 candidates/day

**Example:**
```yaml
keywords:
  - '\bagent|agentic'
  - '\bLLM|language model'
  - '\btool use|function calling'
  - '\bprompt engineering'
  - '\beval|evaluation'
```

---

### `tags` (required)

**Type:** list of strings

**Usage:** Closed vocabulary for tags assigned to featured items. The ranker must pick from this list. Filled into prompts as `{{tags}}`.

**Constraints:**
- Kebab-case (lowercase, hyphens for spaces), no underscores or camelCase
- 8-14 tags is typical (enough diversity, not overwhelming)
- Tags are validated and lowercased by `scripts/new_edition.py`

**Example:**
```yaml
tags:
  - tool-use
  - planning
  - agents-for-code
  - observability
  - safety
  - benchmarks
  - frameworks
  - papers
```

---

### `sections` (required)

**Type:** map of section name → threshold config

**Section names:** `papers`, `news`, `blogs` (all three required)

**Per-section config:**

| Key | Type | Meaning |
|-----|------|---------|
| `featured_min` | int (1-10) | Minimum ranker score to be featured (typically 6-7) |
| `appendix_min` | int (1-10) | Minimum score to land in appendix (typically 4-5, ≤ featured_min) |
| `cap` | int | Max featured items per section per issue |
| `burst_cap` (papers only) | int | Higher cap on heavy arXiv days (optional) |
| `burst_trigger_score` (papers only) | int | Score threshold triggering burst cap (optional) |
| `burst_trigger_count` (papers only) | int | # of high-scoring papers needed to trigger burst (optional) |

**Example (no burst):**
```yaml
sections:
  papers:
    featured_min: 7
    appendix_min: 5
    cap: 5
  news:
    featured_min: 6
    appendix_min: 4
    cap: 6
  blogs:
    featured_min: 6
    appendix_min: 4
    cap: 6
```

**Example (with burst cap for papers):**
```yaml
sections:
  papers:
    featured_min: 7
    appendix_min: 5
    cap: 5
    burst_cap: 8
    burst_trigger_score: 8
    burst_trigger_count: 3
  news:
    featured_min: 6
    appendix_min: 4
    cap: 6
  blogs:
    featured_min: 6
    appendix_min: 4
    cap: 6
```

---

## Validation & Errors

Run after any edit:
```bash
uv run scripts/check_edition.py <slug>
```

This validates:
1. **YAML syntax** — file parses correctly
2. **Required fields** — all fields above are present (no `TODO` if live)
3. **Field types** — slug is kebab-case, status is valid enum, etc.
4. **Regexes** — keyword and downweighted patterns compile
5. **Tags** — kebab-case, no duplicates
6. **Sections** — all three present, thresholds are 1-10, cap > 0
7. **Dry-run sources** — every source in `sources.yaml` is fetched and gated (no LLM)

If validation fails, the error message names the problem and the line number.

---

## Common Patterns

### Broad keyword gate (few items filtered)
```yaml
keywords:
  - '.'  # Matches everything (empty gate)
```
Not recommended unless you want high recall.

### Tight keyword gate (high precision)
```yaml
keywords:
  - '\bagent'
  - '\bLLM|language model'
  - '\bAI|artificial intelligence'
```
This filters to ~50-100 items/day for news/blogs.

### No downweighting (accept everything passing keyword gate)
```yaml
downweighted: []
```

### Conservative thresholds (fewer items featured)
```yaml
sections:
  papers: { featured_min: 8, appendix_min: 6, cap: 3 }
  news: { featured_min: 7, appendix_min: 5, cap: 4 }
  blogs: { featured_min: 7, appendix_min: 5, cap: 4 }
```

### Generous thresholds (more diversity)
```yaml
sections:
  papers: { featured_min: 6, appendix_min: 4, cap: 8 }
  news: { featured_min: 5, appendix_min: 3, cap: 8 }
  blogs: { featured_min: 5, appendix_min: 3, cap: 8 }
```

---

## Edition Lifecycle Example

**Step 1: Create the edition**
```bash
uv run scripts/new_edition.py my-tech --name "My Tech Daily" \
  --describe "Latest in distributed systems and cloud infrastructure"
```

**Step 2: Fill in TODOs in `editions/my-tech/edition.yaml`**
- reader_profile: describe your reader
- downweighted: topics to deprioritize
- keywords: refine regex patterns
- tags: define your tag vocabulary

**Step 3: Validate and dry-run**
```bash
uv run scripts/check_edition.py my-tech
# Review per-source counts; adjust keyword gates
```

**Step 4: Add/edit sources** (see `CONTRIBUTING.md`)
```bash
# Edit editions/my-tech/sources.yaml
uv run scripts/check_edition.py my-tech
```

**Step 5: Run for a week** as `status: draft`
```bash
./run.sh --edition my-tech
```
Tune keywords, prompts, and thresholds based on output.

**Step 6: Go live**
- Delete `editions/my-tech/REVIEW.md`
- Change `status: draft` to `status: live`
- Open a one-line PR

**Step 7: Continue tuning** (after going live, post on homepage)
- Keywords and thresholds can be changed anytime
- Slug is permanent (no URL breakage)
- name, tagline, reader_profile should be stable (used in URLs and prompts)

---

## Troubleshooting

### check_edition.py rejects my edition
- **Unknown keys**: YAML keys must match the reference above
- **Invalid regex**: keyword or downweighted regex doesn't compile; test it on [regex101.com](https://regex101.com)
- **TODO placeholders**: if `status: live`, no `TODO` text allowed
- **Slug mismatch**: slug in YAML must match folder name

### Too many/few items passing keyword gate
- Run `check_edition.py` to see per-source counts
- Adjust `keywords` list and re-run
- Use `\b` for word boundaries to avoid false matches

### Why is an item scoring 5 instead of 7?
- See `docs/ranking-rubric.md` for score interpretation
- Use `scripts/replay_writer.py` to test prompt changes
- Items that don't match reader_profile are downscored

### How do I pause an edition temporarily?
```yaml
status: paused
```
It still appears on the homepage (labelled "paused") and `run.sh` skips it by default. Use `./run.sh --edition <slug>` to force a run.

---

## See Also

- `CONTRIBUTING.md` — how to add/remove sources
- `docs/prefilter-gates-guide.md` — keyword gate tuning in depth
- `docs/prompt-slots-guide.md` — customizing ranker/writer prompts
- `docs/ranking-rubric.md` — understanding item scores
