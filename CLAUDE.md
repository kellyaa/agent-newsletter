# CLAUDE.md

Project instructions for Claude Code. The shared agent guide is [AGENTS.md](AGENTS.md); read it first. Everything there applies here.

Quick reference:

- Tests: `uv run --extra test pytest` (coverage gate 95%, do not lower).
- Lint an edition: `uv run scripts/check_edition.py <slug>`.
- Site: `pnpm --prefix site build`.
- Sign commits: `git commit -s`.
- Never merge a `hold`-labelled PR or remove its label.
- Daily output belongs to the `content` branch; work only on `main`-based branches.
