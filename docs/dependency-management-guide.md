# Dependency Management Guide

How to add and manage Python and Node.js dependencies in the project. This guide covers when to add a dependency, how to add it, and what happens next.

---

## Overview

This project uses two dependency managers:

| Runtime | Manager | Lockfile | Where | Risk tier |
|---------|---------|----------|-------|-----------|
| Python | `uv` | `uv.lock` | repo root | Tier 2 (human review) |
| Node.js | `pnpm` | `pnpm-lock.yaml` | `site/` | Tier 2 (human review) |

Both lockfiles are committed to git. Lockfile updates require human review because they affect which code runs in production.

---

## Adding a Python Dependency

Python packages are used by the pipeline scripts (`scripts/fetch.py`, `rank.py`, etc.) and tests.

### When to add one

Before adding a dependency, check if the project already has what you need:

- **HTTP client** → use `scripts/net.py` (wraps `requests`, with SSRF protection)
- **Database** → use `sqlite3` (standard library)
- **JSON** → use `json` (standard library)
- **YAML** → use `yaml` (already a dependency for parsing edition config)
- **Testing** → use `pytest` and fixtures in `tests/conftest.py` (already set up)

If no existing solution fits, add a new dependency.

### How to add one

1. **Check if it's really needed:**
   ```bash
   uv pip list | grep <package-name>   # is it already installed?
   ```

2. **Add it to the project:**
   ```bash
   cd /tmp/agent-newsletter
   uv add <package-name>  # or uv add <package-name>==<version> for a specific version
   ```
   This updates both `pyproject.toml` (the dependency is recorded) and `uv.lock` (exact versions pinned).

3. **Test it locally:**
   ```bash
   uv run --extra test pytest
   uv run --extra test pytest --cov  # check coverage gate (≥95%)
   ```

4. **Commit with DCO sign-off:**
   ```bash
   git add pyproject.toml uv.lock
   git commit -s -m "deps: add <package-name> for <purpose>"
   ```

5. **Open a PR** and link to the issue. The human reviewer checks:
   - Is this package maintained and secure?
   - Does it add substantial new capability, or is there a lighter alternative?
   - Does the coverage gate still pass (≥95%)?

### Example: Adding a new package

```bash
# You decide to add a package for parsing HTML (not already in the project)
uv add beautifulsoup4

# Test it
uv run --extra test pytest
uv run --extra test pytest --cov-report=html

# Commit
git add pyproject.toml uv.lock
git commit -s -m "deps: add beautifulsoup4 for HTML parsing in new source adapter"

# Push and open a PR
```

---

## Adding a Node.js Dependency

Node packages are used only in the Astro site (`site/`). The pipeline (Python scripts) has no dependency on Node.

### When to add one

Before adding, check what's already available:

- **UI component library** → site uses plain HTML/CSS and Astro components; add carefully
- **Markdown parsing** → `marked` is already available
- **Utilities** → consider if vanilla JS or a small utility does the job first

Node dependencies grow the `site/` bundle size, which affects build time and deploy time.

### How to add one

1. **Add it to the site:**
   ```bash
   cd /tmp/agent-newsletter/site
   pnpm add <package-name>  # or pnpm add <package-name>@<version>
   ```
   This updates both `package.json` and `pnpm-lock.yaml`.

2. **Test the build:**
   ```bash
   pnpm --prefix site install   # ensure lockfile is clean
   pnpm --prefix site build
   pnpm --prefix site test
   ```

3. **Commit with DCO sign-off:**
   ```bash
   git add site/package.json site/pnpm-lock.yaml
   git commit -s -m "deps: add <package-name> for <purpose> in site"
   ```

4. **Open a PR**. The reviewer checks:
   - Is this package maintained?
   - Does it bloat the bundle significantly? (check `pnpm why <package-name>`)
   - Do site tests and build pass?

### Example: Adding a site package

```bash
# You decide the search needs a new highlight library
cd /tmp/agent-newsletter/site
pnpm add highlight-js

# Build and test
pnpm --prefix site build
pnpm --prefix site test

# Commit
git add site/package.json site/pnpm-lock.yaml
git commit -s -m "deps: add highlight-js for code syntax highlighting in search"

# Push and open a PR
```

---

## Why Lockfiles Matter

Lockfiles (`uv.lock` and `pnpm-lock.yaml`) pin the exact version of every transitive dependency. Without them:

- A rebuild six months later might pull new (breaking) versions of indirect dependencies
- Different machines might build with different code, even on the same branch
- Production runs unpredictably

**Pinned lockfiles make rebuilds reproducible.** They are committed to git and must be reviewed when changed.

### Reading a lockfile diff

When reviewing a PR that touches `uv.lock` or `pnpm-lock.yaml`:

1. **Check the direct dependencies** — which package version changed?
   ```bash
   git diff HEAD~ pyproject.toml  # or site/package.json
   ```
   This shows only the main dependencies you intended to change.

2. **Check for surprise updates** — did transitive dependencies also update?
   ```bash
   git diff HEAD~ uv.lock | grep -E '^\+version|^-version' | head -20
   ```
   If a transitive dependency jumped major versions unexpectedly, ask why.

3. **Run tests** — did the change break anything?
   ```bash
   pytest  # for Python changes
   pnpm --prefix site test  # for Node changes
   ```

---

## Common Patterns and Best Practices

### Pattern 1: Outbound HTTP calls

New code that needs to make HTTP requests should use `scripts/net.py`:

```python
from net import make_http_client

with make_http_client() as client:
    response = client.get(url)
```

This gives you:
- SSRF protection (blocks loopback, private, and cloud-metadata addresses)
- Shared User-Agent (avoids looking like a bot)
- Timeout defaults

### Pattern 2: Configuration parsing

If a new source format needs parsing (JSON, YAML, etc.):

- JSON → use `json` (stdlib)
- YAML → use `yaml` (already a dependency for edition.yaml parsing)
- CSV → consider `csv` (stdlib) before adding `pandas` or similar
- Atom/RSS → use `feedparser` (already a dependency)

### Pattern 3: Testing a new dependency

Before committing a new dependency, write a test that exercises it:

```python
# tests/test_my_new_module.py
import pytest
from my_module import new_function

def test_new_function():
    result = new_function("test")
    assert result == "expected"
```

Run coverage:

```bash
uv run --extra test pytest tests/test_my_new_module.py
uv run --extra test pytest --cov  # must stay ≥95%
```

If coverage drops below 95%, add tests until it's back above the gate.

---

## Removing a Dependency

If a dependency is no longer used:

1. **Remove it:**
   ```bash
   uv remove <package-name>  # Python
   # or
   pnpm --prefix site remove <package-name>  # Node
   ```

2. **Check for breakage:**
   ```bash
   uv run --extra test pytest  # Python
   pnpm --prefix site test     # Node
   ```

3. **Commit and open a PR:**
   ```bash
   git add pyproject.toml uv.lock  # or site/package.json site/pnpm-lock.yaml
   git commit -s -m "deps: remove <package-name> (no longer used)"
   ```

---

## Upgrading a Dependency

Upgrades happen via Dependabot (configured in `.github/dependabot.yml`). Dependabot proposes minor and patch version bumps automatically.

For a manual upgrade:

1. **Decide if you want the upgrade** — major version bumps often require code changes
2. **Update it:**
   ```bash
   uv lock --upgrade-package <package-name>  # Python
   # or
   pnpm --prefix site update <package-name>  # Node
   ```

3. **Test thoroughly** — upgrades can introduce subtle breaking changes
4. **Commit and open a PR** — include the reason for the upgrade in the PR body

---

## Tier 2 Risk: Why Lockfile Changes Require Review

Lockfile changes are **Tier 2** because:

- A corrupted or malicious lockfile could inject untrusted code into the pipeline or site
- Lockfile updates affect which code runs in production
- A single missing transitive dependency can cause silent failures or behavior changes

The review process:

1. **Hive (or the code author) opens a PR** with lockfile changes
2. **Maintainer reviews:**
   - Does the direct dependency make sense? (new feature, bug fix, etc.)
   - Are transitive dependencies reasonable? (no surprising major version jumps)
   - Do tests pass?
   - Is the commit message clear?
3. **Maintainer merges** — only then does the new code enter `main`

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `uv add` fails with "package not found" | Check the package name on PyPI (`pip search` or `pypi.org`) |
| `pnpm add` fails with "not found" | Check the package name on npm (`npm.js.org` or `npmjs.com`) |
| Lockfile has unexplained large diff | Run `uv sync` or `pnpm install` to regenerate (often the diff is just formatting) |
| Tests fail after adding a dependency | Check that the dependency is importable and the tests are using it correctly |
| Coverage drops below 95% | Add more tests for the new code; see `docs/testing-patterns-guide.md` |
| PR reviewer asks to use existing dependency instead | Check `scripts/net.py`, shared modules in `scripts/`, and existing project dependencies first |

---

## See Also

- [CONTRIBUTING.md](../CONTRIBUTING.md) — commit style, branch naming, PR workflow
- [docs/code-patterns-guide.md](code-patterns-guide.md) — patterns for database, edition, item data, etc.
- [docs/risk-tiers.md](risk-tiers.md) — what changes require review and how
- [docs/testing-patterns-guide.md](testing-patterns-guide.md) — how to write tests for new code
- [pyproject.toml](../pyproject.toml) — Python dependency definitions
- [site/package.json](../site/package.json) — Node dependency definitions
