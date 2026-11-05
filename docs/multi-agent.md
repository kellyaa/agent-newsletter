# Multi-agent orchestration

How several AI agents work in this repository at once without colliding. It adds no new rules; the authoritative sources are [`AGENTS.md`](../AGENTS.md), [`policies/agent-policy.yaml`](../policies/agent-policy.yaml) and [`agent-boundaries.md`](agent-boundaries.md). (ACMM `acmm:multi-agent-orchestration`.)

## Roles

| Role | Does | Never does |
|------|------|------------|
| Scanner / fixer | Takes a queued issue, works on its own branch, opens a PR labelled `hold` | Merges, removes `hold`, edits `.github/workflows/**` |
| Reviewer (human) | Reviews the PR, removes `hold`, merges | — |
| Daily pipeline | Owns the `content` branch | — |

Agents are dispatched from the issue queue by the hive that manages this repository; the repository itself carries no dispatcher code.

## Avoiding collisions

- **One issue, one agent.** An agent marks an issue in progress before starting, and leaves issues that another agent has already claimed.
- **One branch per task**, cut from a fresh `origin/main`, never from another agent's branch.
- **Check open PRs first.** If an open PR already touches the same files or issue, pick different work or stand down; do not write a second implementation.
- **Stay in your lane.** Agents never commit to `content`, and never edit files listed under `agent_forbidden` in the policy; they file an issue with the exact replacement text instead.
- **Stage scripts stay independent.** Pipeline stages share state only through `state.db` and shared helpers, so parallel changes to different stages do not conflict (`tests/test_stage_import_boundary.py`).

## Handoff

Work moves between agents and humans through GitHub objects only: issue → branch → `hold` PR → human merge. Each step leaves a GitHub record, so the chain from task to merge can be reconstructed from GitHub alone. A held PR whose head moves after review needs fresh human review.
