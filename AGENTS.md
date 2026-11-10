# AGENTS.md

Instructions for AI coding agents working in this repository. Humans: see `CONTRIBUTING.md`; design is in `SPEC.md`.

## What this repo is

A daily multi-edition newsletter pipeline (fetch → prefilter → rank → write → publish) plus an Astro static site. Each newsletter is an *edition*: editorial config only, under `editions/<slug>/`. Pipeline code in `scripts/` is shared by all editions. Daily output lives on the separate `content` branch; never commit to it by hand.

## Layout

- `scripts/` – pipeline stages and tooling (`fetch.py`, `prefilter.py`, `rank.py`, `write.py`, `publish.py`, `edition.py`, `new_edition.py`, `check_edition.py`, …)
- `editions/<slug>/` – `edition.yaml`, `sources.yaml`, `prompts/` fragments; `_template/` is not a real edition
- `prompts/` – base rank/write templates with `{{slot}}` placeholders
- `tests/` – pytest suite (Python); `site/tests/` – site tests
- `site/` – Astro 5 site (pnpm)
- `.github/workflows/` – CI and Pages deploy

## Commands

```bash
uv sync
uv run --extra test pytest                 # full suite; coverage gate is 95% (currently 100%) — do not lower it
uv run scripts/check_edition.py <slug>     # lint + dry-run an edition (no LLM)
pnpm --prefix site install && pnpm --prefix site build
```

## Rules

- Branch from `main`; commit with DCO sign-off (`git commit -s`); conventional style `<scope>: <description>`.
- Add or update tests with every code change. Keep coverage at or above the gate.
- Pipeline stage scripts (`fetch`, `prefilter`, `rank`, `write`, `publish`, `run_attempt`) must not import from each other; they share state only through `state.db` and the shared helpers (`db.py`, `edition.py`, `models.py`, `llm.py`, `candidates.py`). `tests/test_stage_import_boundary.py` enforces this.
- LLM calls go through `scripts/llm.py` only.
- sources.yaml per-entry override rules (`is_positive_int`, `SOURCE_BOOL_FLAGS`, `SOURCE_POSITIVE_INT_KEYS`, `REDDIT_DEFAULT_LIMIT`) live in `scripts/models.py`; fetch, check_edition and new_edition consume them (`tests/test_source_override_rules_single_source.py` enforces this).
- The content-branch layout (`editions/<slug>/state.db`, `issues/`, the legacy `agents` edition) and the run-commit subject prefix (`newsletter(<slug>): `) live in `scripts/models.py` (`EDITIONS_DIRNAME`, `STATE_DB_NAME`, `ISSUES_DIRNAME`, `LEGACY_EDITION`, `edition_state_db_relpath`, `run_commit_prefix`). `scripts/run_edition.sh` writes them and `backfill.py` reads them back, so change both sides together (`tests/test_content_layout_single_source.py` enforces this).
- The issue filename (`<YYYY-MM-DD>.md` in an edition's `issues/`) is built only by `models.issue_filename` and globbed only via `models.ISSUE_GLOB`; `write`, `backfill`, `publish`, `replay_writer` and `reset_day` must not spell `f"{date}.md"` or `"*.md"` inline, and `site/src/content.config.ts` must glob the same suffix (`tests/test_issue_filename_single_source.py` enforces this).
- Per-source labels (`<family>/<id>`, `gh/<owner>/<repo>`) are built only by `models.source_label`; `fetch` records them and `dead_sources` joins on them. The watchdog readers (`missed_days`, `dead_sources`) read state.db only via `db.connect_readonly` / `db.load_*` helpers (`tests/test_source_label_single_source.py` enforces this).
- `fetch.py`, `prefilter.py`, `rank.py`, `write.py`, `publish.py` and `db.py` resolve the edition (`EDITION`) and `CONTENT_ROOT` when called, not at import: use `fetch.sources_path()` / `prefilter.candidates_path()` / `rank.ranked_path()` / `write.issues_dir()` / `publish.issues_dir()` / `db.db_path()` / `edition.current()`, never a module-level constant derived from `edition.*` or `db.CONTENT_ROOT`. `check_edition` must not set `os.environ` to select an edition (`tests/test_fetch_sources_path_lazy.py`, `tests/test_prefilter_edition_lazy.py`, `tests/test_rank_edition_lazy.py`, `tests/test_write_issues_dir_lazy.py` and `tests/test_publish_issues_dir_lazy.py` enforce this). `db.CONTENT_ROOT` / `db.DB_PATH` are deprecated lazy aliases; scripts call `db.content_root()` (`tests/test_db_content_root_lazy.py`).
- The `items.source` prefix per source family (`rss:`, `arxiv:`, `hn:`, `reddit:`, `html:`, `gh:`) lives in `scripts/models.py` as `ITEM_SOURCE_PREFIX`; `fetch` builds the value only via `models.item_source` and `gates` parses it only via `models.item_source_prefix`. `gates.RECENCY_DAYS` / `SOURCE_PRIORITY` / `SECTION_BY_FAMILY` must cover exactly those prefixes (`tests/test_item_source_single_source.py` enforces this).
- Stored ISO-8601 instants (`items.published_at` / `fetched_at`, `$RUN_STARTED_AT`) are parsed only by `models.parse_instant` (trailing `Z` accepted, zone-less => UTC); `fetch`, `gates`, `candidates` and `publish` must not call `datetime.fromisoformat` themselves (`tests/test_instant_parse_single_source.py` enforces this).
- URL policy (http(s) allowlist, canonicalization, item ids, reddit feed URL) lives in `scripts/urls.py`; do not re-implement it inline (`tests/test_url_policy_single_source.py` enforces this).
- Ranker scoring (per-section LLM calls, content-filter bisection, output sanitising, thresholds/caps, unscored → appendix fallback) lives in `rank.score_candidates`; `rank.main` and `backfill.rank_with_optional_llm` both call it and must not orchestrate `invoke_ranker`/`rank_section`/`assign_statuses` themselves (`tests/test_rank_scoring_single_source.py` enforces this).
- LLM role settings (`WRITER_*` / `RANKER_*` model, timeout, token cap and their defaults) are read only by `llm.writer_config()` / `llm.ranker_config()`; do not read them from the environment elsewhere, and call them per LLM call, never into a module-level constant (`rank.RANKER` / `write.WRITER` are deprecated lazy aliases) (`tests/test_llm_role_config.py` and `tests/test_llm_role_config_lazy.py` enforce this).
- The ids an LLM echoes back are short per-call aliases (`i1…iN`); aliasing, `_dup` tolerance and unknown-id handling live in `scripts/llm_ids.py`. `rank.py` and `write.py` call `alias_items`/`resolve_ids` and must not parse LLM ids themselves (`tests/test_llm_ids_single_source.py` enforces this).
- The run_attempts outcome vocabulary (`started` / `ok` / `failed`, plus the `none` sentinel) lives in `scripts/models.py` as `AttemptOutcome` (StrEnum) and `NO_ATTEMPT`; `db.py` and `run_attempt.py` import and use them, and `run.sh` / `run_edition.sh` reference their values. Do not hard-code outcome strings elsewhere (`tests/test_attempt_outcome_single_source.py` enforces this).
- The log line format and root-logger setup live only in `scripts/logsetup.py` (`LOG_FORMAT`, `configure()`). Entry scripts call `logsetup.configure()` under `if __name__ == "__main__":` before `main()`, never at import time and never `logging.basicConfig` themselves, so importing a stage (as `backfill`, `replay_writer`, `check_edition` and the tests do) never configures logging (`tests/test_logging_config_single_source.py` enforces this).
- The run-log contract (`logs/<slug>/run-<date>.log`, the `<ts> [run] DONE` and `FAILED at stage=<stage>` markers, and the `ts`/`log`/`notify` helpers) lives only in `scripts/runlog.sh`. `run_edition.sh` writes the markers via `runlog_mark_done`/`runlog_mark_failed`; `run.sh` and `scripts/watchdog.sh` read them only via `runlog_is_complete`/`runlog_failed_stage`. All three source it and must not spell the markers, the log path, or the helpers inline. Stage names must match `RUNLOG_STAGE_RE` (`tests/test_run_log_single_source.py` enforces this).
- edition.yaml value rules (slug/tag shape `SLUG_RE` / `TAG_RE`, keyword checks `keyword_problem` / `combined_keywords_problem`) live only in `scripts/edition.py`; `edition.load` enforces them and `new_edition.py` drops what they reject, so a scaffolded edition always loads. `new_edition.py` must not copy the regexes or `re.compile` keywords itself (`tests/test_edition_keyword_rules_single_source.py` enforces this).
- Edition behaviour comes from `editions/<slug>/` config, never from hard-coded slugs in code.
- A new script or documented path must be reflected in `README.md` / `CONTRIBUTING.md` (`tests/test_docs_paths.py` enforces this).
- A PR labelled `hold` (or `on-hold` / `do-not-merge`) must not be merged until a human removes the label. Never remove it yourself.
- Do not edit `.github/workflows/**` unless your credentials allow it; open an issue with the exact replacement text instead.
- Do not commit secrets or `.env`; use `.env.template` for new variables.
