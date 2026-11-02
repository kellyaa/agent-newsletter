# Roadmap

Planning artifact from the strategist agent. It proposes priorities and is not a commitment. The issue tracker stays the source of truth for definition-of-done (see SPEC.md §Iteration Plan). Maintainers decide what lands here.

## Where the project is

- Two editions are `live`: `agents` and `engineering-with-ai` (`editions/*/edition.yaml`). `run.sh` runs every non-paused edition, one after another, in a single unattended daily run.
- Multi-edition support (epic #291) has shipped. The scaffolding (`new_edition.py`, `check_edition.py`), the edition-aware site, and the operations guides are in place.
- Recent work has been hardening: refactors that share helpers and constants, test tightening, and docs guides.

## Near term

1. **Cost visibility, not a cap.** The maintainer decided on #438 not to cap costs for now, so `BUDGET_USD` stays unenforced. What remains is recording priced cost: `runs.cost_usd` is still NULL until #441 (held) lands. Tracked in #438.
2. **Close out security scanning.** The CodeQL, pip-audit and pnpm audit workflow merged in #484 (`.github/workflows/security.yml`). The parked issues #425 and #468 (and the tracker #466) still ask for the same thing and need a maintainer to confirm and close them. #463, #433 and #465 are already closed.
3. **Finish the SPEC.md cost wording.** The "Live status" refresh with measured per-edition baselines has merged (#462). The cost wording is still stale: the risk table, Cost Budget bullets and the Cost cap decision say `cost_usd` is not populated, and "Cost cap" should record the #438 decision. Update them after #441 lands. Tracked in #444.

## Mid term

- **Edition-count readiness.** Total run time is the sum of the editions' times (editions are not parallel). Decide at what edition count this needs attention, using measured per-edition wall time.
- **Per-edition cost visibility.** The repository records no per-edition cost today (`docs/multi-edition-operations.md`). This follows from item 1 above.

## Open questions for maintainers

- Is there a planned third edition? That would raise the priority of cost visibility and runtime.
- Revisit the decision not to cap costs if spend grows with edition count (answered for now on #438; a cap would also need a scope: per run, per edition, or per day).

## Out of scope here

Feature implementation. Strategist proposals become issues, which scanner, quality and architect agents or humans then implement.
