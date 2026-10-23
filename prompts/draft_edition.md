# Draft a new newsletter edition

You are setting up a new edition of a daily, opinionated newsletter pipeline. Each edition is a folder of editorial config; the pipeline code is shared. Your job is to propose that config for the new edition described below. A human reviews everything you write, and every URL and regex is machine-checked afterwards, so dead feeds and broken patterns are dropped rather than trusted.

## The new edition

- **Slug:** `{{slug}}`
- **Name:** {{name}}

**Description from the editor:**

{{describe}}

**Seed sources the editor named** (include each one in the right source family if it is a feed, a subreddit or a query; add more of your own):

{{seed_sources}}

## What to produce

Return one JSON object with exactly these keys:

- `tagline`: one sentence for the homepage card.
- `reader_profile`: plain prose, who the reader is and the five or six topics they care about. It is pasted into both the ranker and the writer prompts.
- `downweighted`: 4-8 short phrases for what the ranker should score down.
- `keywords`: 15-40 regular expressions (Python `re`, matched case-insensitively against an item's title and text) for the prefilter keyword gate. Use `\b` word boundaries. Broad enough to let relevant items through; the ranker does the fine judging. Single-quote-safe: no unescaped control characters.
- `tags`: 8-14 lowercase kebab-case tags forming the closed vocabulary the ranker picks from.
- `prompts`: the ten prompt fragments, as strings (see "Fragments" below).
- `sources`: starter sources (see "Sources" below).

## Fragments

Match the shape, length and level of specificity of the worked example. Fragments never contain the characters `{{`.

- `rank_subject` and `write_subject`: two or three words naming the subject, used in "Daily <subject> Newsletter".
- `rank_intro` and `write_intro`: one sentence each saying who the model works for and about what.
- `rank_reader`: the reader as a numbered list of what they care about, then a line starting "Explicitly down-weighted:".
- `rank_relevance`: one bullet for the relevance axis (0-4) in this subject's terms: what scores high, what scores zero.
- `rank_sections`: three bullets, **Papers**, **News**, **Blogs**: what is strong or weak in each for this subject.
- `write_voice`: the voice, with Do and Do not lists and a ~90 word cap on summaries.
- `write_sections`: how the voice tilts for papers, news and blogs; keep the closing paragraph about `previous_newsletter` verbatim.
- `write_theme_examples`: one good theme and three bad ones (dense list, abstract-shaped, generic), written about this subject.

## Sources

Propose real sources you are confident exist. Do not invent URLs: a feed you are unsure of is worse than none. Aim for 6-12 RSS feeds, 2-4 arXiv queries, 2-4 Hacker News queries and 1-3 subreddits.

- `rss`: `{id, name, url, keyword_gate_bypass, recency_days}`. Set `keyword_gate_bypass` true only for low-volume, hand-curated practitioner blogs where nearly every post is relevant. `recency_days` is null unless the blog posts rarely (then 90).
- `arxiv`: `{id, name, query, max_results}`. `query` uses arXiv API syntax, e.g. `cat:cs.SE AND (abs:"LLM" OR abs:"agent")`.
- `hn`: `{id, name, query, min_points}`. The query is free text, not boolean: one concept per query.
- `reddit`: `{id, name, subreddit, min_score}`. `subreddit` is the bare name (`programming`, not `r/programming`). Use a high `min_score` (100+) for large, noisy subreddits.

`id` values are lowercase kebab-case and unique within a family.

## Worked example: the `agents` edition

This is the finished `agents` edition (AI Agents Daily). Match its structure and care.

### editions/agents/edition.yaml

```yaml
{{example_edition}}
```

### editions/agents/sources.yaml

```yaml
{{example_sources}}
```

{{example_fragments}}

## Output

Return only the JSON object: no prose, no markdown, no code fences.
