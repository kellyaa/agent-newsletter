# Change risk tiers

How much review a change needs, by what it touches. Reviewers and automated contributors use this to decide how carefully to look and what to flag. (ACMM `acmm:tier-classifier`.)

| Tier | Touches | Review expected |
|------|---------|-----------------|
| 0 – low | `docs/`, `*.md`, `editions/<slug>/sources.yaml` (adding a verified feed), `.memory/` | One human glance; CI green |
| 1 – normal | `scripts/`, `tests/`, `prompts/`, `editions/<slug>/` prompts and config, `site/` | Tests added or updated; CI green; human review |
| 2 – high | `run.sh`, `watchdog.sh`, `launchd/`, `scripts/llm.py`, `scripts/db.py`, `scripts/publish.py`, `pyproject.toml` (coverage gate), `uv.lock`, `site/pnpm-lock.yaml` | Human review required; state the failure mode and how it was tested; never lower the coverage gate |
| 3 – critical | `.github/workflows/**`, `policies/agent-policy.yaml`, `AGENTS.md`, `.claude/settings.json`, secrets handling (`.env`, `.env.template`), the `content` branch | Human-authored or human-applied only. Agents open an issue with the exact replacement text instead of a PR |

Rules of thumb:

- A change takes the highest tier of any file it touches.
- Flipping an edition to `status: live` is tier 2: the slug is permanent once live.
- Agent PRs are labelled `hold` and are never merged by an agent, whatever the tier (see `AGENTS.md`).
- Nothing is ever committed by hand to the `content` branch.
