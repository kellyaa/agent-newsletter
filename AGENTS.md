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
- Per-source labels (`<family>/<id>`, `gh/<owner>/<repo>`) are built only by `models.source_label`; `fetch` records them and `dead_sources` joins on them. The watchdog readers (`missed_days`, `dead_sources`) read state.db only via `db.connect_readonly` / `db.load_*` helpers (`tests/test_source_label_single_source.py` enforces this).
- `fetch.py`, `publish.py` and `db.py` resolve the edition (`EDITION`) and `CONTENT_ROOT` when called, not at import: use `fetch.sources_path()` / `publish.issues_dir()` / `db.db_path()`, never a module-level constant derived from `edition.*` or `db.CONTENT_ROOT`. `check_edition` must not set `os.environ` to select an edition (`tests/test_fetch_sources_path_lazy.py` and `tests/test_publish_issues_dir_lazy.py` enforce this).
- URL policy (http(s) allowlist, canonicalization, item ids, reddit feed URL) lives in `scripts/urls.py`; do not re-implement it inline (`tests/test_url_policy_single_source.py` enforces this).
- Ranker scoring (per-section LLM calls, content-filter bisection, output sanitising, thresholds/caps, unscored → appendix fallback) lives in `rank.score_candidates`; `rank.main` and `backfill.rank_with_optional_llm` both call it and must not orchestrate `invoke_ranker`/`rank_section`/`assign_statuses` themselves (`tests/test_rank_scoring_single_source.py` enforces this).
- LLM role settings (`WRITER_*` / `RANKER_*` model, timeout, token cap and their defaults) are read only by `llm.writer_config()` / `llm.ranker_config()`; do not read them from the environment elsewhere (`tests/test_llm_role_config.py` enforces this).
- The ids an LLM echoes back are short per-call aliases (`i1…iN`); aliasing, `_dup` tolerance and unknown-id handling live in `scripts/llm_ids.py`. `rank.py` and `write.py` call `alias_items`/`resolve_ids` and must not parse LLM ids themselves (`tests/test_llm_ids_single_source.py` enforces this).
- Edition behaviour comes from `editions/<slug>/` config, never from hard-coded slugs in code.
- A new script or documented path must be reflected in `README.md` / `CONTRIBUTING.md` (`tests/test_docs_paths.py` enforces this).
- A PR labelled `hold` (or `on-hold` / `do-not-merge`) must not be merged until a human removes the label. Never remove it yourself.
- Do not edit `.github/workflows/**` unless your credentials allow it; open an issue with the exact replacement text instead.
- Do not commit secrets or `.env`; use `.env.template` for new variables.
