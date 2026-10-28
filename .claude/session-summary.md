# Session summary

A short hand-off note so the next agent session does not start cold. Overwrite it at the end of a session that changed how the project works; keep it under one screen. Satisfies the ACMM `acmm:session-summary` criterion.

Durable lessons go elsewhere: corrections in `.memory/corrections.jsonl`, reflections in `docs/reflections/`, standing rules in `AGENTS.md`.

## Current state

- Multi-edition pipeline is in place (`editions/<slug>/` config, shared code in `scripts/`). Editions: `agents` (live) and `engineering-with-ai` (draft, go-live check pending).
- Reddit is fetched through the unauthenticated Atom feed with a per-source top-N `limit` (no credentials).
- Policy for automated contributors: `AGENTS.md` and `policies/agent-policy.yaml`. Tool permissions: `.claude/settings.json`.

## Open threads

- `engineering-with-ai` `status: live` flip is tracked in the multi-edition issue (#291).
- `.github/workflows/**` changes need a human or a merge-capable agent; agents file an issue with the exact replacement text instead.

## Next session checklist

1. Read `AGENTS.md` and `.memory/corrections.jsonl`.
2. Branch from a fresh `origin/main`; never commit to `content`.
3. Run `uv run --extra test pytest` before opening a PR.
