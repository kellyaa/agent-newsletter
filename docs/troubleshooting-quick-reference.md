# Troubleshooting Quick Reference

When something goes wrong with the pipeline, use this page to find the right guide fast. This is the **index**; detailed troubleshooting steps are in the guides linked below.

---

## Something Failed — Quick Diagnosis

**Status: Pipeline failed or didn't run**

1. Check the orchestrator log:
   ```bash
   tail -50 logs/run-$(date +%Y-%m-%d).log
   ```
   
   Does it mention a stage name or `FAILED`? 
   - If **no entries**, the pipeline never started → go to ["Pipeline won't start"](#pipeline-wont-start)
   - If **shows a stage name** (e.g., `FAILED at stage=rank`) → go to ["A pipeline stage failed"](#a-pipeline-stage-failed)
   - If **shows nothing recent** → go to ["Scheduler issues"](#scheduler-issues)

2. Check the per-edition log:
   ```bash
   tail -100 logs/agents/run-$(date +%Y-%m-%d).log    # replace 'agents' with your edition slug
   ```
   
   This has the detailed error. Look for one of these patterns:

---

## A Pipeline Stage Failed

Each stage has its own guide. Find the stage name in your log (e.g., `failed at stage=fetch`):

| Stage | Most Common Causes | Guide |
|-------|-------------------|-------|
| **fetch** | Network error, rate limit, feed broken | [Debugging → Fetch](#) in [debugging-guide.md](debugging-guide.md#stage-1-fetch) |
| **prefilter** | Keyword gate too tight (no candidates) | [Debugging → Prefilter](#) in [debugging-guide.md](debugging-guide.md#stage-2-prefilter) |
| **rank** | LLM timeout, endpoint down, bad API key | [LLM Error Handling](llm-error-handling-guide.md) |
| **write** | LLM timeout, file write error | [LLM Error Handling](llm-error-handling-guide.md) or [Debugging → Write](#) in [debugging-guide.md](debugging-guide.md#stage-4-write) |
| **publish** | Git error (dirty worktree), content branch issue | [Debugging → Publish](#) in [debugging-guide.md](debugging-guide.md#stage-5-publish) |

**Quick fix:** Often, a transient failure (network blip, LLM rate limit) fixes itself on retry:
```bash
./run.sh --failed      # Re-run only the editions that failed today
```

---

## Issue Has Too Few Featured Items

**Symptom:** The published issue has 5 featured items instead of the usual 12–15.

→ Go to [Troubleshooting Low Featured Item Count](troubleshooting-low-featured-count.md)

**Quick diagnosis:**
```bash
sqlite3 editions/agents/state.db "SELECT date, items_featured, items_candidate FROM runs ORDER BY date DESC LIMIT 3"
```

- If `items_featured` is suddenly 0 or very small: fetch/prefilter issue
- If `items_featured` is small but `items_candidate` is normal: ranker issue
- If both are small: no items passed the prefilter (keyword gate too tight)

---

## Pipeline Won't Start

**Symptoms:** `./run.sh` finishes instantly with no log file, or the launchd job never runs.

**Diagnosis:**

1. **Not starting from shell command?**
   ```bash
   ./run.sh --edition agents    # try manually with one edition
   ```
   If it works, the script is fine. Skip to ["Scheduler Issues"](#scheduler-issues).

2. **Manual run fails immediately?**
   ```bash
   ./run.sh 2>&1 | head -30
   ```
   Look for:
   - `flock not found` → install util-linux (`brew install util-linux` on macOS)
   - `edition.py list` → an edition config has syntax errors; run `uv run scripts/check_edition.py <slug>`
   - `CONTENT_ROOT` or worktree errors → see [Repository Branches and Worktrees](repository-branches-and-worktrees.md)
   - `git pull --ff-only` error → your working tree or branches have uncommitted changes

3. **LLM endpoint unreachable?**
   ```bash
   curl -s "$LLM_BASE_URL/health" || echo "endpoint down"
   ```
   Set `LLM_BASE_URL` and `LLM_API_KEY` in `.env` and retry.

→ Go to [Operations Runbook § Pipeline won't start](operations-runbook.md#pipeline-wont-start) for detailed fixes.

---

## Scheduler Issues

**Symptoms:** Pipeline worked before, but hasn't run in 24+ hours. The watchdog may have fired a stale-pipeline alert.

**For macOS launchd:**

```bash
# Check if the job is installed:
launchctl list | grep agent-newsletter

# Check the plist for hard-coded paths (should be your repo root, not /Users/kelly/…):
grep -A 2 "ProgramArguments" ~/Library/LaunchAgents/com.kelly.agent-newsletter.plist

# Manually trigger the job:
launchctl start com.kelly.agent-newsletter

# Tail the launchd log:
log stream --predicate 'eventMessage contains "agent-newsletter"' --level debug
```

**For Linux cron/systemd:**

```bash
# Check if the cron job is there:
crontab -l | grep agent-newsletter

# Run it manually:
/path/to/agent-newsletter/run.sh >> /path/to/agent-newsletter/logs/launchd.out 2>&1

# Check systemd status (if using systemd):
systemctl --user status agent-newsletter.timer
```

→ Go to [CONTRIBUTING.md § Setup](../CONTRIBUTING.md#setup) or [Operations Runbook § Scheduler setup](operations-runbook.md) for detailed instructions.

---

## Content Not Updating on the Site

**Symptoms:** The website hasn't updated in days, but the pipeline logs show `DONE`.

**Diagnosis:**

1. Check if issues are being written:
   ```bash
   git log --oneline .worktrees/content/site/src/content/issues/ | head -5
   ```

2. Check if the content branch is building:
   ```bash
   git show-ref | grep content    # does the content branch exist?
   git log --oneline content | head -5    # any recent commits?
   ```

3. Check the GitHub Pages deploy workflow:
   - Go to https://github.com/kellyaa/agent-newsletter/actions
   - Look for the "Pages" or deploy workflow; is it passing?

→ Go to [Operations Runbook § Content not updating on site](operations-runbook.md#content-not-updating-on-the-site) or [Debugging Guide → Publish](debugging-guide.md#stage-5-publish).

---

## LLM Calls Failing or Hanging

**Symptoms:** `rank.py` or `write.py` hangs, times out, or errors with "API key invalid" or "model not found."

→ Go to [LLM Error Handling Guide](llm-error-handling-guide.md)

**Quick checks:**

```bash
# Is the endpoint reachable?
curl -s -H "Authorization: Bearer $LLM_API_KEY" "$LLM_BASE_URL/models" | jq . | head -20

# Is your API key valid?
# (depends on your LLM provider; check their docs)

# Is RANKER_MODEL and WRITER_MODEL set correctly?
echo "Ranker: $RANKER_MODEL, Writer: $WRITER_MODEL"

# Try a simple ranker test:
EDITION=agents uv run scripts/rank.py
```

---

## Database Corruption or Lock Errors

**Symptoms:** `sqlite3 locked database` errors, or queries return garbage/incomplete data.

→ Go to [Database Recovery Guide](database-recovery-guide.md)

**Quick mitigation:**

```bash
# Check database integrity:
sqlite3 editions/agents/state.db ".schema" | head -20

# Close any stuck connections:
# (If using macOS) lsof | grep state.db
# (If using Linux) fuser editions/agents/state.db

# As a last resort, reset the database (loses today's prefilter state):
uv run scripts/reset_day.py --edition agents --force
```

---

## I Don't Know What's Wrong — Full Diagnostics

If none of the above help, run the full diagnostics checklist:

1. **Environment:**
   ```bash
   echo "Python: $(python3 --version)"
   echo "uv: $(uv --version 2>/dev/null || echo 'not installed')"
   echo "flock: $(which flock || echo 'not found')"
   echo "Node: $(node --version)"
   echo "Repo root: $(pwd)"
   ```

2. **LLM setup:**
   ```bash
   set -a; . .env; set +a
   curl -s "$LLM_BASE_URL/health" 2>&1 | head -5
   ```

3. **Edition health:**
   ```bash
   uv run scripts/check_edition.py agents    # replace 'agents' with your edition
   ```

4. **Database:**
   ```bash
   sqlite3 editions/agents/state.db "SELECT COUNT(*) FROM items; SELECT COUNT(*) FROM runs;"
   ```

5. **Recent logs:**
   ```bash
   tail -100 logs/agents/run-$(date +%Y-%m-%d).log
   tail -100 logs/run-$(date +%Y-%m-%d).log
   ```

6. **File that should exist:**
   ```bash
   ls -lah .env .worktrees/content/ editions/agents/ | head -20
   ```

→ Go to [Debugging Guide](debugging-guide.md) for deeper diagnosis, or [Operations Runbook](operations-runbook.md) for common operational issues.

---

## Links to All Troubleshooting Guides

| Symptom | Guide |
|---------|-------|
| Pipeline stage failed | [Debugging Guide](debugging-guide.md) |
| Too few featured items | [Troubleshooting Low Featured Item Count](troubleshooting-low-featured-count.md) |
| LLM errors (timeout, API key, model not found) | [LLM Error Handling Guide](llm-error-handling-guide.md) |
| Database corruption, locks | [Database Recovery Guide](database-recovery-guide.md) |
| Pipeline won't start, scheduler issues | [Operations Runbook](operations-runbook.md) |
| Need to recover a missed day | [Run Strategies and Recovery](run-strategies-and-recovery.md) — `backfill` command |
| Low signal from a source | [Source Reliability Matrix](source-reliability-matrix.md) |
| Everything fails, need to start over | [Getting Started Guide](getting-started-guide.md) and [Local Setup](../CONTRIBUTING.md#local-setup) |

---

## Still Stuck?

1. **Search the docs:** Use your editor's find (Ctrl-F) to search across all `docs/*.md` for a keyword
2. **Check the ROADMAP:** [ROADMAP.md](../ROADMAP.md) lists known issues and planned fixes
3. **Search GitHub issues:** https://github.com/kellyaa/agent-newsletter/issues?q=label:documentation
4. **Read test examples:** Tests under `tests/` are well-commented examples of how things work

