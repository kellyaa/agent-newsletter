# Roadmap

Planning artifact from the strategist agent. It proposes priorities and is not a commitment. The issue tracker stays the source of truth for definition-of-done (see SPEC.md §Iteration Plan). Maintainers decide what lands here.

## Where the project is

- Two editions are `live`: `agents` and `engineering-with-ai` (`editions/*/edition.yaml`). `run.sh` runs every non-paused edition, one after another, in a single unattended daily run.
- Multi-edition support (epic #291) has shipped. The scaffolding (`new_edition.py`, `check_edition.py`), the edition-aware site, and the operations guides are in place.
- Recent work has been hardening: refactors that share helpers and constants, test tightening, and docs guides.

## Near term

1. **Cost visibility, not a cap.** The maintainer decided on #438 not to cap costs for now, so `BUDGET_USD` stays unenforced. What remains is recording priced cost: `runs.cost_usd` is still NULL until #441 (held) lands. Tracked in #438.
2. **Close out security scanning.** The CodeQL, pip-audit and pnpm audit workflow merged in #484 (`.github/workflows/security.yml`). The parked issues #425 and #468 (and the tracker #466) still ask for the same thing and need a maintainer to confirm and close them. #463, #433 and #465 are already closed. The `pnpm-audit` job runs with `continue-on-error` because 16 findings remain, so the scan does not yet gate CI. Clearing them is item 3.
3. **Astro 5 to 7 upgrade.** #485 tracks it (claimed by scanner). It clears the remaining `pnpm-audit` findings, including a critical one in astro. Once the audit is clean, `continue-on-error` can be removed. It spans two major versions, so it needs the site build and site tests to pass.
4. **Finish the SPEC.md cost wording.** The "Live status" refresh with measured per-edition baselines has merged (#462). The cost wording is still stale: the risk table, Cost Budget bullets and the Cost cap decision say `cost_usd` is not populated, and "Cost cap" should record the #438 decision. Update them after #441 lands. Tracked in #444.
5. **Detect missed days and dead sources.** No issue was published on 2026-10-31 in either edition and nothing flagged it: `run_attempts` records failures, not absences, and the root `watchdog.sh` only notifies through macOS `osascript`. Per-source fetch failures are also not recorded, so a dead feed looks like a quiet one. Both are tracked in #519 (the per-source variant was folded into it). Needs a maintainer decision on whether to recover or accept the 10-31 gap and which off-macOS channel to report through, then one shared reporting path.

## Needs a maintainer decision

- **Cross-day topic dedup.** SPEC.md "Layer 3" is still unimplemented: `topics_covered` is created in `scripts/db.py` but never read or written, and its tracking issue (#4) is closed. Is a repeat story across days a problem for readers? If yes, reopen or replace #4 (ranker `topic` slug, "recently covered" ranker input, pruning). If no, drop the stub table and the SPEC.md references. This was raised in a strategist finding folded into #438 and has no issue of its own yet.

## Mid term

- **Edition-count readiness.** Total run time is the sum of the editions' times (editions are not parallel). Decide at what edition count this needs attention, using measured per-edition wall time.
- **Per-edition cost visibility.** The repository records no per-edition cost today (`docs/multi-edition-operations.md`). This follows from item 1 above.

## Open questions for maintainers

- Is there a planned third edition? That would raise the priority of cost visibility and runtime.
- Revisit the decision not to cap costs if spend grows with edition count (answered for now on #438; a cap would also need a scope: per run, per edition, or per day).

## Out of scope here

Feature implementation. Strategist proposals become issues, which scanner, quality and architect agents or humans then implement.
