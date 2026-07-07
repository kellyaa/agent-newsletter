# Front-page theme: lede + territory rewrite

## Problem

The writer LLM's `theme` field (rendered on the front-page card of each daily issue) has failed in two opposite directions across successive rubric versions:

1. **Pre-2026-06-23 (generic era):** themes collapsed to interchangeable category labels — *"Today's papers move beyond basic capabilities to focus on the engineering of reliable, measurable, and safe agent systems."* Strip the date and no reader could tell which issue it belonged to. The rubric only said "include if a clear theme emerges," which the model satisfied with field-level framings.

2. **Post-2026-06-23 (dense era):** the rewrite in commit `66eadd6` swung to the other extreme. Themes became run-on lists of 4-6 system names, each with a mini-abstract clause. Example from 2026-07-07: *"FARMA details attacks on remembered reasoning history, `MemGhost` shows how to inject stealthy memories via a single email, ADI bypasses instruction-focused defenses by poisoning metadata, `Governed Individuation` proposes an architectural fix using cryptographic identity, and `R2Act` reveals models choose valid recovery actions 37-60% of the time."* Specific, but reads like a catalog, not editorial. A cold reader parachuting onto the front page has no idea what any of those names refer to.

Root cause is symmetric: the rubric asks the writer to *enumerate items* (either as categories or as system names). Neither is what a good editor writes on a front page.

## Goal

A front-page theme that reads plainly to a cold reader while still being specific and distinguishable day-to-day. A reader who lands on the page fresh should walk away knowing:

- The single most interesting thing on today's issue, framed by what it *is* before what it *found*.
- A rough sense of what else is in the issue (kinds of work, not names).

## Non-goals

- No template/UI change. This is a prompt-only change to `prompts/write.md`.
- No schema change to `scripts/write.py`. The `theme` field stays a nullable string.
- No change to per-item summaries, appendix rendering, or any other part of the writer output.

## Design

### Shape: lede + territory

The theme has two parts, ~60 words total.

**Part 1 — Lede (1-2 sentences).** Introduce 1-2 featured items using the "front-page test" (below). Written for a cold reader: frame each item by what it *is* (a paper looking at X, a postmortem of Y, a benchmark measuring Z) before quoting its finding. Prefer plain English over the paper's internal jargon. Project or paper names are optional in the lede — include them only when the name itself will mean something to the reader without further context; otherwise defer names to the per-item summary.

**Voice pattern:** *"A new paper looks at X and finds Y."* / *"A production postmortem describes how Z broke and what fixed it."* / *"Two blog posts push back on the assumption that…"* The subject of the sentence is the *kind of work* and its *finding*, not the project name.

**Part 2 — Territory (1 sentence).** Gestures at what else is in the issue *without name-checking systems*. Talks about kinds of work — "three more papers on memory attacks", "two production postmortems", "a batch of eval methodology posts". The reader learns the shape of the day.

**Escape hatch.** If the day has no clear standout (all featured items are solid but interchangeable), the lede can name one item and the territory sentence carries more weight. Return `null` only when fewer than 3 featured items exist (unchanged from current rubric).

### The front-page test

Of today's featured items, pick 1-2 that best fit at least one of:

- **(a)** most surprising or counterintuitive claim
- **(b)** sharpest contradiction with prior work or another featured item
- **(c)** most actionable finding for a senior engineer
- **(d)** explainable in one sentence to a reader who hasn't heard of the specific project — if you can't frame it plainly, it's probably not the right lede

Highest score is not automatically the lede. A smaller item with a striking claim outranks a solid-but-expected paper.

### Source constraint (featured only)

The items named in the lede **must appear in the input's `featured` array**. Not the appendix. Not `previous_newsletter`. Not the model's training data.

Structurally this is already guaranteed — `scripts/write.py:80-88` selects only `status = 'featured'` rows for the writer's `featured[]` input, and the appendix comes in as a separately-labeled field. Making it an explicit rule in the theme section is belt-and-suspenders against the model drifting into name-drops it can't back up.

### Worked examples

**Good.**
> A new paper looks at what happens to code review inside teams that adopt AI-generated PRs — and finds that reviewers approve more (+14.5pp) but write 22% fewer comments, an erosion that survives four organizational controls. Two other papers explore related memory-store attacks; a Simon Willison post argues the same review-atrophy pattern shows up at his consulting clients.

**Bad — abstract-shaped.**
> Habituation at the Gate documents a 22% drop in reviewer comments as AI PR exposure grows, even as approval rates rise +14.5pp — a review-erosion signal that survives four organizational controls.

Reads like the paper's own abstract. Cold reader can't tell what "Habituation at the Gate" is, why +14.5pp matters, or what the paper is *about* — only what it *found*.

**Bad — dense (real, from 2026-07-07).**
> FARMA details attacks on remembered reasoning history, MemGhost shows how to inject stealthy memories via a single email, ADI bypasses instruction-focused defenses by poisoning metadata, Governed Individuation proposes an architectural fix using cryptographic identity, and R2Act reveals models choose valid recovery actions 37-60% of the time.

**Bad — generic (real, from 2026-06-19 era).**
> Today's papers move beyond basic capabilities to focus on the engineering of reliable, measurable, and safe agent systems.

### Banlist additions

Keep all existing banned framings. Add:

- A comma-separated list of more than two system/paper names in the theme. Any sentence naming 3+ items in a row is a violation, even without banned framings.
- Naming items in the *territory* sentence. Territory talks about kinds of work, not specific projects.

### Rules dropped

- "Name at least 3 specific things (papers, systems, mechanisms, numbers, claims)." This is the rule driving the dense output. Replaced by the lede+territory structure.
- "Use the items' own vocabulary — if a paper introduces 'Memory Contagion' or 'GroundEval,' use that name." Replaced by: names are optional, prefer plain framing, defer names to summary when the name won't mean anything cold.

## Cost and risk

**Cost:** effectively zero. The theme is one field of ~60 words in an output that runs 6-8k completion tokens (`scripts/write.py:34-36`). A shorter/tighter theme saves ~15-20 output tokens per run — rounding to zero at ~$0.30-0.50/run total.

**Risk 1 — schema retries.** If the new rubric confuses the model into more parse failures, the retry logic (commit `1129fb1`) doubles the writer cost. Mitigation: replay against 3 recent runs before deploying; watch retry rate over the first week after deploy.

**Risk 2 — drift back to generic.** With the "name at least 3 specific things" rule gone, the model might slide back toward the pre-June-23 category-label failure. Mitigation: the banlist for generic framings stays; the front-page test's plain-English clause forces the lede to describe *a specific item*.

**Rollback:** trivial. `git revert` the prompt commit. No data migration, no schema change.

## Testing

Manual eyeball check before deploying. Replay the writer stage against three recent runs known to have failed themes:

- `2026-06-24` (dense: names 4 systems)
- `2026-06-27` (dense: names 6 systems)
- `2026-07-07` (dense: names 5 systems)

Method: for each target date, temporarily move the existing issue file aside (`site/src/content/issues/<date>.md`), invoke the writer with the new prompt against the same `featured[]` set already in `state.db` (writer selects `status = 'featured'`, which is stable post-publish), diff the resulting theme against the archived one. Restore the original file after inspection — the replay is exploratory, not a rewrite of history.

**Success criteria (all four must hold across all three replays):**

1. Each new theme names 1-2 items maximum in the lede, plus a territory sentence.
2. Each lede frames its item by *what it is* before quoting a number or claim — reads plainly to a cold reader.
3. Each theme includes a territory sentence describing kinds-of-work, not more system names.
4. The three themes read distinguishably from each other (you can tell which issue each belongs to without seeing the date).

**Failure recovery:** if 1-2 of the three replays fail a criterion, iterate on the prompt. If all three fail the same way, the rubric needs a bigger rethink — surface that finding, don't push forward.

## Files touched

- `prompts/write.md` — replace the theme description in the output-rules block; replace the entire `## Theme` section with the new lede+territory rubric, examples, front-page test, and updated banlist.

Nothing else.
