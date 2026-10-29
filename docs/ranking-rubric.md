# Ranking Rubric: Score Interpretation and Exemplars

The ranker LLM scores each candidate item on a scale of 1-10 based on how well it serves the edition's reader profile. This guide explains the scoring scale, per-section expectations, and provides exemplars to calibrate understanding.

## Score Scale: 1-10

The ranker assigns each item an integer score from 1 (reject) to 10 (must-include). Scores are bucketed into three outcome categories:

| Score Range | Outcome | Interpretation |
|-------------|---------|-----------------|
| 1-3 | **Dropped** | Not relevant to reader; wasted space |
| 4-6 | **Appendix** | Interesting but uncertain; lower confidence |
| 7-10 | **Featured** | High confidence; strong reader value |
| **Threshold** | Set per edition per section | Tuned in `edition.yaml` → `sections.<section>.featured_min` |

---

## Unifying Principle

**The score answers:** *How much value does this provide to a senior engineer trying to build, run, or understand production agents?*

Items are evaluated on:
1. **Relevance:** Does this address the reader's stated interests?
2. **Signal strength:** Is this concrete (code, patterns, lessons learned) or vague hype?
3. **Novelty:** Is this new insight or reheated old ideas?
4. **Audience fit:** Senior engineers with production code = higher bar than consumer blog posts

---

## Per-Section Scoring

### Papers

Papers are held to the highest standard (typically featured_min: 7-8) because they take time to read and must offer novel techniques.

| Score | Bar | Examples |
|-------|-----|----------|
| 9-10 | Breakthrough technique directly applicable to production | "Scaling agents via tool-use optimization," "Proven cost reduction through hierarchical planning" |
| 7-8 | Solid technique, well-executed, advances the state of practice | "Empirical study of prompt injection attacks in production," "Benchmarking agent frameworks at scale" |
| 6 | Relevant but incremental; interesting context but not actionable | "Literature review of agent planning approaches," "Comparison of 3 open-source frameworks" |
| 4-5 | Tangential or weak execution; interesting angle but execution is unclear | "Preliminary thoughts on agent safety," "Position paper on AI regulation" |
| 1-3 | Not relevant; generic AI hype without agent focus | "Why AI is transforming business," "Overview of LLMs," benchmarks without methodological interest |

**Exemplars (real papers, scores rationalized):**

- **Score 9:** "Agents can leverage tool-use to execute complex code generation tasks 40% faster" → *High relevance (tool-use is core), concrete metrics, production-applicable*
- **Score 7:** "A framework for multi-agent debugging" → *Solid contribution, but more specialized than broadly applicable*
- **Score 5:** "Surveying 20 years of multi-agent research" → *Contextual value, but not novel; reader already knows the landscape*
- **Score 2:** "The future of AI is agentic systems" → *Hype without substance, no new technique*

---

### News

News items (blog posts, announcements, release notes) are scored lower (typically featured_min: 6-7) because they're more ephemeral and signal-to-noise is lower.

| Score | Bar | Examples |
|-------|-----|----------|
| 8-10 | Major milestone or pattern change; affects production decisions | "OpenAI releases extended context reasoning," "A major agent framework goes open-source" |
| 6-7 | Solid announcement or practice writeup; useful for practitioners | "Company shares lessons running 100K agents in production," "Open-source tool for agent evaluation released" |
| 5 | Interesting but incremental; useful context but not urgent | "New model reaches 95% on benchmark X," "Blog post on prompt engineering best practices" |
| 4 | Tangential or vague; might appeal to subset of readers | "Startup raises funding for AI agents," "Interview with researcher on agent safety" |
| 1-3 | Not relevant; generic AI news without agent angle | "Another LLM benchmark," "AI company hires CTO," "Regulatory guidance on AI" |

**Exemplars:**

- **Score 9:** "Anthropic releases Claude 4 with native tool-use and 200K context window" → *Major capability shift, affects all agent practitioners*
- **Score 6:** "Databricks shares 5 lessons from deploying agents in production" → *Relevant how-to, useful for practitioners*
- **Score 4:** "Sequoia releases framework for evaluating language models" → *Related to agents, but generic evaluation talk*
- **Score 2:** "Anthropic raises $5B Series B funding" → *Company news, not practice-relevant*

---

### Blogs

Blogs are the lowest bar (typically featured_min: 5-6) because they're accessible and opinion-driven; readers expect variety.

| Score | Bar | Examples |
|-------|-----|----------|
| 8-10 | Exceptional writeup; actionable advice or novel perspective from credible author | "Simon Willison: I built a production agent system, here's what I learned" |
| 6-7 | Solid practice guide or personal lesson; credible voice, worth the time | "How to test agents: a practical guide," "Running LLM inference at 10ms latency" |
| 5 | Interesting angle; solid writing but familiar territory | "Using agents to automate customer support," generic tutorial on embeddings |
| 4 | Helpful but niche; might not apply to most readers | "Using agents for niche use case X," vague AI/business commentary |
| 1-3 | Not relevant; clickbait or generic inspiration | "Why AI will change everything," "5 AI trends to watch in 2026" |

**Exemplars:**

- **Score 9:** "I shipped 3 agent products; here's what surprised me" (from experienced practitioner) → *Personal lessons, credible voice, high signal*
- **Score 6:** "Building your first agent: a step-by-step guide" → *Accessible, well-written, useful context*
- **Score 4:** "Using ChatGPT for customer support" (generic how-to) → *Useful but not specific to reader profile*
- **Score 2:** "AI is the future and everyone needs it" → *Hype without substance*

---

## Common Scoring Mistakes

### Over-scoring (Readers complain: "Why was this featured?")

**Mistake 1: Scoring generic content high because it mentions agents**
```
"5 ways AI will transform business" → Scored 7 ✗
Reason: Mentions agents but is generic business hype
Better: Score 2-3, it's for the "AI in business" readers, not practitioners
```

**Mistake 2: Scoring papers high for novelty, ignoring applicability**
```
"New approach to agent planning using graph transformers" → Scored 8 ✗
But: No code, no production deployment, academic curiosity only
Better: Score 5-6, it's interesting but not actionable for production builders
```

**Mistake 3: Scoring niche content as broadly relevant**
```
"Using LLMs for medical diagnosis" → Scored 7 ✗
But: Highly specialized; only 5% of readers work in healthcare
Better: Score 4-5, interesting angle but niche
```

### Under-scoring (Readers ask: "Why wasn't this featured?")

**Mistake 1: Penalizing established patterns as "not novel"**
```
"Production patterns for multi-agent systems" → Scored 4 ✗
But: Comprehensive guide, well-written, directly applicable
Better: Score 7, novelty < applicability for this audience
```

**Mistake 2: Dismissing opinion pieces from credible voices**
```
"Lessons from shipping 10 production agents" → Scored 4 ✗
But: Author is well-known practitioner, lessons are actionable
Better: Score 7-8, credible insider perspective
```

**Mistake 3: Being too strict on papers**
```
"Survey of agent architectures" → Scored 3 ✗
But: Comprehensive, well-organized, useful reference
Better: Score 6, surveys serve a purpose even if not novel
```

---

## Audience Targeting

Scores are relative to the **reader_profile** in the edition's `edition.yaml`. Always ask:

> Does this serve the reader as described?

**Example 1: Agent builders (production engineers)**
- Score 8: "How we scaled to 1M agent invocations/day" → *Yes, directly serves builders*
- Score 4: "Philosophical thoughts on AGI" → *No, too high-level for practitioners*

**Example 2: Researchers / cutting-edge practitioners**
- Score 8: "Novel approach to multi-agent coordination" → *Yes, advances research*
- Score 4: "Proven patterns from production deployments" → *Lower for researchers (not novel)*

**Example 3: Business/decision makers**
- Score 8: "ROI analysis of agent automation" → *Yes, speaks to business value*
- Score 4: "Deep dive into transformer attention mechanisms" → *No, too technical*

---

## Tuning Your Edition's Scoring

If you notice recurring score misalignment, adjust the `featured_min` threshold in your edition:

```yaml
sections:
  papers:
    featured_min: 7     # Only top-tier papers featured
    appendix_min: 5
  news:
    featured_min: 6     # Balance of news + high-confidence items
    appendix_min: 4
  blogs:
    featured_min: 5     # Lower bar; more variety
    appendix_min: 3
```

**Too many uninteresting items featured?** → Raise `featured_min` (e.g., 6 → 7)

**Too few items featured?** → Lower `featured_min` (e.g., 7 → 6) or expand `keywords`

---

## Using Scores Operationally

### Debugging: "Why did this paper rank 6 instead of 8?"

1. **Check the reader_profile:** Does the paper serve that reader?
2. **Check for downweighting:** Did `downweighted` keywords apply?
3. **Check for keyword gate:** Did it barely pass the keyword gate? (Weak signal)
4. **Replay the ranker:** `uv run scripts/replay_writer.py <date>` and inspect the ranker's reasoning

### Understanding appendix items

Appendix items (scores 4-6) are "interesting but uncertain." They belong in appendix because:
- They're tangential (related but not core)
- They're from lower-signal sources (many views, low passes through prefilter)
- They're speculative or early-stage

Readers can browse appendix for depth; featured items are "definitely read this."

### Cost implications

More featured items = higher cost (they consume editorial space and require writer prose). Adjust `cap` per section:

```yaml
sections:
  papers: { cap: 3 }   # Conservative: 3 high-quality papers
  papers: { cap: 10 }  # Liberal: 10 papers + variety
```

---

## Exemplar Issue Walkthrough

**Issue date:** 2026-06-15

**Papers section:**
- "Scaling multi-agent systems with distributed tools" — Score 9 (featured) → Yes; direct technique for practitioners
- "Agent benchmarks across 10 frameworks" — Score 7 (featured) → Useful comparison, actionable
- "Why AI governance matters" — Score 2 (dropped) → Not relevant to builder audience
- "Preliminary thoughts on agent safety" — Score 5 (appendix) → Interesting but not production-ready

**News section:**
- "OpenRouter releases model marketplace with agent inference optimization" — Score 8 (featured) → Major platform for builders
- "Stanford releases 50K annotated agent dialogue dataset" — Score 6 (featured) → Useful resource for training
- "AI company X raises Series C" — Score 2 (dropped) → Not relevant
- "New benchmark shows GPT-4 vs Claude comparison" — Score 4 (appendix) → Interesting but generic benchmark

**Blogs section:**
- "I built 3 agents and failed; here's why" — Score 8 (featured) → Credible voice, lessons learned
- "Using agents to automate customer support" — Score 5 (appendix) → Niche use case, useful but not broadly applicable
- "AI trends for 2026" → Score 2 (dropped) → Generic futurism

---

## Recalibration

Every 2-4 weeks, review your edition's scoring:

```bash
# Inspect the last 5 days of ranked items
sqlite3 .worktrees/content/state.db \
  "SELECT title, score, section FROM items \
   WHERE status IN ('featured', 'appendix') \
   AND published_date > date('now', '-5 days') \
   ORDER BY score DESC LIMIT 30;"
```

Ask:
- Are scores consistent with the rubric above?
- Are all featured papers >6 in quality?
- Are all appendix items <7?
- Would a reader agree with the scores?

If not, adjust the ranker prompt in `editions/<slug>/prompts/rank.*.md` or the `featured_min` threshold.

---

## See Also

- `docs/edition-yaml-reference.md` — threshold configuration (featured_min, appendix_min, cap)
- `docs/prefilter-gates-guide.md` — gate-filtering before ranking
- `scripts/rank.py` — ranker implementation
- `scripts/replay_writer.py` — replayable ranker testing
- `SPEC.md` — audience and editorial rubric (original design)
