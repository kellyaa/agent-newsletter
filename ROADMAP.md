# Roadmap

Planning artifact from the strategist agent. It proposes priorities and is not a commitment. The issue tracker stays the source of truth for definition-of-done (see SPEC.md §Iteration Plan). Maintainers decide what lands here.

## Where the project is

- Two editions are `live`: `agents` and `engineering-with-ai` (`editions/*/edition.yaml`). `run.sh` runs every non-paused edition, one after another, in a single unattended daily run.
- Multi-edition support (epic #291) has shipped. The scaffolding (`new_edition.py`, `check_edition.py`), the edition-aware site, and the operations guides are in place.
- Recent work has been hardening: refactors that share helpers and constants, test tightening, and docs guides.

## Near term

1. **Cost control.** `BUDGET_USD` and `runs.cost_usd` are scaffolded but not enforced or populated, and spend now scales with edition count. Tracked in #438.
2. **CI security scanning.** CodeQL, pip-audit and pnpm audit are specified in #463 (actionable, with the workflow text). #433 is closed, and #425 is an older parked duplicate that can be closed once #463 lands. The change is a new file under `.github/workflows/`, which agents cannot push, so a human has to apply it. #465 only documents that requirement.
3. **Finish the SPEC.md cost wording.** The "Live status" refresh with measured per-edition baselines has merged (#462). The cost wording is still stale: the risk table, Cost Budget bullets and the Cost cap decision say `cost_usd` is not populated. Update them after #441 and the #438 decision land. Tracked in #444.

## Mid term

- **Edition-count readiness.** Total run time is the sum of the editions' times (editions are not parallel). Decide at what edition count this needs attention, using measured per-edition wall time.
- **Per-edition cost visibility.** The repository records no per-edition cost today (`docs/multi-edition-operations.md`). This follows from item 1 above.

## Open questions for maintainers

- Should a budget cap apply per run, per edition, or per day across editions?
- Is there a planned third edition? That would raise the priority of cost control and runtime.

## Out of scope here

Feature implementation. Strategist proposals become issues, which scanner, quality and architect agents or humans then implement.
