# Database Recovery and Advanced Troubleshooting

This guide covers recovery procedures for corrupted or inconsistent `state.db`, diagnosing schema issues, and manual data repairs that go beyond the standard troubleshooting steps in `debugging-guide.md`.

## Quick Diagnosis: Is state.db Corrupt?

Run these checks first:

```bash
EDITION=agents
DB="editions/$EDITION/state.db"

# 1. Can SQLite open it?
sqlite3 "$DB" "SELECT 1;" > /dev/null && echo "✓ Database opens" || echo "✗ Corrupt: open failed"

# 2. Can we query the main tables?
sqlite3 "$DB" "SELECT COUNT(*) as items_count FROM items;" && echo "✓ items table OK" || echo "✗ Corrupt: items table"
sqlite3 "$DB" "SELECT COUNT(*) as runs_count FROM runs;" && echo "✓ runs table OK" || echo "✗ Corrupt: runs table"

# 3. Check for orphaned rows (items without matching runs)
ORPHANED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM items WHERE published_at NOT IN (SELECT date FROM runs);")
echo "Orphaned items (not in any published run): $ORPHANED"

# 4. Verify schema matches expectations
sqlite3 "$DB" ".schema items" | grep -q "PRIMARY KEY" && echo "✓ Schema looks OK" || echo "⚠ Schema may be incomplete"
```

---

## Recovery Scenarios

### Scenario 1: SQLite "database is locked" or "disk I/O error"

**Symptoms:**
```
sqlite3: Error: database is locked
# or
sqlite3: Error: disk I/O error
```

**Causes:**
- Another process is actively writing to state.db (a run is in progress)
- File permissions issue
- Disk full or filesystem error

**Recovery:**

```bash
# 1. Check if a run is in progress
ps aux | grep -E "run.sh|fetch.py|rank.py|write.py" | grep -v grep

# 2. If yes, wait for it to finish. If hung, kill it:
pkill -f "/path/to/repo/run.sh"

# 3. Check file permissions
ls -l editions/agents/state.db
# Should be readable/writable by your user. Fix with:
chmod 644 editions/agents/state.db

# 4. Check disk space
df -h editions/

# 5. Verify the file is not corrupted after the crash
sqlite3 editions/agents/state.db "PRAGMA integrity_check;" | head -5
```

### Scenario 2: "database corruption detected" or PRAGMA integrity_check Fails

**Symptoms:**
```
sqlite3: Error: database corruption detected
# or after running:
sqlite3 editions/agents/state.db "PRAGMA integrity_check;"
# Output: ✗ (rows after header should be all-OK, not error lines)
```

**Recovery:**

**Step 1: Backup the corrupted file**
```bash
EDITION=agents
DB="editions/$EDITION/state.db"
cp "$DB" "$DB.corrupt.$(date +%Y%m%d-%H%M%S).backup"
```

**Step 2: Try SQLite's built-in recovery**
```bash
# SQLite can sometimes recover from minor corruption
sqlite3 "$DB" "PRAGMA integrity_check;" | head -20

# Attempt recovery via dump and restore
sqlite3 "$DB" ".dump" > /tmp/state_dump.sql 2>&1
```

**Step 3: Restore from dump**
```bash
# If .dump succeeded:
mv "$DB" "$DB.corrupted"
sqlite3 "$DB" < /tmp/state_dump.sql

# Verify integrity
sqlite3 "$DB" "PRAGMA integrity_check;"
```

**Step 4: If dump fails or recovery succeeds only partially**

Restore from git history (state.db is on the `content` branch):

```bash
# List the last 3 commits that touched state.db
git log --oneline -- "editions/$EDITION/state.db" | head -3

# Restore from a known-good commit (e.g., 5 days ago)
git show HEAD~10:editions/$EDITION/state.db > /tmp/state_good.db
sqlite3 /tmp/state_good.db "PRAGMA integrity_check;" | head -1

# If OK, use it (you'll lose runs and items added since that commit)
mv /tmp/state_good.db "editions/$EDITION/state.db"
```

**Step 5: Re-initialize if necessary**

If state.db is beyond repair and you don't have a good backup, reinitialize and accept data loss:

```bash
# Run from the content worktree (CONTENT_ROOT). Move the broken DB aside first:
# init_db only creates missing tables and columns, it does not replace a file.
mv "editions/$EDITION/state.db" "editions/$EDITION/state.db.broken"

# Create a fresh, fully migrated DB (scripts/ is not a package, so put it on sys.path)
EDITION="$EDITION" CONTENT_ROOT="$PWD" uv run --project "$REPO_ROOT" python -c "
import sys; sys.path.insert(0, '$REPO_ROOT/scripts')
import db; db.init_db(); print('Fresh database created at', db.db_path())"
```

`$REPO_ROOT` is your code checkout (the directory with `run.sh`). `db.init_db()` resolves the path from `$EDITION` and `$CONTENT_ROOT`; set `CONTENT_ROOT` explicitly, because when it is unset the DB is created under the code checkout, not the current directory.

---

### Scenario 3: Schema Mismatch or "no such column"

**Symptoms:**
```
sqlite3: Error: no such column: cost_usd
# or during a pipeline run:
OperationalError: no such column: ...
```

**Cause:** Code expects a column that doesn't exist (usually after upgrading to a version with a new schema).

**Recovery:**

The pipeline automatically runs schema migrations on `init_db()`. Force a migration:

```bash
# Method 1: Run a pipeline stage with a fresh edge; it will auto-migrate
# (Safest: this goes through the normal init_db path)
EDITION=agents ./run.sh --force --edition agents

# Method 2: Manual migration (if you know the exact ALTER needed)
sqlite3 "editions/agents/state.db" << 'SQL'
ALTER TABLE runs ADD COLUMN cost_usd REAL;
SQL

# Verify the column now exists
sqlite3 "editions/agents/state.db" ".schema runs" | grep cost_usd
```

---

### Scenario 4: Duplicate or Missing Items

**Symptoms:**
- An item appears twice in the `items` table with different IDs
- An item from an issue doesn't exist in state.db
- Issue frontmatter references items not in the database

**Diagnosis:**

```bash
EDITION=agents
DB="editions/$EDITION/state.db"

# Find duplicates by canonical URL
sqlite3 "$DB" << 'SQL'
SELECT canonical_url, COUNT(*) as count, GROUP_CONCAT(id) as ids
FROM items
GROUP BY canonical_url
HAVING count > 1
LIMIT 10;
SQL

# Find items in an issue that aren't in the DB
sqlite3 "$DB" << 'SQL'
SELECT id, title, status
FROM items
WHERE status != 'published' AND published_at >= date('now', '-1 day')
ORDER BY published_at DESC;
SQL
```

**Fix:**

If a duplicate exists with different scores, keep the higher-scored one:

```bash
# Manually delete the lower-scored duplicate
sqlite3 "$DB" << 'SQL'
DELETE FROM items
WHERE canonical_url = 'https://example.com/article'
  AND id = 'the_id_of_the_lower_scored_one'
  AND score < 7;
SQL
```

If missing items are from old issues (not affecting current runs), this is safe data loss.

---

### Scenario 5: run_attempts Shows "started" but No End Time

**Symptoms:**
```bash
sqlite3 editions/agents/state.db "SELECT * FROM run_attempts ORDER BY start_time DESC LIMIT 1;"
# Shows outcome='started' with NULL end_time from hours ago
```

**Cause:** The `run.sh` crashed or was killed mid-pipeline, and `run_attempt.py` never finished recording the final state.

**Recovery:**

```bash
# 1. Check if any pipeline process is still running
ps aux | grep -E "run.sh|fetch.py|rank.py|write.py" | grep -v grep

# 2. If processes are running, check their start times (against the run_attempt record)
# If they're very old (>30 min), kill them:
pkill -f "/path/to/repo/run.sh"

# 3. Manually update the run_attempt to mark it as failed:
EDITION=agents
DB="editions/$EDITION/state.db"

sqlite3 "$DB" << 'SQL'
UPDATE run_attempts
SET 
  outcome = 'failed',
  failed_stage = 'unknown (crash)',
  end_time = datetime('now')
WHERE outcome = 'started' AND end_time IS NULL
ORDER BY start_time DESC
LIMIT 1;
SQL

# 4. Re-run the pipeline to try again
./run.sh --edition "$EDITION"
```

---

### Scenario 6: Aged-Out Papers Not Recorded Correctly

**Symptoms:**
- `papers_expired_unjudged` in the `runs` table is NULL or seems wrong
- Items show `status='expired_unjudged'` but aren't counted

**Cause:** Schema mismatch or prefilter didn't write the counter.

**Diagnosis & Fix:**

```bash
EDITION=agents
DB="editions/$EDITION/state.db"

# 1. Check how many items have this status
sqlite3 "$DB" "SELECT COUNT(*) FROM items WHERE status = 'expired_unjudged';"

# 2. Check the latest runs record
sqlite3 "$DB" "SELECT date, papers_expired_unjudged FROM runs ORDER BY date DESC LIMIT 5;"

# 3. If the column is missing, add it:
sqlite3 "$DB" "ALTER TABLE runs ADD COLUMN papers_expired_unjudged INTEGER;"

# 4. Recompute from items (advisory only; historical runs remain NULL):
sqlite3 "$DB" << 'SQL'
UPDATE runs
SET papers_expired_unjudged = (
  SELECT COUNT(*) FROM items 
  WHERE status = 'expired_unjudged' 
  AND published_date = runs.date
)
WHERE papers_expired_unjudged IS NULL;
SQL
```

---

## Maintenance Tasks

### Regular Health Checks

Add to your monitoring/cron:

```bash
#!/bin/bash
EDITION=agents
DB="editions/$EDITION/state.db"

# Weekly integrity check
if ! sqlite3 "$DB" "PRAGMA integrity_check;" | grep -q "^ok$"; then
  echo "ERROR: state.db integrity check failed"
  exit 1
fi

# Check for orphaned rows
ORPHANED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM items WHERE published_at NOT IN (SELECT date FROM runs);")
if [ "$ORPHANED" -gt 0 ]; then
  echo "WARNING: $ORPHANED orphaned items in database"
fi

# Check for stuck runs
STUCK=$(sqlite3 "$DB" "SELECT COUNT(*) FROM run_attempts WHERE outcome = 'started' AND end_time IS NULL AND start_time < datetime('now', '-2 hours');")
if [ "$STUCK" -gt 0 ]; then
  echo "WARNING: $STUCK stuck run_attempts (started >2h ago, never finished)"
fi
```

### Backup Strategy

Since `state.db` is on the `content` branch (tracked in git), backups happen via git history. To make an explicit backup:

```bash
# Manual backup
cp editions/agents/state.db editions/agents/state.db.backup.$(date +%Y%m%d)

# Or tag the current commit
git tag -a backup-agents-$(date +%Y%m%d) -m "state.db backup before major change"
git push origin --tags
```

Restore from tag:

```bash
git show backup-agents-20261103:editions/agents/state.db > /tmp/restore.db
# Verify integrity
sqlite3 /tmp/restore.db "PRAGMA integrity_check;"
# Use it
cp /tmp/restore.db editions/agents/state.db
```

---

## When to Give Up and Reinitialize

If state.db cannot be recovered and you don't have a good backup, reinitialize. You'll lose:
- All historical items and scores
- Run history (`runs` table)
- Dedup state (items may re-feature)
- Cost metrics from before reinitialization

But you keep:
- Edition config (`edition.yaml`, `sources.yaml`, prompts)
- Code and tests
- The content branch can be rebuilt from scratch

```bash
EDITION=agents
DB="editions/$EDITION/state.db"

# Backup the corrupted file
cp "$DB" "$DB.corrupted"

# Remove and reinitialize
rm "$DB"
EDITION="$EDITION" CONTENT_ROOT="$PWD" uv run --project "$REPO_ROOT" python -c "import sys; sys.path.insert(0, '$REPO_ROOT/scripts'); import db; db.init_db()"

# Run a fresh pipeline
./run.sh --edition "$EDITION" --refetch
```

---

## Preventive Measures

1. **Git commit after every successful run** — `run.sh` automatically does this, so state.db history is available.
2. **Monitor disk space** — I/O errors often start with "disk full."
3. **Keep `.worktrees/content` clean** — don't manually edit state.db on the content branch.
4. **Run integrity checks weekly** — catch corruption early.
5. **Test recovery procedures** — practice restoring from backups before you need to.

---

## See Also

- [Database Schema and Querying Guide](database-schema-guide.md) — schema, columns, and diagnostic queries
- [Debugging Guide](debugging-guide.md) — stage-by-stage failure diagnosis
- [Operations and Maintenance](operations-and-maintenance.md) — routine ops and monitoring
