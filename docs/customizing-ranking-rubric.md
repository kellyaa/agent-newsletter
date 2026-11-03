# Customizing the Ranking Rubric

The ranking rubric defines what makes an item worth featuring. This guide explains how to safely modify it for a new edition or tune it for an existing one.

**Before you start:** Read [ranking-rubric.md](ranking-rubric.md) to understand the four axes, thresholds, and per-section emphasis. This guide assumes you understand the baseline.

---

## Quick Start: Tune for a New Edition

You have a new edition targeting a different audience. The baseline rubric emphasizes:
- Papers: novelty and technical depth
- News: relevance and credibility
- Blogs: depth and practitioner voice

If your edition shifts the audience (e.g., more operations-focused, less academic), you'll want to adjust the emphasis. Here's how.

### Step 1: Understand your edition's audience

In `editions/<slug>/edition.yaml`:

```yaml
reader_profile: >
  Staff engineer running AI-powered infrastructure in production.
  Cares about reliability, cost control, and operational lessons learned.
  Prefers battle-tested techniques over bleeding-edge research.
```

This is the lens for judging "relevance." Everything in the rubric filters through it.

### Step 2: Create prompt fragments for your edition

In `editions/<slug>/prompts/`, create:

```bash
# These are merged with prompts/rank.md
editions/my-ops/prompts/rank.intro.md        # audience + emphasis
editions/my-ops/prompts/rank.axis-*.md       # override specific axes
```

**Do not edit `prompts/rank.md` directly.** The base template is shared; edition-specific wording goes in fragments.

### Step 3: Edit the fragments

#### `rank.intro.md` (audience intro):

```markdown
# Ranking Rubric for [edition name]

Reader: {{reader_profile}}

Scoring logic: the four axes below are scored 0 to their max, then summed 0–10.

## The Four Axes

[The base four axes from prompts/rank.md, plus your emphasis tweaks]

### Relevance (0–4): **[EMPHASIS: Operations-focused version]**

- Build/run/debug agents in production → high (the edition cares about this)
- Papers: only if they include operational guidance or failure modes
- News: deprecations, breaking changes, incident postmortems → max points
- Blogs: battle-tested advice; over-optimized-for-papers → high

*Academic novelty alone is not relevant for this edition.*

### Technical Depth (0–3): [Keep baseline or adjust]
### Novelty (0–2): [Keep baseline or adjust]
### Credibility (0–1): [Keep baseline or adjust]
```

#### `rank.thresholds.md` (section-specific tuning):

```markdown
## Section Thresholds

Score interpretation within sections:

| Section | Featured | Appendix | Drop | Notes |
|---------|----------|----------|------|-------|
| **Papers** | ≥7 | 5–6 | <5 | For ops edition, papers must demonstrate learnings (not just novelty) |
| **News** | ≥6 | 4–5 | <4 | Lower bar for concrete operational news (version bumps, outages) |
| **Blogs** | ≥6 | 4–5 | <4 | Shift: practitioner ops content scores higher than framework deep-dives |
```

### Step 4: Test before going live

```bash
# Lint and syntax-check your edition
uv run scripts/check_edition.py my-ops

# Manually run the ranker against test candidates
# (This requires LLM credentials; see README § LLM configuration)
uv run scripts/rank.py --edition my-ops --dry-run

# Review the scored items and reasoning
cat logs/my-ops/ranked.json | jq '.papers[:3]'
```

### Step 5: Deploy and monitor

1. Open a PR with the new edition (tier 1: code review)
2. Merge to `main` as `status: draft`
3. Run for ~1 week; monitor section balance:

   ```bash
   sqlite3 .worktrees/content/editions/my-ops/state.db \
     "SELECT items_papers, items_news, items_blogs FROM runs \
      WHERE date >= date('now', '-7 days') ORDER BY date"
   ```

4. Adjust thresholds in `edition.yaml` if one section is always empty
5. When balanced, change `status: draft` → `status: live` in a new PR

---

## Deep Customization: Changing Axes or Adding New Ones

The four axes (Relevance, Depth, Novelty, Credibility) are baked into:
- The prompt template (`prompts/rank.md`)
- The JSON schema the LLM returns
- Tests that validate the schema

Changing them requires coordinating code and tests. This is tier 2+ (high-risk).

### When to consider it:

- Your edition has fundamentally different evaluation criteria
- The four axes don't capture something essential
- Examples: "community adoption" for a developer-tools edition, "reproducibility" for a research edition

### How to do it safely:

1. **Document the change first.** File an issue explaining why the current axes are insufficient and what you propose.

2. **Update the data model** (`scripts/models.py`):

   ```python
   class RankerOutput(TypedDict):
       id: str
       score: int           # still 0–10 overall
       tags: list[str]
       reasoning: str
       # Add new axis fields if needed
       community_adoption: int  # 0–2 (example)
   ```

3. **Update the ranker output schema** (`prompts/rank.md`):

   ```json
   {
     "id": "...",
     "score": 7,
     "tags": [...],
     "relevance": 4,
     "depth": 2,
     "novelty": 1,
     "credibility": 0,
     "community_adoption": 2,
     "reasoning": "..."
   }
   ```

4. **Update the ranker logic** (`scripts/rank.py`):

   ```python
   # Parse the new field
   community_adoption = response.get("community_adoption", 0)
   
   # Incorporate into final score (still 0–10)
   score = min(10, relevance + depth + novelty + credibility)
   ```

5. **Update tests** (`tests/test_rank.py`):

   ```python
   def test_ranker_validates_new_axes():
       # Verify schema accepts the new field
       # Verify score calculation includes it
   ```

6. **Update documentation**:
   - [ranking-rubric.md](ranking-rubric.md) — add the new axis
   - [edition-yaml-reference.md](edition-yaml-reference.md) — if threshold config changes
   - Edition's `rank.*.md` fragments — edition-specific guidance

7. **Test end-to-end**:

   ```bash
   uv run --extra test pytest tests/test_rank.py -v
   uv run scripts/check_edition.py agents
   uv run scripts/rank.py --dry-run
   ```

8. **Open a PR** (tier 1–2 depending on scope). Link the issue.

---

## Common Tuning Scenarios

### Scenario: Blogs are too short / too technical

**Symptom:** Blog section is mostly empty; when blogs do feature, they're all deep-dives.

**Root cause:** Blogs threshold is too high, or "depth" is weighted too heavily.

**Fix:**

```yaml
# In editions/<slug>/edition.yaml
sections:
  blogs:
    featured_min: 5    # lower from 6
    appendix_min: 3    # lower from 4
```

Then in `editions/<slug>/prompts/rank.blogs.md`:

```markdown
### For Blogs: Depth and Practicality

- Practitioner voice and narrative style count heavily (not just technical depth)
- Battle-tested lessons from real systems → max points
- Accessible writeups of complex topics → high score
- Listicles and generic hot takes → low score
- Tutorial or how-to guides → high if specific, low if generic
```

Re-run and check: `./run.sh --force`

### Scenario: Papers are too many / too many not relevant

**Symptom:** Papers section is capped every day; many are "interesting but not operational."

**Root cause:** Papers threshold is too low, or "relevance" isn't filtering strongly enough.

**Fix:**

```yaml
# In editions/<slug>/edition.yaml
sections:
  papers:
    featured_min: 7    # raise from 7 (no change, but clarify in prompt)
    appendix_min: 6    # raise from 5
```

Then in `editions/<slug>/prompts/rank.papers.md`:

```markdown
### For Papers: Relevance and Practical Applicability

- Research contributions that advance agent engineering practices → high
- Papers with operational lessons (failure modes, scaling strategies) → high
- Benchmarks or surveys without practical takeaway → cap at 5
- Techniques requiring months of team time or specialized hardware → cap at 6 unless the idea is immediately useful
```

Test: `uv run scripts/check_edition.py <slug>` then `./run.sh --force`

### Scenario: Balancing multiple editions with different thresholds

You have `agents` (general audience) and `ops` (operations-focused) running from the same sources.

**Problem:** If you lower papers threshold in `ops`, it might re-rank items that already featured in `agents`.

**Solution:** Rank is deterministic per edition. Each edition:
- Has its own `state.db`
- Ranks only its own candidates
- Uses its own `edition.yaml` thresholds
- Produces its own issue

So there's no conflict. Each edition's published items are independent. You can set `featured_min: 5` for `ops` and `featured_min: 7` for `agents` without interference.

---

## Measuring Rubric Changes

### Before you change:

```bash
# Current baseline (14 days)
DB=.worktrees/content/editions/agents/state.db
sqlite3 -header "$DB" "
  SELECT date, items_papers, items_news, items_blogs, 
         COUNT(CASE WHEN score >= 7 THEN 1 END) as papers_7plus
  FROM runs JOIN items ON items.fetched_at < runs.date + 1 AND items.fetched_at >= runs.date
  WHERE date >= date('now', '-14 days') AND items.section='papers'
  GROUP BY date ORDER BY date DESC"
```

### After you deploy:

Same query one week later. Compare:
- Section balance (did one empty out?)
- Average scores (did tuning shift the distribution?)
- Featured counts (did it spike or drop?)

### If something broke:

Use `replay_writer.py` to test a past date against your new rubric:

```bash
# Test the new rubric against yesterday's items
uv run scripts/replay_writer.py 2026-11-01
cat logs/theme-replay-2026-11-01.json | jq '.featured | length'
```

---

## Reference

- [ranking-rubric.md](ranking-rubric.md) — baseline axes and thresholds
- [edition-yaml-reference.md](edition-yaml-reference.md) — `featured_min`, `appendix_min`, `cap` per section
- [prompt-slots-guide.md](prompt-slots-guide.md) — how to edit prompt fragments
- [CONTRIBUTING.md § Adding an edition](../CONTRIBUTING.md#adding-an-edition) — full workflow
- [scripts/rank.py](../scripts/rank.py) — ranker implementation
- `prompts/rank.md` — base template with `{{slots}}`

