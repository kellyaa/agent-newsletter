# Strategy

Where to look to see what the project is aiming at, what is planned, and what automated contributors have been doing. This is an index, not a plan: it holds no priorities of its own, so it cannot go stale against the sources below. Satisfies the ACMM `acmm:strategic-dashboard` criterion (`docs/strategy.md`). There is no hosted dashboard.

| Question | Where |
| --- | --- |
| What is the product and how is it designed? | [`SPEC.md`](../SPEC.md) |
| What is proposed next, and what needs a maintainer decision? | [`ROADMAP.md`](../ROADMAP.md) (written by the strategist agent; a proposal, not a commitment) |
| What is the definition of done for a piece of work? | The issue tracker, which stays the source of truth (see `SPEC.md` Iteration Plan) |
| Is the project healthy right now? | [quality.md](quality.md) and `.claude/health-check.md` |
| What did automated contributors change, and under what rules? | Pull requests labelled `hold` (reviewed and merged by a human); rules in [agent-boundaries.md](agent-boundaries.md) and [risk-tiers.md](risk-tiers.md) |
| What did we learn from incidents and reviews? | [reflections/](reflections/README.md) and `.memory/corrections.jsonl` |
| How do I respond to an incident? | [ai-ops-runbook.md](ai-ops-runbook.md) |

## Keeping it current

`ROADMAP.md` is the only place priorities are written down. Change it there, not here. Add a row to this table only when a new source of strategic information appears.
