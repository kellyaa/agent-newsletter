# Documentation Index

This directory contains comprehensive guides for operating, extending, and understanding the agent-newsletter pipeline. Navigate by use case below, or browse the full list of guides.

## Quick Navigation

### I want to…

- **Look up a command** → [command-reference.md](command-reference.md)
- **Contribute for the first time** → [getting-started-guide.md](getting-started-guide.md), then [Start here](../CONTRIBUTING.md)
- **Get started contributing code** → [Start here](../CONTRIBUTING.md), then read the appropriate guide below
- **Run the pipeline manually** → [operations-and-maintenance.md](operations-and-maintenance.md)
- **Learn run strategies (--force, --refetch, --failed)** → [run-strategies-and-recovery.md](run-strategies-and-recovery.md)
- **Troubleshoot a failed run** → [debugging-guide.md](debugging-guide.md)
- **Fix low featured item count** → [troubleshooting-low-featured-count.md](troubleshooting-low-featured-count.md)
- **Understand LLM errors & content filtering** → [llm-error-handling-guide.md](llm-error-handling-guide.md)
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
| [agent-boundaries.md](agent-boundaries.md) | What AI agents may and may not do here (summary of AGENTS.md and the policy file) |
| [multi-agent.md](multi-agent.md) | How several AI agents work in the repo at once without colliding (roles, coordination) |
| [strategy.md](strategy.md) | Index of where direction, roadmap, health and agent activity are recorded |

### Operations

| Guide | Purpose |
|-------|---------|
| [operations-and-maintenance.md](operations-and-maintenance.md) | Daily checks, run model, where things live, when to investigate |
| [run-strategies-and-recovery.md](run-strategies-and-recovery.md) | When to use --force, --refetch, --failed; recovery for each failure mode |
| [operations-runbook.md](operations-runbook.md) | Fast lookup for common operational tasks; quick-reference companion to operations-and-maintenance |
| [ai-ops-runbook.md](ai-ops-runbook.md) | Index for incidents: pipeline health, recovery, cost, and handling agent-authored changes |
| [debugging-guide.md](debugging-guide.md) | Stage-by-stage failure diagnosis, common errors, recovery steps |
| [troubleshooting-low-featured-count.md](troubleshooting-low-featured-count.md) | When an issue publishes with fewer featured items than expected; diagnosis and fixes |
| [llm-error-handling-guide.md](llm-error-handling-guide.md) | LLM failures, content filtering, retries, timeouts; what happens when ranking or writing fails |
| [database-recovery-guide.md](database-recovery-guide.md) | Recover a corrupted or inconsistent `state.db`; advanced troubleshooting beyond the debugging guide |
| [monitoring-and-alerting-integration.md](monitoring-and-alerting-integration.md) | Integrate pipeline health with external monitoring and alerting systems |
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
| [customizing-ranking-rubric.md](customizing-ranking-rubric.md) | How to safely modify or tune the ranking rubric for a new or existing edition |
| [environment-configuration-reference.md](environment-configuration-reference.md) | Complete reference for every environment variable the pipeline reads |
| [prompt-slots-guide.md](prompt-slots-guide.md) | Editing rank and write prompt fragments without breaking the pipeline |

### Sources & Data

| Guide | Purpose |
|-------|---------|
| [adding-feed-sources-guide.md](adding-feed-sources-guide.md) | Add an RSS, arXiv, HN, Reddit, or HTML source to an edition |
| [extending-pipeline-sources.md](extending-pipeline-sources.md) | Add a new source family (e.g., authenticated API); adapter pattern |
| [database-schema-guide.md](database-schema-guide.md) | `items`, `runs`, `run_attempts` schema; common queries |
| [sql-export-and-analysis-guide.md](sql-export-and-analysis-guide.md) | Export `state.db` for external analysis, build custom reports, audit pipeline decisions |
| [metrics.md](metrics.md) | Summary of all recorded metrics (see `metrics/` for column docs) |

### Development

| Guide | Purpose |
|-------|---------|
| [getting-started-guide.md](getting-started-guide.md) | First-time contributor walkthrough: understand the codebase, set up, make your first change |
| [site-development-guide.md](site-development-guide.md) | Run the Astro dev server, understand the content model, edit pages |
| [llm-integration-guide.md](llm-integration-guide.md) | Add new LLM calls; structured output; retries; timeouts |
| [code-patterns-guide.md](code-patterns-guide.md) | Common patterns used across the project (DB access, config loading) when writing new code |
| [testing-patterns-guide.md](testing-patterns-guide.md) | How tests are organized; fixture patterns; e2e and unit test suites |
| [dependency-management-guide.md](dependency-management-guide.md) | Adding and managing Python and Node.js dependencies; lockfile workflow |
| [review-rubric.md](review-rubric.md) | What constitutes good code in this project (clarity, coverage, idempotency) |
| [quality.md](quality.md) | Code quality standards: coverage gate, test patterns, type hints |

### Reference

| Document | Purpose |
|----------|---------|
| [command-reference.md](command-reference.md) | Quick lookup for all pipeline, testing, operational commands and environment variables |
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
