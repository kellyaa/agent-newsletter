# Roadmap

Planning artifact from the strategist agent. It proposes priorities and is not a commitment. The issue tracker stays the source of truth for definition-of-done (see SPEC.md §Iteration Plan). Maintainers decide what lands here.

## Where the project is

- Two editions are `live`: `agents` and `engineering-with-ai` (`editions/*/edition.yaml`). `run.sh` runs every non-paused edition, one after another, in a single unattended daily run.
- Multi-edition support (epic #291) has shipped. The scaffolding (`new_edition.py`, `check_edition.py`), the edition-aware site, and the operations guides are in place.
- Recent work has been hardening: refactors that share helpers and constants, test tightening, and docs guides.

## Near term

1. **Cost visibility, not a cap.** The maintainer decided on #438 not to cap costs for now, so `BUDGET_USD` stays unenforced. `runs.cost_usd` is still NULL: the PR that would write it (#441) was closed without merging, so pricing is done downstream from `runs.tokens_in` / `tokens_out`. `docs/environment-configuration-reference.md` says `MODEL_PRICES_USD` is not implemented (#549). #438 and #444 are closed. Reviving #441 would need a new issue.
2. **Notify on `MISSED:` / `DEAD:` outside macOS.** Both checks now exist in `scripts/watchdog.sh --check` (`scripts/missed_days.py`, `scripts/dead_sources.py`), but they only print and are informational. The root `watchdog.sh` still notifies only through macOS `osascript`, so a Linux host gets no alert. #572 listed a non-macOS notifier as an optional, separate change; it is now tracked in #600. It needs a maintainer decision on whether Linux hosts matter (README says "Mac (or Linux)") and on the channel.

Done since the last revision: one LLM id contract for rank and write (#586 via #592, short per-call aliases); per-source fetch outcomes in `state.db` (#572 step 1, #593) and the `DEAD:` watchdog check with the `dead_after_days` override (#572 step 2, #594); the writer outage fixes (#579 via #584, #585, #588); the Astro 5 to 7 upgrade (#570); the `content` branch rulesets (#537); the ACMM L6 checklist issues (#509, #510, #511, #512, #515, #517).

## Needs a maintainer decision

- **Cross-day topic dedup.** SPEC.md "Layer 3" is still unimplemented: `topics_covered` is created in `scripts/db.py` but never read or written, and its tracking issue (#4) is closed. Is a repeat story across days a problem for readers? If yes, replace #4 with implementation issues (ranker `topic` slug, "recently covered" ranker input, pruning). If no, drop the stub table and the SPEC.md references. The decision request is tracked in #599. A last-month study there (both editions) found about 4 cross-day topic repeats per edition (about 3% of `agents` featured slots); the `engineering-with-ai` 10-24/10-25 duplicate came from the `breaking-changes` to `engineering-with-ai` rename and state.db migration (#325), not from a dedup gap. Within-day clustering (3+ featured items on one story) is the more visible problem and `topics_covered` would not fix it. The strategist recommends dropping the stub, with an optional within-day rule in `prompts/rank.md`. It is waiting on a maintainer decision.
- **SPEC.md cost wording.** The risk table, Cost Budget bullets and "Cost cap" decision in SPEC.md should record the #438 decision (no cap for now; `cost_usd` unpopulated). #444 is closed, so this is no longer tracked. It is a small docs change once the pricing question in item 2 is settled.

## Mid term

- **Edition-count readiness.** Total run time is the sum of the editions' times (editions are not parallel). Decide at what edition count this needs attention, using measured per-edition wall time.
- **Per-edition cost visibility.** The repository records no per-edition cost today (`docs/multi-edition-operations.md`). This follows from item 2 above.

## Open questions for maintainers

- Is there a planned third edition? That would raise the priority of cost visibility and runtime.
- Revisit the decision not to cap costs if spend grows with edition count (answered for now on #438; a cap would also need a scope: per run, per edition, or per day).

## Out of scope here

Feature implementation. Strategist proposals become issues, which scanner, quality and architect agents or humans then implement.
