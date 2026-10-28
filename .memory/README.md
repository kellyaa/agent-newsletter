# Correction memory

Agents and humans record corrections here so the next session does not repeat the same mistake. Add one JSON object per line to `.memory/corrections.jsonl` when a reviewer corrects agent work, such as a PR review comment, a reverted change or a CI failure caused by a wrong assumption.

Fields:

| field | meaning |
| --- | --- |
| `date` | ISO date of the correction |
| `ref` | PR or issue it came from, e.g. `#357` |
| `mistake` | what was done wrong |
| `correction` | what is right |
| `rule` | the lasting rule to apply next time, in one sentence |

Example:

```json
{"date": "2026-10-27", "ref": "#357", "mistake": "Used the HTML category page as an RSS url", "correction": "Use the feed host, feed.infoq.com/ai-coding/", "rule": "Confirm a source URL returns XML before adding it to sources.yaml"}
```

Read this directory before starting work. If a correction becomes a standing rule, promote it to `AGENTS.md`.
