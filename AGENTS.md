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
- Edition behaviour comes from `editions/<slug>/` config, never from hard-coded slugs in code.
- A new script or documented path must be reflected in `README.md` / `CONTRIBUTING.md` (`tests/test_docs_paths.py` enforces this).
- A PR labelled `hold` (or `on-hold` / `do-not-merge`) must not be merged until a human removes the label. Never remove it yourself.
- Do not edit `.github/workflows/**` unless your credentials allow it; open an issue with the exact replacement text instead.
- Do not commit secrets or `.env`; use `.env.template` for new variables.
