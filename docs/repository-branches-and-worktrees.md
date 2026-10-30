# Repository Branches and Worktrees

This project uses two independent branches and git worktrees to keep human-authored code separate from machine-authored content. This guide explains why, how to set them up, and how to troubleshoot.

## Why Two Branches?

**The problem:** The daily pipeline commits generated artifacts (`state.db`, issue Markdown files) every day. If these lived on `main` alongside code changes, the history would be cluttered with 365 automated commits per year, obscuring actual code decisions.

**The solution:** Two branches, never merged:

| Branch | Owns | Author | Frequency |
|--------|------|--------|-----------|
| `main` | Code, tests, prompts, site scaffold, config | Humans (PRs) | When changes land |
| `content` (orphan) | `state.db`, issue files (`.md`) | Pipeline (automatic) | Every day |

**Deploy combines both:** The GitHub Actions workflow checks out `main` (structure + config) and `content` (data) in one build, so the site reads both.

---

## The `content` Branch: Why "Orphan"?

The `content` branch is an **orphan branch** — it has no shared git history with `main`. This is intentional:

```bash
# This is what the initial setup does (you only do this once when forking):
git checkout --orphan content       # New branch, no parent
git rm -rf .                        # Clear working tree
git commit --allow-empty -m "init"  # Create empty root commit
git push origin content
git checkout main
```

**Why orphan?**
- Keeps `content`'s history completely separate (smaller, focused on data changes)
- Makes it clear that content and code have different lifecycles
- Prevents accidental merges (git won't let you merge unrelated histories without `--allow-unrelated-histories`)
- `git log main` shows only code changes; `git log content` shows only daily runs

---

## Git Worktrees: Working with Two Branches Simultaneously

Since you can't have two branches checked out in the same directory, the pipeline uses **git worktrees** — multiple working directories pointing to the same repository.

### Worktree Layout

```
repo/
├── .git                        # Shared git directory (single repo)
├── main branch is checked out here
├── .worktrees/
│   ├── content/                # Separate worktree
│   │   └── content branch is checked out here
│   └── (other worktrees if needed)
└── .gitignore                  # Ignores .worktrees/
```

### Initial Setup: One-Time

If you cloned the repo and haven't set up worktrees yet, create the content worktree:

```bash
cd repo/
git worktree add .worktrees/content content
# Creates a new directory .worktrees/content/ with the content branch checked out
```

The pipeline (`run.sh`) sets this up automatically, but you'll need it for local development (e.g., Astro dev server, manual SQL queries).

### Listing Worktrees

```bash
git worktree list
# Output:
# /path/to/repo                    (main)
# /path/to/repo/.worktrees/content (content)
```

---

## Daily Pipeline: How `run.sh` Uses Worktrees

```bash
# 1. run.sh starts in the main repo directory (main branch)
./run.sh

# 2. Inside run.sh, CONTENT_ROOT is set and exported:
export CONTENT_ROOT=.worktrees/content

# 3. Scripts write to CONTENT_ROOT:
# - rank.py outputs issues to: $CONTENT_ROOT/editions/<slug>/issues/YYYY-MM-DD.md
# - publish.py updates: $CONTENT_ROOT/editions/<slug>/state.db

# 4. run_edition.sh commits and pushes from the worktree:
cd $CONTENT_ROOT
git add editions/<slug>/
git commit -m "newsletter(agents): 2026-10-30 daily run"
git push origin content
cd -  # back to main worktree
```

You don't need to do this manually — `run.sh` handles it. But understanding the flow helps when debugging.

---

## Common Worktree Tasks

### Task 1: Check the Content Branch Locally

```bash
cd .worktrees/content

# Now you're on the content branch
git log --oneline -5
# Output: shows recent daily runs

# Query the database
sqlite3 editions/agents/state.db "SELECT date, items_featured FROM runs LIMIT 5;"

# Check if today's run completed
ls -lt editions/agents/issues/*.md | head -3

cd -  # Back to main
```

### Task 2: Clean Up or Reset a Worktree

If the content worktree gets into a bad state (e.g., detached HEAD):

```bash
# Check status
git worktree list
# If status shows "detached" or "pruned", it needs attention

# Remove a broken worktree (safely)
git worktree remove .worktrees/content

# Re-create it fresh
git worktree add .worktrees/content content

# Verify it's on the right branch
cd .worktrees/content && git status
```

### Task 3: Develop Against Latest Content

If you're working on features that need to read from `state.db` (e.g., a new query tool):

```bash
# Ensure your worktree is up to date
cd .worktrees/content
git pull origin content
cd -

# Now write code that references it:
# - CONTENT_ROOT is set by run.sh; falls back to cwd if unset
# - Code reads: $CONTENT_ROOT/editions/<slug>/state.db
```

---

## Troubleshooting Worktrees

### Problem: "fatal: 'main' is not a working directory"

This happens if you try to operate on a worktree that's been deleted.

**Fix:** Prune stale worktree references:
```bash
git worktree prune
git worktree list  # Should be clean now
```

### Problem: ".worktrees/content" shows "detached"

The worktree's HEAD is detached (not on a branch).

**Fix:** Reattach it:
```bash
cd .worktrees/content
git checkout content
cd -
```

### Problem: "working tree is dirty" when running pipeline

The content worktree has uncommitted changes.

**Fix:** Check what changed:
```bash
cd .worktrees/content
git status
git diff
```

Usually it's stale `state.db` changes from an interrupted run. If safe, reset:
```bash
git checkout -- .
cd -
./run.sh  # Try again
```

### Problem: Worktree missing after pulling on main

After a `git pull`, the `.worktrees/` directory might be missing or out of date.

**Fix:** Recreate or update it:
```bash
git worktree list
# If missing:
git worktree add .worktrees/content content
# If outdated:
cd .worktrees/content && git pull origin content && cd -
```

---

## Architecture: How Code Reads Content

Pipeline code and scripts need to access both branches. The pattern is:

```python
# From any stage script:
import os
from pathlib import Path

# CONTENT_ROOT is set by run.sh; defaults to cwd if unset
content_root = Path(os.getenv("CONTENT_ROOT", "."))

# Now reference the content branch files:
state_db = content_root / "editions" / edition_slug / "state.db"
issue_file = content_root / "editions" / edition_slug / "issues" / "2026-10-30.md"
```

**In run.sh:**
```bash
export CONTENT_ROOT=.worktrees/content
uv run scripts/fetch.py     # Scripts use CONTENT_ROOT
uv run scripts/rank.py
# ... etc
```

**In tests:**
```bash
# CONTENT_ROOT is not set, so scripts default to cwd (repo root for tests)
# Tests use fixtures that mock database paths
uv run --extra test pytest tests/
```

---

## See Also

- [README.md § Repository layout](../README.md#repository-layout) — High-level branch model
- [Git Worktrees documentation](https://git-scm.com/docs/git-worktree) — Full `git worktree` reference
- [SPEC.md § Publisher](../SPEC.md#6-publisher--publishpy) — How pipeline writes to content branch
