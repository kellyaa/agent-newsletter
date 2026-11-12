# Roadmap

Planning artifact from the strategist agent. It proposes priorities and is not a commitment. The issue tracker stays the source of truth for definition-of-done (see SPEC.md §Iteration Plan). Maintainers decide what lands here.

## Where the project is

- Two editions are `live`: `agents` and `engineering-with-ai` (`editions/*/edition.yaml`). `run.sh` runs every non-paused edition, one after another, in a single unattended daily run.
- Multi-edition support (epic #291) has shipped. The scaffolding (`new_edition.py`, `check_edition.py`), the edition-aware site, and the operations guides are in place.
- Recent work has been hardening: refactors that share helpers and constants, test tightening, and docs guides. The latest run single-sources more contracts in `scripts/models.py` and a few shared modules, each pinned by an AST guard test and an `AGENTS.md` rule (#636, #640, #642, #644, #646, #649, #651, #653, #656, #659, #663, #666).

## Near term

1. **Cost visibility, not a cap.** The maintainer decided on #438 not to cap costs for now, so `BUDGET_USD` stays unenforced. `runs.cost_usd` is still NULL: the PR that would write it (#441) was closed without merging, so pricing is done downstream from `runs.tokens_in` / `tokens_out`. `docs/environment-configuration-reference.md` says `MODEL_PRICES_USD` is not implemented (#549). #438 and #444 are closed. Reviving #441 would need a new issue.

Done since the last revision: the non-macOS watchdog notifier (#600 via #615: `notify-send` on Linux desktops, optional `WATCHDOG_WEBHOOK_URL` POST for headless hosts, no-op otherwise; #658 via #659 then moved `notify` into `scripts/runlog.sh`, so the `<edition> failed` and `Newsletter pipeline failed` alerts from `run_edition.sh` / `run.sh` reach the same channels; the `MISSED:` / `DEAD:` lines from `scripts/watchdog.sh --check` still only print); one LLM id contract for rank and write (#586 via #592, short per-call aliases); per-source fetch outcomes in `state.db` (#572 step 1, #593) and the `DEAD:` watchdog check with the `dead_after_days` override (#572 step 2, #594); the writer outage fixes (#579 via #584, #585, #588); the Astro 5 to 7 upgrade (#570); the `content` branch rulesets (#537); the ACMM L6 checklist issues (#509, #510, #511, #512, #515, #517); the single-source contract series (items.source prefix #636, issue filename #640, ISO-8601 instant parsing #642, log format #644, run-log DONE/FAILED marker #646, edition.yaml keyword/slug rules #648 via #649, `{{slot}}` prompt syntax #650 via #651, papers pre-rank cap #652 via #653, issue frontmatter contract #655 via #656, the shared `notify` helper #658 via #659, the single-run lock #662 via #663: `scripts/runlock.sh` is the only home of the `.watchdog*.lock` paths and `flock` calls, and a missing `flock` now makes `run.sh` exit 1 with an alert and the watchdog print `STATUS: FAILED <date> no-flock`, instead of reading as "run in flight"); the content-branch shell contract #665 via #666 (`scripts/contentbranch.sh` is the only home of the `.worktrees/content` worktree path, the `newsletter(<slug>): ...` run-commit subjects and `CONTENT_DAILY_RUN_RE`, so the root `watchdog.sh` regex is guarded against drifting from what `run_edition.sh` writes); test tightening on backfill's partial-promotion warning boundary (#660); the SPEC.md cost wording now records the #438 no-cap decision (#654); the cross-day topic dedup decision, which was `drop` (#619 via #628: the unused `topics_covered` stub table is removed and SPEC.md "Layer 3" records that topic-level dedup is intentionally not implemented; the optional within-day clustering rule in `prompts/rank.md` was not chosen).

## Needs a maintainer decision

Nothing is waiting on a maintainer right now. The SPEC.md cost wording that was listed here landed in #654.

## Mid term

- **Edition-count readiness.** Total run time is the sum of the editions' times (editions are not parallel). Decide at what edition count this needs attention, using measured per-edition wall time.
- **Per-edition cost visibility.** The repository records no per-edition cost today (`docs/multi-edition-operations.md`). This follows from near-term item 1 above.

## Open questions for maintainers

- Is there a planned third edition? That would raise the priority of cost visibility and runtime.
- Revisit the decision not to cap costs if spend grows with edition count (answered for now on #438; a cap would also need a scope: per run, per edition, or per day).

## Out of scope here

Feature implementation. Strategist proposals become issues, which scanner, quality and architect agents or humans then implement.
