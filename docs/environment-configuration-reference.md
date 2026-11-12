# Environment Configuration Reference

Complete reference for all environment variables used by the pipeline, organized by layer: user configuration, internal runtime, and advanced/debugging options.

## User-Configured Variables

These are meant to be set in `.env` or `~/.config/agent-newsletter/env` before running the pipeline.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `LLM_BASE_URL` | **yes** | (none) | OpenAI-compatible endpoint base URL (e.g., `https://api.openai.com/v1`, `http://localhost:8000/v1`) |
| `LLM_API_KEY` | **yes** | (none) | Bearer token for the LLM endpoint |
| `OPENAI_API_KEY` | no | (none) | Fallback bearer token if `LLM_API_KEY` is unset. Recognized for compatibility with standard OpenAI tools. |
| `RANKER_MODEL` | **yes** | (none) | Model ID for the per-section ranker (e.g., `gpt-4o-mini`, `claude-opus-5-5`) |
| `WRITER_MODEL` | **yes** | (none) | Model ID for the editorial writer (quality matters more; typically a larger/better model than ranker) |
| `RANKER_TIMEOUT_S` | no | `1800` | Per-call timeout for ranker LLM requests (seconds) |
| `WRITER_TIMEOUT_S` | no | `1200` | Per-call timeout for writer LLM requests (seconds) |
| `RANKER_MAX_TOKENS` | no | `32000` | Max completion tokens for ranker calls |
| `WRITER_MAX_TOKENS` | no | `16000` | Max completion tokens for writer call |
| `LLM_EXTRA_HEADERS` | no | (none) | JSON object of extra headers to send on every LLM request (e.g., `{"X-Tenant-Id": "abc"}`) |
| `LLM_RESPONSE_FORMAT` | no | `json_schema` | Structured-output mode: `json_schema` (strict), `json_object`, or `none` (rely on prompt). Use `json_object` for providers that reject `json_schema` (e.g., LiteLLM-fronted Bedrock). |
| `LLM_RETRY_NO_CACHE` | no | `1` | When `llm.py` retries after parse/truncation failure, send cache opt-out header. Set `0` for endpoints that reject unknown body params. |
| `FETCH_ALLOW_PRIVATE_HOSTS` | no | `0` | SSRF guard: blocks fetches that resolve to loopback, RFC-1918 private, or cloud-metadata addresses (e.g., 169.254.169.254). Set to `1` only if a source in `sources.yaml` is intentionally self-hosted or internal. See [extending-pipeline-sources.md](extending-pipeline-sources.md) for adapter patterns. |
| `EDITION` | **yes** (for pipeline runs) | (none) | Slug of the edition to run (e.g., `agents`). No default; must be set explicitly. |
| `MODEL_PRICES_USD` | no | (none) | **Not yet implemented.** No code reads this variable, so setting it has no effect and `runs.cost_usd` is always NULL. Planned format: JSON map of model IDs to `[usd_per_1M_in, usd_per_1M_out]`. Price from `runs.tokens_in/out` downstream until this is built. |
| `BUDGET_USD` | no | (none) | **Not enforced, and no cap is planned for now** (maintainer decision on #438). Setting it has no effect. Tokens are recorded in `runs.tokens_in/out`; `runs.cost_usd` is not populated. |
| `STALE_HOURS` | no | `36` | Hours without a successful daily run before `watchdog.sh` fires a notification. Set in `watchdog.sh`, can be overridden in `.env` (the root `watchdog.sh` loads it via `scripts/envfiles.sh`). |
| `CONTENT_ROOT` | no (usually) | `cwd` | Path to the content worktree (`.worktrees/content`). Set automatically by `run.sh`; must be exported manually when running scripts ad-hoc outside `run.sh`. |

### Configuration Examples

**OpenAI:**
```bash
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
RANKER_MODEL=gpt-4o-mini
WRITER_MODEL=gpt-4o
```

**Local vLLM server:**
```bash
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=dummy  # local servers accept any non-empty value
RANKER_MODEL=meta-llama/Llama-2-7b-chat-hf
WRITER_MODEL=meta-llama/Llama-2-13b-chat-hf
```

**LiteLLM proxy with auth headers:**
```bash
LLM_BASE_URL=https://litellm-proxy.example.com/v1
LLM_API_KEY=sk-litellm-...
LLM_EXTRA_HEADERS='{"X-Tenant-Id": "acme-org", "Authorization": "Bearer custom-token"}'
RANKER_MODEL=claude-opus-5-5
WRITER_MODEL=claude-sonnet-5-5
```

**With internal/self-hosted sources (FETCH_ALLOW_PRIVATE_HOSTS):**
```bash
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
RANKER_MODEL=gpt-4o-mini
WRITER_MODEL=gpt-4o
# Allow fetching from internal Atom/RSS feeds at private addresses
FETCH_ALLOW_PRIVATE_HOSTS=1
```

---

## Runtime-Set Variables

The pipeline sets these automatically during execution. Do not set them manually unless running scripts ad-hoc outside `run.sh`.

| Variable | Set by | Purpose | Example |
|----------|--------|---------|---------|
| `EDITION` | script / user | Currently-running edition slug | `agents` |
| `CONTENT_ROOT` | `run.sh` | Path to `.worktrees/content` | `/path/to/repo/.worktrees/content` |
| `RUN_DATE` | `db.run_date()` or env | Publication date for this run (YYYY-MM-DD) | `2026-11-03` |
| `RUN_STARTED_AT` | `run.sh` → `run_attempt.py` | ISO timestamp when the run started | `2026-11-03T07:00:42Z` |
| `LLM_USAGE_LOG` | `rank.py` / `write.py` | Path to JSONL log of LLM calls (for tokens accounting) | `logs/agents/usage-2026-11-03.jsonl` |
| `RUN_ATTEMPT` | `run.sh` / `run_attempt.py` | Attempt number if this is a retry | `1`, `2`, etc. (or omitted for first try) |

### When Running Scripts Manually

If you invoke a script directly (not via `run.sh`), ensure these are set:

```bash
# Example: running rank.py ad-hoc
export EDITION=agents
export CONTENT_ROOT=/path/to/repo/.worktrees/content
export RUN_DATE=2026-11-03

uv run scripts/rank.py
```

Or source `.env` first:

```bash
set -a
source .env
set +a
uv run scripts/rank.py
```

---

## Internal Variables (Not User-Facing)

These are used internally by the pipeline and should not be modified:

| Variable | Used by | Purpose |
|----------|---------|---------|
| `GH_TOKEN` | `fetch.py` (GitHub source) | GitHub CLI authentication. **Do not export this manually** — the hive handles it. |
| `SHOW_DRAFTS` | `.github/workflows/deploy.yml` (Astro build) | When `1`, draft editions are visible on the site. Typically unset (drafts hidden by default). |
| `SITE_URL` | `.github/workflows/deploy.yml` | GitHub Pages URL for the site (set during deploy). |
| `SITE_BASE` | `.github/workflows/deploy.yml` | Base path for the site (usually `/agent-newsletter/` for a GitHub Pages repo). |

---

## Debugging and Development Variables

These are optional and used for testing, local development, or advanced debugging:

| Variable | Used by | Purpose |
|----------|---------|---------|
| `DEBUG` | (none currently) | Not used by the pipeline, but can be set to enable verbose output in custom scripts. |
| `DRY_RUN` | (none currently; reserved) | When `1`, scripts validate config without writing to DB or pushing. Not yet implemented. |
| `TEST_MODE` | `tests/conftest.py` | Set automatically by pytest; indicates running under tests. |
| `PYTHONDONTWRITEBYTECODE` | (recommended for dev) | Set to `1` to prevent `.pyc` files from being created (cleaner repo). |

### Example Development Setup

```bash
# .env for local development
set -a

# LLM (use a local or free tier for dev)
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=${OPENAI_API_KEY:-}  # falls back to system OPENAI_API_KEY
RANKER_MODEL=gpt-4o-mini
WRITER_MODEL=gpt-4o-mini

# Edition and paths
EDITION=agents
CONTENT_ROOT=$(pwd)/.worktrees/content

# Debug/dev
PYTHONDONTWRITEBYTECODE=1

set +a
```

---

## Priority and Override Order

`run.sh`, the root `watchdog.sh` and `scripts/watchdog.sh` load the env files first, before reading any setting, via `envfiles_load` in `scripts/envfiles.sh`. Values are resolved in this order (first match wins):

1. **Command-line flags** — `./run.sh --edition agents` overrides `EDITION` env var
2. **`.env` in repo root** — user-created, gitignored (sourced last, so it overrides the next two)
3. **`~/.config/agent-newsletter/env`** — machine-wide defaults
4. **System environment** — what's already exported (a value set in either file replaces it)
5. **Code defaults** — hardcoded in scripts (e.g., `RANKER_TIMEOUT_S` defaults to `1800`)

### Example: Multiple Editions

Run different editions with different LLM models:

```bash
# agents edition with GPT-4 (expensive)
RANKER_MODEL=gpt-4 WRITER_MODEL=gpt-4 ./run.sh --edition agents

# news edition with cheaper model
RANKER_MODEL=gpt-4o-mini WRITER_MODEL=gpt-4o-mini ./run.sh --edition news
```

---

## Secrets and Credential Management

### Never Commit Credentials

- `.env` is gitignored — keep it local
- `~/.config/agent-newsletter/env` is on your machine only
- Do not export `LLM_API_KEY` globally in `~/.bashrc` (pollutes child processes)

### Credential Rotation

When rotating API keys:

1. Generate a new key in your LLM provider's dashboard
2. Update `.env` locally:
   ```bash
   LLM_API_KEY=sk-new-key-here
   ```
3. Test with `uv run scripts/rank.py` or `./run.sh --edition agents --force`
4. Revoke the old key in the provider dashboard

### Using a Secrets Manager

For production setups, use a secrets manager:

```bash
# Example: pull from AWS Secrets Manager
LLM_API_KEY=$(aws secretsmanager get-secret-value --secret-id agent-newsletter-key | jq -r .SecretString)
LLM_BASE_URL=$(aws secretsmanager get-secret-value --secret-id agent-newsletter-url | jq -r .SecretString)
export LLM_API_KEY LLM_BASE_URL
```

---

## Troubleshooting Configuration

| Issue | Check |
|-------|-------|
| `LLM endpoint returns 401 Unauthorized` | `LLM_API_KEY` is wrong or expired. Verify in provider dashboard. |
| `timeout: timed out waiting for connection` | `LLM_BASE_URL` is unreachable. Check URL, network, firewall. |
| `model not found: gpt-4o-mini` | `RANKER_MODEL` / `WRITER_MODEL` don't match provider's available models. |
| `unsupported response format: json_schema` | Some providers don't support structured output. Try `LLM_RESPONSE_FORMAT=json_object`. |
| `another run holds .watchdog.lock` | Pipeline is already running. Wait or check `ps aux \| grep run.sh`. |
| `FAILED: flock not found` | `flock` is not installed, so `run.sh` cannot take its lock. Install it (README, Prerequisites). |
| `EDITION not set` | Set `export EDITION=agents` or add to `.env`. |

---

## See Also

- README.md § [LLM Configuration](#llm-configuration-env) — quick setup guide
- `.env.template` — template with all variables
- `scripts/llm.py` — reads and validates LLM config
- `.github/workflows/deploy.yml` — shows how GitHub Actions sets variables

