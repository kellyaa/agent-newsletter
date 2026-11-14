# Documentation Index

Comprehensive guide to all resources for users, maintainers, and contributors to the agent-newsletter project.

## Getting Started

- **[Getting Started Guide](./getting-started-guide.md)** — First-time setup, environment variables, running your first edition
- **[Command Reference](./command-reference.md)** — Quick reference for all CLI commands (uv, scripts, shell)
- **[CONTRIBUTING.md](../CONTRIBUTING.md)** — How to contribute: Git workflow, testing, PR process

## For Maintainers and Operators

### Running and Monitoring

- **[Operations Runbook](./operations-runbook.md)** — Daily operations, incident response, common tasks
- **[Run Strategies and Recovery](./run-strategies-and-recovery.md)** — Different run modes, failure scenarios, recovery steps
- **[Monitoring and Alerting Integration](./monitoring-and-alerting-integration.md)** — Watchdog behavior, notification channels, health checks
- **[Multi-Edition Operations](./multi-edition-operations.md)** — Managing multiple live editions, cost accounting, runtime

### Troubleshooting

- **[Debugging Guide](./debugging-guide.md)** — Inspecting state.db, analyzing LLM responses, tracing issues
- **[Troubleshooting Low Featured Item Count](./troubleshooting-low-featured-count.md)** — Common causes and fixes
- **[LLM Error Handling Guide](./llm-error-handling-guide.md)** — Understanding LLM failures, retry logic, token issues
- **[Database Recovery Guide](./database-recovery-guide.md)** — Repairing corrupted state.db, recovery procedures

### Configuration and Tuning

- **[Environment Configuration Reference](./environment-configuration-reference.md)** — All environment variables, model pricing, API keys
- **[Edition YAML Reference](./edition-yaml-reference.md)** — Edition config syntax, keywords, status values
- **[Edition Tuning Guide](./edition-tuning-guide.md)** — How to adjust an edition for better results

## For Developers

### Architecture and Design

- **[SPEC.md](../SPEC.md)** — Canonical design document; architecture, layers, contracts, iteration plan
- **[Site Architecture](./site-architecture.md)** — Astro site structure, component organization, build process
- **[Repository Structure](../README.md)** — Scripts, editions, tests, and file layout

### Extending and Customizing

- **[Adding Feed Sources](./adding-feed-sources-guide.md)** — How to add RSS, arXiv, HN, Reddit, or HTML sources
- **[Extending Pipeline Sources](./extending-pipeline-sources.md)** — Implementing new fetch strategies beyond built-in sources
- **[Customizing Ranking Rubric](./customizing-ranking-rubric.md)** — LLM ranking behavior, thresholds, section rules
- **[Prompt Slots Guide](./prompt-slots-guide.md)** — Writing and filling `{{slot}}` templates

### Implementation Guides

- **[Code Patterns Guide](./code-patterns-guide.md)** — Recommended patterns for pipeline stages, testing, error handling
- **[Testing Patterns Guide](./testing-patterns-guide.md)** — Unit tests, integration tests, coverage gates
- **[LLM Integration Guide](./llm-integration-guide.md)** — How LLM calls work, token budgets, model selection
- **[Shell Architecture Guide](./shell-architecture-guide.md)** — Understanding the shell orchestration layer (contracts, worktrees, locking)
- **[Site Development Guide](./site-development-guide.md)** — Astro setup, component development, styling

### DevOps and Infrastructure

- **[Dependency Management Guide](./dependency-management-guide.md)** — Python (uv), JavaScript (pnpm), updating versions
- **[Repository Branches and Worktrees](./repository-branches-and-worktrees.md)** — Content branch, git worktrees, rebase strategies

### Data and Performance

- **[Database Schema Guide](./database-schema-guide.md)** — state.db schema, queries, record layout
- **[SQL Export and Analysis](./sql-export-and-analysis-guide.md)** — Extracting metrics, writing analytical queries
- **[Metrics](./metrics.md)** — Available metrics, how they're calculated, dashboards
- **[Performance and Cost Guide](./performance-and-cost-guide.md)** — Token budgets, wall time, per-edition costs
- **[Source Reliability Matrix](./source-reliability-matrix.md)** — Feed health, uptime, error rates per source

### Reference and Standards

- **[Agent Boundaries](./agent-boundaries.md)** — What each automated agent can/cannot do; rules of engagement
- **[Risk Tiers](./risk-tiers.md)** — Risk classification for commits, branches, and changes
- **[Prefilter Gates Guide](./prefilter-gates-guide.md)** — Technical reference for gate logic
- **[Ranking Rubric](./ranking-rubric.md)** — Detailed scoring mechanics and section rules

## For Quality and Review

- **[Review Rubric](./review-rubric.md)** — How to evaluate pull requests and contributions
- **[Quality](./quality.md)** — Quality standards, test coverage requirements, linting rules
- **[AGENTS.md](../AGENTS.md)** — Rules for AI agents; import boundaries, constants locations, contracts

## Strategic Resources

- **[Strategy](./strategy.md)** — Long-term vision, roadmap context, decision log
- **[ROADMAP.md](../ROADMAP.md)** — Near-term priorities, maintainer decisions, milestones
- **[AI Ops Runbook](./ai-ops-runbook.md)** — Managing AI agent workflows, decision logs, cost analysis

## Advanced Topics

### Multi-Agent and Analytics

- **[Multi-Agent Coordination](./multi-agent.md)** — How fetch, rank, write, and publish stages interact

### Governance and Policy

- **[SECURITY-AI.md](../SECURITY-AI.md)** — Security considerations for AI-generated content
- **[LICENSE](../LICENSE)** — Licensing information

## Index by Topic

### Setup and First Run
1. Start with [Getting Started Guide](./getting-started-guide.md)
2. Review [Environment Configuration Reference](./environment-configuration-reference.md)
3. Try [Command Reference](./command-reference.md)

### Running and Operating
1. [Operations Runbook](./operations-runbook.md)
2. [Run Strategies and Recovery](./run-strategies-and-recovery.md)
3. [Monitoring and Alerting Integration](./monitoring-and-alerting-integration.md)

### Building a New Edition
1. [Edition YAML Reference](./edition-yaml-reference.md)
2. [Edition Tuning Guide](./edition-tuning-guide.md)
3. [Customizing Ranking Rubric](./customizing-ranking-rubric.md)

### Adding Data Sources
1. [Adding Feed Sources](./adding-feed-sources-guide.md)
2. [Source Reliability Matrix](./source-reliability-matrix.md)
3. [Extending Pipeline Sources](./extending-pipeline-sources.md)

### Development and Extension
1. [SPEC.md](../SPEC.md) — Read first
2. [Code Patterns Guide](./code-patterns-guide.md)
3. [LLM Integration Guide](./llm-integration-guide.md)
4. [Testing Patterns Guide](./testing-patterns-guide.md)

### Troubleshooting
1. [Debugging Guide](./debugging-guide.md)
2. [LLM Error Handling Guide](./llm-error-handling-guide.md)
3. [Troubleshooting Low Featured Item Count](./troubleshooting-low-featured-count.md)

---

*Last updated: 2026-11-12*
*For the most current guidance, see ROADMAP.md and AGENTS.md*
