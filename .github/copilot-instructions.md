# Copilot instructions

See [AGENTS.md](../AGENTS.md) for the full guide. Key points:

- Python 3.11+ pipeline in `scripts/`, tests in `tests/` (`uv run --extra test pytest`, coverage gate 95%).
- Astro 5 site in `site/` (pnpm).
- Editions are config-only folders under `editions/<slug>/`; do not hard-code edition slugs in code.
- LLM calls only via `scripts/llm.py`; pipeline stage scripts do not import each other.
- Add tests for every change; sign commits with `git commit -s`.
- Never merge a PR labelled `hold` or remove that label.
