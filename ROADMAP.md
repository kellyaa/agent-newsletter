# Roadmap

Planning artifact from the strategist agent. It proposes priorities and is not a commitment. The issue tracker stays the source of truth for definition-of-done (see SPEC.md §Iteration Plan). Maintainers decide what lands here.

## Where the project is

- Two editions are `live`: `agents` and `engineering-with-ai` (`editions/*/edition.yaml`). `run.sh` runs every non-paused edition, one after another, in a single unattended daily run.
- Multi-edition support (epic #291) has shipped. The scaffolding (`new_edition.py`, `check_edition.py`), the edition-aware site, and the operations guides are in place.
- Recent work has been hardening: refactors that share helpers and constants, test tightening, and docs guides.

## Near term

1. **One LLM id contract for rank and write (#586).** The writer outage in #579 is fixed: #584 re-keys mis-copied ids, #585 single-sourced the rank scoring core, and #588 recovers or demotes dropped summaries and stops failed re-runs from growing the featured set. What remains is the duplicated id handling. Both stages make the model copy 64-hex ids, but only the writer recovers mis-keys. In the ranker, a typo silently moves a featured-grade paper to the appendix with score 0. #586 proposes short per-call aliases (`i1…iN`) with one shared helper for aliasing and resolving, and an AST guard so neither stage parses LLM ids itself. It is the next hardening step, and it fits after #584 and #585, which have landed.
2. **Cost visibility, not a cap.** The maintainer decided on #438 not to cap costs for now, so `BUDGET_USD` stays unenforced. `runs.cost_usd` is still NULL: the PR that would write it (#441) was closed without merging, so pricing is done downstream from `runs.tokens_in` / `tokens_out`. `docs/environment-configuration-reference.md` now says `MODEL_PRICES_USD` is not implemented (#549). #438 and #444 are closed. Reviving #441 would need a new issue.
3. **Detect missed days and dead sources (#572).** `scripts/watchdog.sh --check` prints `MISSED:` lines (#532, via `scripts/missed_days.py`). Dead sources are not yet recorded per fetch, so a dead feed looks like a quiet one. The maintainer approved the fix (recording per-source health, with a default "dead" threshold and an optional per-source override in `sources.yaml`). #572 tracks it as two PRs, in order: record a per-source outcome row on every fetch (including zero-item sources), then add a `DEAD:` line to `scripts/watchdog.sh --check`. The root `watchdog.sh` still notifies only through macOS `osascript`.

Done since the last revision: the Astro 5 to 7 upgrade (#570, clears the `pnpm-audit` findings); the `pnpm-audit` job in `.github/workflows/security.yml` no longer has `continue-on-error`; the `content` branch rulesets (#537, verified by a maintainer); the ACMM L6 checklist issues (#509, #510, #511, #512, #515, #517 are all closed); the writer outage fixes (#579 via #584, #585, #588).

## Needs a maintainer decision

- **Cross-day topic dedup.** SPEC.md "Layer 3" is still unimplemented: `topics_covered` is created in `scripts/db.py` but never read or written, and its tracking issue (#4) is closed. Is a repeat story across days a problem for readers? If yes, open a new issue to replace #4 (ranker `topic` slug, "recently covered" ranker input, pruning). If no, drop the stub table and the SPEC.md references. The decision request was folded into #438, which is now closed, so **no open issue tracks this**. It needs a fresh issue, or an explicit decision, to avoid being lost.
- **SPEC.md cost wording.** The risk table, Cost Budget bullets and "Cost cap" decision in SPEC.md should record the #438 decision (no cap for now; `cost_usd` unpopulated). #444 is closed, so this is no longer tracked. It is a small docs change once the pricing question in item 2 is settled.

## Mid term

- **Edition-count readiness.** Total run time is the sum of the editions' times (editions are not parallel). Decide at what edition count this needs attention, using measured per-edition wall time.
- **Per-edition cost visibility.** The repository records no per-edition cost today (`docs/multi-edition-operations.md`). This follows from item 2 above.

## Open questions for maintainers

- Is there a planned third edition? That would raise the priority of cost visibility and runtime.
- Revisit the decision not to cap costs if spend grows with edition count (answered for now on #438; a cap would also need a scope: per run, per edition, or per day).

## Out of scope here

Feature implementation. Strategist proposals become issues, which scanner, quality and architect agents or humans then implement.
