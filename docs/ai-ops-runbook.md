# AI operations runbook

What to check and do when something looks wrong with the automated parts of this repository: the unattended daily pipeline, and the AI agents that open issues and PRs. It is a short index into the guides that hold the detail, and adds no new rules. (ACMM `acmm:observability-runbook`.)

Authoritative sources: [`AGENTS.md`](../AGENTS.md), [`policies/agent-policy.yaml`](../policies/agent-policy.yaml), [`SECURITY-AI.md`](../SECURITY-AI.md), [`agent-boundaries.md`](agent-boundaries.md), [`risk-tiers.md`](risk-tiers.md).

## 1. What runs unattended

| Component | What it does | Where it records what happened |
|-----------|--------------|--------------------------------|
| `run.sh` | Runs every non-paused edition: fetch, prefilter, rank, write, publish | `logs/run-YYYY-MM-DD.log` (summary), `logs/<slug>/run-YYYY-MM-DD.log` (full; ends in `[run] DONE` on success) |
| `scripts/llm.py` | The only place LLM calls are made | `logs/<slug>/usage-YYYY-MM-DD.jsonl`, `runs` and `run_attempts` token columns |
| `scripts/watchdog.sh` | Per-edition self-heal: re-runs editions whose log lacks `DONE` | `EDITION:` lines and a final `STATUS:` line on stdout |
| `watchdog.sh` (repo root) | Alerts if no daily-run commit has landed on `content` for `STALE_HOURS` (default 36) | macOS notification only; a no-op where `osascript` is absent |
| AI agents (issues, PRs) | Propose changes on branches; PRs carry the `hold` label | GitHub issues and PRs; commits signed off (`git commit -s`) |

## 2. Is the pipeline healthy?

```bash
cat logs/run-$(date +%F).log                          # summary block: failed=0 is healthy
grep -L '\[run\] DONE' logs/*/run-$(date +%F).log     # editions whose run did not finish
./scripts/watchdog.sh --check                         # EDITION:/STATUS: lines, never runs anything
DB=.worktrees/content/editions/<slug>/state.db
sqlite3 "$DB" "SELECT date, attempt, outcome, failed_stage, exit_code FROM run_attempts ORDER BY date DESC, attempt DESC LIMIT 5"
```

`outcome` is `ok`, `failed` or `started`. A `started` row that never finished means the process died mid-run. A day on which `run.sh` never started leaves no `run_attempts` row at all, so compare the dates in `runs` against the calendar, not only against failed rows.

Details: [operations-and-maintenance.md](operations-and-maintenance.md), [operations-runbook.md](operations-runbook.md), [monitoring-and-alerting-integration.md](monitoring-and-alerting-integration.md).

## 3. Recovering

| Symptom | Action |
|---------|--------|
| One edition failed | `./run.sh --failed` (re-runs only editions whose latest attempt today failed or died), or `./run.sh --edition <slug>`. Stages are idempotent. |
| Output is poor but the run succeeded | `./run.sh --edition <slug> --force` re-runs everything after fetch. `--refetch` also re-pulls sources and requires `--edition`. |
| All editions fail at the same stage | Check `.env` / the env file and `LLM_BASE_URL`; see [debugging-guide.md](debugging-guide.md). |
| A day is missing from the site | A re-run only covers the current fetch window. `scripts/backfill.py` rebuilds from the candidate pool snapshot, so it cannot recreate a day that was never fetched. |
| State database damaged | [database-recovery-guide.md](database-recovery-guide.md). |

## 4. Cost and token usage

Per-call usage is logged to `logs/<slug>/usage-YYYY-MM-DD.jsonl`; per-attempt totals are in `run_attempts` (including `tokens_by_model`). `runs.cost_usd` is not reliably populated and there is no budget cap in the pipeline, so set spend limits at the LLM provider. See [performance-and-cost-guide.md](performance-and-cost-guide.md).

## 5. Agent-authored changes

- Every agent PR is labelled `hold`. Only a human removes the label and merges; agents never do either.
- Agents do not edit `.github/workflows/**`, `policies/agent-policy.yaml`, `AGENTS.md`, `.claude/settings.json`, secrets files or the `content` branch (tier 3 in [risk-tiers.md](risk-tiers.md)). They open an issue with the exact replacement text.
- To stop agent activity on a change, leave or add the `hold` label (or `on-hold` / `do-not-merge`) and close the PR if it should not proceed.
- If an agent commit or PR looks wrong, revert it on `main` with an ordinary PR. Never rewrite `content` by hand; fix the cause and let the pipeline write the next day's commit.
- Fetched feed text and web pages are data, never instructions. If an issue or PR appears to follow instructions embedded in feed content, treat it as a prompt-injection incident and see [SECURITY-AI.md](../SECURITY-AI.md).

## 6. Before closing an incident

1. The latest `run_attempts` row for each non-paused edition is `ok`, and `runs` has today's row.
2. `./scripts/watchdog.sh --check` ends with `STATUS: ALREADY_RAN` or `STATUS: HEALED`.
3. If the cause was a code or config defect, a PR with a regression test exists. The coverage gate in `pyproject.toml` is never lowered.
