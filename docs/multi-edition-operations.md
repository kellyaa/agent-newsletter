# Multi-Edition Operations

Running more than one edition from one checkout: what is shared, what is per
edition, how the daily run behaves with N editions, and how to handle the common
situations. It builds on [edition-yaml-reference.md](edition-yaml-reference.md) and
the "Adding an edition" section of [CONTRIBUTING.md](../CONTRIBUTING.md), and on
[debugging-guide.md](debugging-guide.md) for triage.

Statements below are taken from `run.sh`, `scripts/run_edition.sh`,
`scripts/edition.py`, `scripts/watchdog.sh` and the deploy workflow. Where the
repository records no data (for example per-edition cost) this guide says so rather
than inventing numbers.

## 1. Architecture: independent and shared

### Execution model

`run.sh` is the orchestrator. It does the once-per-day work, then runs **each edition
in its own child process, one after another**:

```
run.sh
 ├─ single-run lock (.watchdog.lock, flock)
 ├─ load env files, self-update (git pull --ff-only), content worktree, layout migration
 ├─ pick editions (status != paused, ordered by `order` then slug)
 └─ for each edition:  EDITION=<slug> scripts/run_edition.sh
        fetch → prefilter → rank → write → publish → commit + push editions/<slug>/
```

- **Editions do not run in parallel.** There is one lock for the whole run. Total wall
  time is the sum of the editions' times, and the schedule is a single launchd job
  (07:00 daily in the shipped plist).
- **Failure is isolated.** A failing edition is logged, notifies, records a failed
  attempt, and the loop carries on. `run.sh` exits non-zero at the end if any edition
  failed, but later editions still ran.
- **Each edition publishes as soon as it finishes.** Its `editions/<slug>/` folder is
  committed and pushed to the `content` branch immediately, so a later edition's
  failure cannot hold it back.
- **Idempotent.** Re-running `./run.sh` skips editions whose latest attempt today is
  `ok`, so a repeat is cheap.

### Shared vs per edition

| Shared by every edition | Per edition |
|---|---|
| Pipeline code (`scripts/`) | `editions/<slug>/edition.yaml`: identity, reader profile, keywords, tags, section thresholds, `status`, `order` |
| Base prompts (`prompts/rank.md`, `prompts/write.md`) | `editions/<slug>/prompts/<stage>.<slot>.md` fragments ([prompt-slots-guide.md](prompt-slots-guide.md)) |
| LLM endpoint and credentials (`LLM_BASE_URL`, `LLM_API_KEY`) | `editions/<slug>/sources.yaml` |
| `RANKER_MODEL`, `WRITER_MODEL`, timeouts, token caps | `editions/<slug>/state.db` (items, runs, run_attempts) on the `content` branch |
| The `.env` / `~/.config/agent-newsletter/env` files | `editions/<slug>/issues/YYYY-MM-DD.md` on the `content` branch |
| The site and deploy workflow | `logs/<slug>/` (run log, usage log, `candidates.json`, `ranked.json`, rendered prompts) |
| The single-run lock | Run-attempt history and the fetched-today marker |

Two consequences worth knowing:

1. **Models and LLM settings are global.** There is no per-edition model setting in
   `edition.yaml` or in the scripts. All editions use the same `RANKER_MODEL` and
   `WRITER_MODEL`. If one edition needs a different model, that needs a code change;
   running `EDITION=x RANKER_MODEL=... ./scripts/run_edition.sh` by hand works for
   experiments but is not part of the scheduled run.
2. **State is fully separate.** Each edition has its own `state.db`, so dedup,
   candidate pools and history do not cross editions. The same URL can appear in two
   editions independently.

## 2. Configuration strategy

- Put everything subject-specific in the edition folder: keywords, tags, reader
  profile, sections, sources, prompt fragments.
- Put machine-wide settings (endpoint, key, models, timeouts) in
  `~/.config/agent-newsletter/env` and repo-local overrides in `.env`. `run.sh`
  sources them in that order and the last file wins.
- Do not copy the base prompts per edition; use fragments, which keeps one place to
  fix prompt bugs (see [prompt-slots-guide.md](prompt-slots-guide.md)).
- Start a new edition with `scripts/new_edition.py <slug>`, which scaffolds from
  `editions/_template/`, and validate with `scripts/check_edition.py <slug>`. See
  CONTRIBUTING.md, "Adding an edition".
- Choose `order` with gaps (10, 20, 30). It sets both the run order and the homepage
  order. A slow or flaky edition should get a high `order` so it does not delay the
  others.

### `status` as the operating switch

| Status | Daily `./run.sh` | `./run.sh --edition x` | Site |
|---|---|---|---|
| `draft` | runs, notifies on failure | runs | hidden unless `SHOW_DRAFTS=1` |
| `live` | runs | runs | listed |
| `paused` | **skipped** | runs | listed, labelled "paused" |

## 3. Resources

### Time

Sequential execution means wall time is roughly the sum of the per-edition times.
Most of an edition's time is its LLM calls (see
[performance-and-cost-guide.md](performance-and-cost-guide.md)). The
README records about 10 minutes for the original edition; there is no recorded
figure for other editions, so measure yours (section 5).

Because everything is serial under one lock, adding editions pushes the finish time
later but does not increase concurrent load: **CPU and memory are not a scaling
concern here.** One edition's pipeline runs at a time, mostly waiting on the network
and the LLM endpoint. The practical limits are the daily time window you are willing
to wait and your LLM endpoint's rate and spend limits.

### LLM spend

Spend scales roughly linearly with the number of editions, times each edition's
candidate volume. The repository does not store a price, and `runs.cost_usd` is never
populated, so compute it from tokens: `run_attempts.tokens_by_model` per edition
times your provider's prices. `BUDGET_USD` is not enforced; there is no built-in cap
across editions, so set spend limits on the provider side.

To forecast before enabling an edition: run `check_edition.py` (no LLM) to see how
many items survive the gates per source; use the estimation method in the
performance guide; and add the result to your current daily total. Run the new edition
as `draft` for a week and read its real numbers before trusting the forecast.

### Network

Sources are fetched sequentially within an edition, and arXiv and Reddit are spaced
(3 s and 2 s between sources of that family) to avoid 429s. Editions run serially, so
two editions both querying arXiv are separated by the intervening stages. If two
editions share an IP and heavy arXiv usage, watch for 429s (see
[source-reliability-matrix.md](source-reliability-matrix.md)).

## 4. Daily operation

```bash
./run.sh                       # every non-paused edition not already ok today
./run.sh --failed              # only editions whose latest attempt today failed or died
./run.sh --edition a --edition b
./run.sh --force --edition a   # re-run a's post-fetch stages (costs LLM tokens)
./run.sh --refetch --edition a # also re-pull sources (requires --edition)
```

`--failed` and `--edition` cannot be combined. `--refetch` requires `--edition` on
purpose.

## 5. Monitoring many editions

### The run summary

`logs/run-<date>.log` has one line per edition plus a summary:

```
── summary ── ok=2 failed=1
  agents: ok
  engineering-with-ai: ok
  my-edition: FAILED at stage=rank rc=1
```

This is the single place to look first. The full output for each edition is in
`logs/<slug>/run-<date>.log`, which ends in `DONE` on success.

### Failure notifications

On macOS, a failure produces a notification naming the edition and stage, with the
re-run command. A failure of the orchestrator itself produces a separate
"Newsletter pipeline failed" notification. There is no email or other channel; on
other platforms `osascript` is absent and notifications are a no-op, so rely on the
log and the watchdogs.

### Two watchdogs, two jobs

- **Root `watchdog.sh`** (hourly via launchd): fires a notification if the newest
  successful daily-run commit of **any** edition on `content` is older than
  `STALE_HOURS` (default 36). Because it takes the newest across all editions, one
  healthy edition keeps it quiet even if another has been failing for days. It is a
  "whole pipeline is stuck" alarm, not a per-edition one.
- **`scripts/watchdog.sh`**: per edition. For every non-paused edition it checks
  whether today's log ended in `DONE`, and re-runs those that did not with
  `run.sh --edition`. It prints one `EDITION:` line per edition and a final `STATUS:`
  line (worst of). Use `--check` to only report. It probes the same lock as `run.sh`
  so it will not start a second pipeline. See the README, "Self-heal watchdog". Check
  how it is scheduled in your environment; this repository's launchd directory only
  installs the root watchdog.

### Per-edition health queries

Each edition has its own database, so query each:

```bash
for db in .worktrees/content/editions/*/state.db; do
  echo "== $db"
  sqlite3 "$db" "SELECT date, outcome, failed_stage, tokens_in, tokens_out
                 FROM run_attempts ORDER BY date DESC, attempt DESC LIMIT 7"
done
```

And the day's results:

```bash
for db in .worktrees/content/editions/*/state.db; do
  echo "== $db"
  sqlite3 "$db" "SELECT date, items_featured, duration_seconds, tokens_in, tokens_out
                 FROM runs ORDER BY date DESC LIMIT 7"
done
```

An edition with attempts but no `runs` row for a day did not publish that day. See
[metrics/README.md](metrics/README.md) for every column.

### Cost aggregation

`tokens_by_model` in `run_attempts` is `<model>:<in>/<out>` pairs. Sum tokens per model
across editions and multiply by your prices:

```bash
for db in .worktrees/content/editions/*/state.db; do
  sqlite3 "$db" "SELECT '$(basename "$(dirname "$db")")', date, tokens_by_model
                 FROM run_attempts WHERE date = date('now','localtime')"
done
```

Remember that failed attempts are billed too, so use `run_attempts` (every attempt),
not only `runs`, when reconciling against a provider invoice.

## 6. Playbooks

### Adding the Nth edition

1. `uv run scripts/new_edition.py <slug> --name "..." --describe "..."`; fill any TODOs.
2. `uv run scripts/check_edition.py <slug>`; read the per-source counts.
3. Merge as `status: draft` with an `order` after the existing ones.
4. Next morning's run includes it. Expect the total run to take longer by about one
   edition's duration and spend to rise by about one edition's tokens.
5. After about a week of tuning, delete `REVIEW.md` and open a one-line PR changing the
   status to `live`.

### One edition failing without blocking the others

This is the default behaviour: nothing to do for the others. For the failing one:

1. Read the summary line for the stage, then `logs/<slug>/run-<date>.log`.
2. Fix the cause. Stages are idempotent, so `./run.sh --failed` resumes only the
   failed editions.
3. If you need to take it out of the daily run while you investigate, set
   `status: paused` (the daily run skips it). You can still run it by hand with
   `--edition`.
4. Use `--force --edition <slug>` only when you must discard today's rank and write
   output; it spends LLM tokens again.

### An edition that is chronically slow or expensive

Give it a higher `order` so others finish first, shrink its inputs (fewer sources,
tighter `keywords:`), or pause it. There is no per-edition timeout besides the shared
`RANKER_TIMEOUT_S` and `WRITER_TIMEOUT_S`.

### Pausing and archiving an edition

Use `status: paused`. It stops the daily run and keeps the edition on the site,
labelled "paused", with its history intact.

**Do not delete `editions/<slug>/` from `main` to archive it while issues for that slug
still exist on the `content` branch.** The deploy workflow stages every
`editions/*/issues` folder from `content`, and the site build deliberately fails if
`src/content/issues` contains a folder that no `editions/*/edition.yaml` owns
(`site/src/content.config.ts`). To remove an edition completely you would have to
remove its folder on `content` as well, in step with the change on `main`, which loses
its published history from the site. Decide that deliberately and test a site build
first.

A slug is permanent once live (it is in URLs), so do not rename it.

### Moving the pipeline to another machine

All durable state is in git, on two branches:

- `main`: code and the editorial config for every edition.
- `content`: each edition's `state.db` and issues.

So a move is: clone the repo on the new machine; recreate the untracked pieces
(`.env` / `~/.config/agent-newsletter/env` with the LLM settings, the launchd jobs
via `launchd/install.sh`, `uv` / Python dependencies); let `run.sh` create the
`.worktrees/content` worktree on first run; then run `./run.sh` once by hand.
Notes:

- `logs/` is not in git. Run history for the **first** attempt on the new machine
  starts empty, but `run_attempts` and `runs` carry over in each `state.db`.
- Do not run the pipeline on two machines for the same day. The lock is a local file
  and does not coordinate across machines; both would fetch, rank and publish, and the
  second push would conflict or duplicate spend. Disable the old machine's schedule
  first.
- The launchd plists contain hard-coded paths (`/Users/kelly/...`). `launchd/install.sh`
  does **not** rewrite them; apply the `sed` substitutions in the README's install notes
  first, then run `./launchd/install.sh`.

### Migrating an edition between repositories

Not supported by a script. The pieces are the edition folder on `main`
(`editions/<slug>/`) and its folder on `content` (`state.db`, `issues/`). Copy both,
keep the same `slug`, and run `check_edition.py` in the destination.

## 7. Limits to be aware of

- Serial execution only. If the total no longer fits your window, the options are
  pausing editions or cheaper models, not tuning parallelism.
- One global model configuration.
- One run lock: a long-running day blocks the next scheduled run, which exits without
  doing anything while the lock is held.
- Cost and failure history are per edition; there is no built-in cross-edition
  dashboard. Use the loops in section 5, or add one.
