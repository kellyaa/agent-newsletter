# Documentation Index

This directory contains comprehensive guides for operating, extending, and understanding the agent-newsletter pipeline. Navigate by use case below, or browse the full list of guides.

## Quick Navigation

### I want to…

- **Get started contributing code** → [Start here](../CONTRIBUTING.md), then read the appropriate guide below
- **Run the pipeline manually** → [operations-and-maintenance.md](operations-and-maintenance.md)
- **Troubleshoot a failed run** → [debugging-guide.md](debugging-guide.md)
- **Add a feed source** → [adding-feed-sources-guide.md](adding-feed-sources-guide.md)
- **Create a new newsletter edition** → [CONTRIBUTING.md § Adding an edition](../CONTRIBUTING.md#adding-an-edition), then [edition-yaml-reference.md](edition-yaml-reference.md)
- **Tune an edition's keyword gate or sources** → [edition-tuning-guide.md](edition-tuning-guide.md)
- **Understand how ranking works** → [ranking-rubric.md](ranking-rubric.md)
- **Write a new LLM integration** → [llm-integration-guide.md](llm-integration-guide.md)
- **Work on the Astro site** → [site-development-guide.md](site-development-guide.md)
- **Understand the database schema** → [database-schema-guide.md](database-schema-guide.md)
- **Handle multiple editions** → [multi-edition-operations.md](multi-edition-operations.md)
- **Add a new source family** → [extending-pipeline-sources.md](extending-pipeline-sources.md)

---

## All Guides

### Architecture & Design

| Guide | Purpose |
|-------|---------|
| [site-architecture.md](site-architecture.md) | Astro site structure, routing, content model, build process |
| [repository-branches-and-worktrees.md](repository-branches-and-worktrees.md) | Two-branch model, worktree layout, main vs content |
| [risk-tiers.md](risk-tiers.md) | Change review expectations by what you touch (tier 0–3) |

### Operations

| Guide | Purpose |
|-------|---------|
| [operations-and-maintenance.md](operations-and-maintenance.md) | Daily checks, run model, where things live, when to investigate |
| [debugging-guide.md](debugging-guide.md) | Stage-by-stage failure diagnosis, common errors, recovery steps |
| [multi-edition-operations.md](multi-edition-operations.md) | Running multiple newsletters from one codebase, watchdog model |
| [performance-and-cost-guide.md](performance-and-cost-guide.md) | Where tokens and wall time go; cost measurement and tracking |
| [source-reliability-matrix.md](source-reliability-matrix.md) | Which sources fail how often; fallback and retry behavior |

### Configuration & Tuning

| Guide | Purpose |
|-------|---------|
| [edition-yaml-reference.md](edition-yaml-reference.md) | Every field in `edition.yaml`: thresholds, tags, reader profile, status |
| [edition-tuning-guide.md](edition-tuning-guide.md) | How to adjust a live edition's output: keyword gates, sources, prompt |
| [prefilter-gates-guide.md](prefilter-gates-guide.md) | Recency window, keyword matching, section assignment, dedup rules |
| [ranking-rubric.md](ranking-rubric.md) | The four rubric axes, section-specific thresholds, featured/appendix cutoffs |
| [prompt-slots-guide.md](prompt-slots-guide.md) | Editing rank and write prompt fragments without breaking the pipeline |

### Sources & Data

| Guide | Purpose |
|-------|---------|
| [adding-feed-sources-guide.md](adding-feed-sources-guide.md) | Add an RSS, arXiv, HN, Reddit, or HTML source to an edition |
| [extending-pipeline-sources.md](extending-pipeline-sources.md) | Add a new source family (e.g., authenticated API); adapter pattern |
| [database-schema-guide.md](database-schema-guide.md) | `items`, `runs`, `run_attempts` schema; common queries |
| [metrics.md](metrics.md) | Summary of all recorded metrics (see `metrics/` for column docs) |

### Development

| Guide | Purpose |
|-------|---------|
| [site-development-guide.md](site-development-guide.md) | Run the Astro dev server, understand the content model, edit pages |
| [llm-integration-guide.md](llm-integration-guide.md) | Add new LLM calls; structured output; retries; timeouts |
| [testing-patterns-guide.md](testing-patterns-guide.md) | How tests are organized; fixture patterns; e2e and unit test suites |
| [review-rubric.md](review-rubric.md) | What constitutes good code in this project (clarity, coverage, idempotency) |
| [quality.md](quality.md) | Code quality standards: coverage gate, test patterns, type hints |

### Reference

| Document | Purpose |
|----------|---------|
| [../SPEC.md](../SPEC.md) | High-level design and component architecture; canonical reference |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Contributing workflow, PR naming, branch model, hold-label policy |
| [../AGENTS.md](../AGENTS.md) | Instructions for AI coding agents (permissions, constraints, rules) |
| [../SECURITY-AI.md](../SECURITY-AI.md) | Security policy for agents and LLM calls |

---

## Getting Help

- **Issue tracker:** [github.com/kellyaa/agent-newsletter/issues](https://github.com/kellyaa/agent-newsletter/issues)
- **Design doc:** [SPEC.md](../SPEC.md)
- **Tests as examples:** `tests/` contains patterns for fetch, prefilter, rank, and write operations
- **Search:** Use your editor's search to find references to fields, functions, or concepts across guides

---

## Contributing Documentation

When adding new files to `docs/`:

1. Add a brief description (one line) to the appropriate table above
2. Link from the "Quick Navigation" table if it covers a major task
3. Ensure the file passes `tests/test_docs_paths.py`: paths referenced must exist
4. See [../CONTRIBUTING.md](../CONTRIBUTING.md) for PR workflow

Changes to existing docs: follow the same process, no special approval needed for tier-0 (docs-only) changes.
