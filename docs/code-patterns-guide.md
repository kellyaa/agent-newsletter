# Code Patterns and Common Tasks

Reference for patterns used throughout the project. Use these when writing new code or understanding existing code.

`scripts/` is not a Python package: scripts import each other as top-level modules (`import edition`, `from db import connect`). A script run as `uv run scripts/x.py` has `scripts/` on `sys.path`, and `tests/conftest.py` adds it for tests. Never write `from scripts.x import ...`; it fails with `ModuleNotFoundError: No module named 'edition'`. The examples below are checked against the real code by `tests/test_docs_code_examples.py`.

---

## Database Access Pattern

**Where:** All stage scripts (`fetch.py`, `prefilter.py`, `rank.py`, etc.)

**Pattern:** `init_db()` → `connect()` → call a `db.py` helper → close. SQL lives in `scripts/db.py`; stages call its helpers rather than writing statements inline.

```python
from db import connect, count_items_in_status, init_db
from models import Status

# Path defaults to db.db_path(): the selected edition's state.db under CONTENT_ROOT
init_db()            # creates tables if missing, runs migrations
conn = connect()     # WAL, busy timeout, row_factory = sqlite3.Row
try:
    pending = count_items_in_status(conn, Status.FEATURED, Status.APPENDIX)
finally:
    conn.close()
```

**Key rules:**
- New SQL goes in a `db.py` helper (most helpers commit); stages don't run statements inline
- Status and section values come from `models.Status` / `models.Section`, never string literals
- Use parameterized queries (`?` placeholders) to prevent SQL injection
- Pass a path (`init_db(path)`, `connect(path)`) only for a throwaway DB (tests, `backfill` sandbox, `check_edition` dry run)

See [scripts/db.py](../scripts/db.py) for schema and helpers.

---

## Edition Loading Pattern

**Where:** Any script that needs edition config (`scripts/rank.py`, `scripts/check_edition.py`, etc.)

**Pattern:** Load via `scripts/edition.py`, validate strictly

```python
import edition

ed = edition.current()        # the edition named by $EDITION (no default), cached
# or, for a specific slug:
ed = edition.load("agents")   # raises edition.EditionError if invalid

# Access fields
print(ed.reader_profile)
print(ed.section_rules()["papers"]["featured_min"])
print(ed.paths().sources)     # editions/<slug>/sources.yaml
keyword_re = ed.keyword_re()  # the keyword gate as one pattern
```

**Key rules:**
- Load happens in one place (`edition.load`); validation is strict
- Raises `EditionError` on invalid YAML or missing required fields
- Resolve the edition when called (`edition.current()`), never into a module-level constant
- Never hard-code edition-specific logic (e.g., `if slug == "agents"`)
- Tests run against the real `agents` edition (`tests/conftest.py` sets `EDITION`) or a temp edition dir

See [scripts/edition.py](../scripts/edition.py) and [tests/conftest.py](../tests/conftest.py) for fixtures.

---

## Item Data Model Pattern

**Where:** When you pass an item between stages

**Pattern:** Use the stage-boundary TypedDicts in `scripts/models.py`: `ItemRow` (an `items` row), `PrefilterItem`, `CandidateItem`, `ScoredItem`, `RankDecision`, `FeaturedItem`, `AppendixItem`.

```python
from models import ItemRow, Status, item_source

row: ItemRow = {
    "id": item_id,                         # urls.url_id(url)
    "source": item_source(family, entry),  # e.g. "rss:simonw"; never spelled inline
    "status": Status.NEW,
    # ...
}
```

New items enter the DB only through `fetch.upsert_items` → `db.upsert_item`, which sets `status = 'new'`, `appearances = 1` and the first/last-seen dates, and merges re-fetched items (`ON CONFLICT(id)`).

**Key rules:**
- Use the TypedDicts from `scripts/models.py` at stage boundaries
- `score`, `tags`, `why`, `section` are set downstream (nullable until then); `tags` is read and written only via `models.decode_tags` / `encode_tags`
- `status` lifecycle: `new` → `candidate` → `featured`/`appendix` → `published`, or `dropped` / `expired_unjudged`
- URL policy (canonicalization, ids, scheme allowlist) is `scripts/urls.py` only

See [scripts/models.py](../scripts/models.py) for all TypedDicts.

---

## Environment Variable Access Pattern

**Where:** Any code that needs config from `.env` or runtime env

**Pattern:** Read each variable in the one module that owns it, at call time, never into a module-level constant

```python
import edition
from db import content_root
from llm import ranker_config

slug = edition.selected_slug()   # $EDITION; raises EditionError if unset (no default)
root = content_root()            # $CONTENT_ROOT, else cwd
cfg = ranker_config()            # RANKER_MODEL / RANKER_TIMEOUT_S / RANKER_MAX_TOKENS
# LLM_BASE_URL / LLM_API_KEY / LLM_EXTRA_HEADERS are read inside llm.py only
```

**Key rules:**
- One reader per variable: role settings via `llm.ranker_config()` / `writer_config()`, the edition via `edition.current()` / `selected_slug()`, the content root via `db.content_root()`
- Read when called, so a test or a backfill that changes the env is honoured
- Required vars: raise with a helpful message if missing
- Optional vars: provide sensible defaults
- Document all env vars in `.env.template` and README.md
- Never read `.env` file directly; use environment variables (`.env` is sourced by shell before Python runs)
- Tests set env vars in fixtures, never by editing `.env`

See [README.md § LLM configuration](../README.md#llm-configuration-env) and `.env.template`.

---

## LLM Call Pattern

**Where:** Any code that calls an LLM (`rank.py`, `write.py`, etc.)

**Pattern:** Use `scripts/llm.py`'s `call_llm()`, never `openai` directly

```python
from llm import call_llm, writer_config

MY_SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "integer"}, "reasoning": {"type": "string"}},
    "required": ["score", "reasoning"],
    "additionalProperties": False,
}

cfg = writer_config()            # per call, never a module-level constant
response: dict = call_llm(
    prompt,                      # a single user message
    MY_SCHEMA,                   # a JSON Schema dict
    "my_response",               # schema name
    model=cfg.model,
    timeout_s=cfg.timeout_s,
    max_tokens=cfg.max_tokens,
    label="my-stage",
)
```

**Key rules:**
- Import `call_llm` from `llm` only; it always returns a dict (there is no plain-text mode)
- The schema is a JSON Schema dict; `call_llm` checks the reply is a JSON object, callers sanitise fields
- Model, timeout and token cap come from `ranker_config()` / `writer_config()`
- One retry on a bad reply is automatic; do not add your own. Catch `ContentFilterError` if you can split the batch
- Echoed ids go through `llm_ids.alias_items` / `resolve_ids`

See [scripts/llm.py](../scripts/llm.py) and [docs/llm-integration-guide.md](llm-integration-guide.md).

---

## Testing Pattern: Unit Tests

**Where:** Tests for individual functions

**Pattern:** Fixtures + arrange-act-assert

```python
from datetime import datetime, timezone

import pytest
from gates import passes_recency

NOW = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)  # pin the clock

@pytest.fixture
def sample_item():
    return {
        "id": "abc123",
        "source": "rss:simonw",                 # rss => 30-day window
        "published_at": "2026-11-01T10:00:00Z",
        "fetched_at": "2026-11-02T10:00:00Z",
        # ...
    }

def test_recent_rss_item_passes(sample_item):
    assert passes_recency(sample_item, NOW) is True

def test_old_rss_item_fails(sample_item):
    sample_item["published_at"] = "2026-09-01T10:00:00Z"  # 2 months old
    assert passes_recency(sample_item, NOW) is False
```

**Key rules:**
- Use fixtures from [tests/conftest.py](../tests/conftest.py)
- One assertion per test, or related assertions with clear intent
- Test both pass and fail cases
- Mock external calls (HTTP, subprocess) with fixtures
- Coverage gate is 95%; new code must have tests

See [docs/testing-patterns-guide.md](testing-patterns-guide.md) for detailed patterns.

---

## Testing Pattern: End-to-End Tests

**Where:** Tests that run the full pipeline

**Pattern:** Throwaway DB, fixture data, verify output

```python
import os
import shutil
import sqlite3
import subprocess

import pytest
from db import init_db

@pytest.fixture
def tmp_pipeline(tmp_path, sample_edition_dir):
    """Full pipeline in a temp directory."""
    # Copy edition template
    edition_path = tmp_path / "editions" / "test"
    shutil.copytree(sample_edition_dir, edition_path)
    
    # Create throwaway DB
    db_path = edition_path / "state.db"
    init_db(db_path)
    
    return tmp_path, db_path

def test_full_pipeline_end_to_end(tmp_pipeline, monkeypatch):
    tmp_root, db_path = tmp_pipeline
    monkeypatch.chdir(tmp_root)
    
    # Run fetch stage
    result = subprocess.run(
        ["uv", "run", "scripts/fetch.py"],
        env={**os.environ, "EDITION": "test"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    
    # Verify items in DB
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert count > 0
```

**Key rules:**
- Use `tmp_path` fixture for isolation
- Create throwaway DBs; never touch live data
- Run stages as subprocesses, not imports (catches import errors)
- Verify output in DB or files, not by inspecting print output
- Keep tests fast: use fixture data, not real feeds

See [tests/e2e/](../tests/e2e/) for examples.

---

## Prompt Rendering Pattern

**Where:** Scripts that use prompts

**Pattern:** Use `scripts/prompts.py` to merge base + fragments

```python
import edition
import prompts

ed = edition.load("agents")

# Render the rank prompt (prompts/rank.md) with the edition's fragments
rubric = prompts.render("rank", ed)
# render_and_log also writes it to logs/<slug>/rank.rendered.md
rubric = prompts.render_and_log("rank", ed)
```

`rank.py` then appends the candidates JSON and passes the result to `call_llm` with `rank.RANKER_OUTPUT_SCHEMA` (see the LLM Call Pattern above).

**Key rules:**
- Never hand-assemble prompts from file reads
- Use `prompts.render(stage, ed)` / `render_and_log`; file names come from `prompts.template_path` / `fragment_filename`
- Edition fragments override base templates via `{{slot}}` substitution
- Changes to `prompts/rank.md` invalidate cached paper scores (hash check in prefilter)

See [scripts/prompts.py](../scripts/prompts.py).

---

## Idempotency Pattern

**Where:** Pipeline stages (should be safe to re-run)

**Pattern:** Use `INSERT OR IGNORE` for DB writes

```python
import sqlite3

# Insert items, skipping duplicates by URL
with sqlite3.connect(db_path) as conn:
    for item in items:
        conn.execute(
            "INSERT OR IGNORE INTO items (id, source, url, ...) VALUES (?, ?, ?, ...)",
            (item["id"], item["source"], item["url"], ...)
        )
    conn.commit()

# Update status, but check state first
with sqlite3.connect(db_path) as conn:
    for item_id in featured_ids:
        current_status = conn.execute(
            "SELECT status FROM items WHERE id = ?", (item_id,)
        ).fetchone()[0]
        
        # Only transition from 'candidate' to 'featured'
        if current_status == "candidate":
            conn.execute(
                "UPDATE items SET status = 'featured' WHERE id = ?",
                (item_id,)
            )
    conn.commit()
```

**Key rules:**
- Use `INSERT OR IGNORE` (skip if key exists) or `INSERT OR REPLACE` (overwrite)
- For updates, check current state before transitioning
- Log what you did so re-runs are visible in logs
- Tests verify idempotency: run the same stage twice, expect identical output

---

## Error Handling Pattern

**Where:** Any code that can fail (fetch, LLM calls, DB operations)

**Pattern:** Catch, log, raise or retry

```python
import logging
from llm import ContentFilterError, call_llm

log = logging.getLogger("my-stage")

try:
    response = call_llm(prompt, MY_SCHEMA, "my_response", model=cfg.model)
except ContentFilterError:
    # Not retried by llm.py: split the batch and drop the blocked item (rank does this)
    raise
except RuntimeError as e:
    # Both attempts failed (bad JSON, truncation, timeout, HTTP error)
    log.error("my-stage LLM call failed: %s", e)
    raise
```

**Key rules:**
- Log the error with context (what were you doing?)
- Re-raise as a different exception type with a message the user understands
- Don't swallow exceptions; let the orchestrator (`run.sh`) see them
- Retries belong in `llm.py`, not in caller code

See [scripts/llm.py](../scripts/llm.py) for retry patterns.

---

## Common Functions and Helpers

### File and Path Handling

```python
import edition
from db import content_root, db_path
from models import issue_filename

# The selected edition's state.db and issue folder under CONTENT_ROOT (set by run.sh)
state_db = db_path()
paths = edition.runtime_paths(content_root())
issue_file = paths.issues_dir / issue_filename(date)   # <YYYY-MM-DD>.md
issue_file.parent.mkdir(parents=True, exist_ok=True)
issue_file.write_text(markdown_content)

# Debug artifacts for the edition
logs_dir = edition.current().paths().logs              # logs/<slug>/
```

### Date and Time

```python
from datetime import datetime, timezone

from db import run_date
from models import parse_instant

# The run's date key ($RUN_DATE from run_edition.sh, else the local date)
today = run_date()

# Wall-clock instants: aware UTC, injectable as `now` so tests can pin them
now = datetime.now(timezone.utc)

# Parsing stored instants: trailing Z accepted, zone-less => UTC
published_dt = parse_instant(item["published_at"])
```

### Logging

```python
import logging

import logsetup

log = logging.getLogger("my-stage")

log.info("Fetched %d items from %s", count, source)
log.warning("Source %s returned 429; retrying in 10s", source)
log.error("Database write failed: %s", exc, exc_info=True)

if __name__ == "__main__":
    logsetup.configure()   # only here, never at import time or via basicConfig
```

---

## Key Imports Checklist

Before starting a new script, make sure you have:

```python
import logging
import os
import sqlite3
from pathlib import Path
from datetime import datetime

# Project imports (scripts/ is on sys.path; not a package)
import edition                                   # edition.current(), runtime_paths()
import logsetup                                  # logsetup.configure() under __main__
import prompts                                   # prompts.render(stage, ed)
from candidates import load_ranking_pool         # the ranker's candidate pool
from db import connect, init_db                  # schema, migrations and all SQL
from llm import call_llm, ranker_config          # LLM calls only
from models import ItemRow, Section, Status      # domain constants and TypedDicts
```

**Don't import:**
- `openai` directly (use `scripts/llm.py`)
- Other stage scripts (e.g., from `rank.py` inside `write.py`; `tests/test_stage_import_boundary.py` enforces this)
- `scripts.<module>` (scripts/ is not a package)

---

## See Also

- [CONTRIBUTING.md § Code style](../CONTRIBUTING.md#code-style) — conventions
- [testing-patterns-guide.md](testing-patterns-guide.md) — in-depth test patterns
- [SPEC.md § Components](../SPEC.md#components) — architecture reference
- [Source code](../scripts/) — read the implementations

