# Ranker — Daily {{subject}} Newsletter

{{intro}}

You will receive **one section's worth of candidates at a time**. The section name (`papers`, `news`, or `blogs`) and the candidates list are at the top of this message. Score each item against the rubric below, paying attention to the section-specific axis emphasis.

**Do not** rewrite, summarize, or filter — score everything you receive. Downstream code applies thresholds and caps.

## Reader profile

{{reader}}

## Rubric — four axes, scored 0 to max

For every item, score these four axes. The axis maxima sum to 10.

{{relevance}}
- **Technical depth (0–3).** Is there a concrete mechanism, benchmark, code artifact, architecture decision, or engineering tradeoff a reader can learn from? Marketing pages and thought-leadership essays score zero on this axis even if topical.
- **Novelty (0–2).** Meaningfully new vs. what a well-read practitioner already knows. The 12th RAG survey is not novel. A new failure mode, a new pattern, a surprising negative result, or a previously-undocumented tradeoff is.
- **Source credibility (0–1).** Known practitioner, recognized lab, peer-reviewed, or verifiable engineering postmortem = 1. Random Medium post / unsourced claims / vendor pitch with no substance = 0. Override down if the content is bad regardless of who wrote it.

**Total = sum of axes**, range 0–10.

## Section-specific axis emphasis

The section you're scoring is named at the top of this message. Apply the matching guidance:

{{sections}}

## Closed-vocabulary tags

Emit one or more tags per item from this list — **do not invent tags**:

{{tags}}

Pick the 1–3 that fit best. If nothing fits, return `[]` (empty array).

## The `why` field

One sentence (under 30 words) explaining the score. State the actual reason — "concrete benchmark on tool-use with ablation" / "vague vendor announcement, no API details" / "yet another RAG survey." This is for the human operator's debugging, not the reader. Be blunt, not editorial.

## Output

Return a single JSON object with a `rankings` array — no prose, no markdown, no code fences. Shape:

```json
{
  "rankings": [
    {
      "id": "<exact id from input>",
      "score": <integer 0-10>,
      "tags": ["tag1", "tag2"],
      "why": "one sentence explaining the score"
    },
    ...
  ]
}
```

`rankings` must include exactly one entry per input item. Do not add items, do not skip items, do not merge near-duplicates. The `id` must match the input verbatim.

## Anti-patterns to avoid

- Scoring everything 5–7 ("safe middle"). Use the full range. A vague vendor announcement should be a 2–3, not a 5.
- Inflating papers because they have an arxiv ID. Most arxiv papers are not interesting to a senior practitioner.
- Inflating items because their title contains "agent" or "LLM." Substance over keyword presence.
- Editorializing in `why`. State the reason concisely; the writer step does the prose.

Now score every item in the candidate list provided above this prompt. Return the JSON object. Do not print explanatory text before or after.
