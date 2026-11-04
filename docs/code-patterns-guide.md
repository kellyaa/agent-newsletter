# Code Patterns and Common Tasks

Reference for patterns used throughout the project. Use these when writing new code or understanding existing code.

---

## Database Access Pattern

**Where:** All stage scripts (`fetch.py`, `prefilter.py`, `rank.py`, etc.)

**Pattern:** Open connection → read/write → commit → close (via context manager)

```python
import sqlite3
from scripts.db import init_db

# Initialize (creates tables if missing, runs schema migrations)
init_db(slug, db_path)

# Read
with sqlite3.connect(db_path) as conn:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM items WHERE status = 'candidate'").fetchall()
    for row in rows:
        print(row["title"])

# Write
with sqlite3.connect(db_path) as conn:
    conn.execute(
        "UPDATE items SET score = ? WHERE id = ?",
        (score, item_id)
    )
    conn.commit()
```

**Key rules:**
- Always use context manager (`with`) for auto-close on error
- Set `row_factory = sqlite3.Row` to access columns by name, not index
- Call `conn.commit()` explicitly; context manager doesn't auto-commit on error
- Use parameterized queries (`?` placeholders) to prevent SQL injection

See [scripts/db.py](../scripts/db.py) for schema and helpers.

---

## Edition Loading Pattern

**Where:** Any script that needs edition config (`scripts/rank.py`, `scripts/check_edition.py`, etc.)

**Pattern:** Load via `scripts/edition.py`, validate strictly

```python
from scripts.edition import Edition

# Load and validate
edition = Edition.load(slug)  # from $EDITION env var
# or
edition = Edition.from_path(Path("editions/agents"))

# Access fields
print(edition.reader_profile)
print(edition.featured_min["papers"])
print(edition.sources)

# Safe defaults
tags = edition.tags or []
```

**Key rules:**
- Load happens in one place; validation is strict
- Raises on invalid YAML or missing required fields
- Never hard-code edition-specific logic (e.g., `if slug == "agents"`)
- Tests use `--no-llm` editions or fixtures

See [scripts/edition.py](../scripts/edition.py) and [tests/conftest.py](../tests/conftest.py) for fixtures.

---

## Item Data Model Pattern

**Where:** When you construct or parse an `Item`

**Pattern:** Use `scripts/models.py` TypedDict; validate shape

```python
from scripts.models import Item

# Construct
item: Item = {
    "id": sha256_of_canonical_url,
    "source": "rss:simonw",
    "url": url,
    "canonical_url": canonical,
    "title": title,
    "author": author or None,
    "published_at": published_iso8601 or None,
    "fetched_at": datetime.now().isoformat(),
    "raw_text": abstract or summary[:500],
    "score": None,  # set by ranker
    "tags": None,
    "section": "blogs",  # set by prefilter
    "section_override": None,
    "keyword_gate_bypass": False,
    "recency_days_override": None,
    "why": None,
    "status": "new",
    "first_seen_date": today,
    "last_seen_date": today,
    "appearances": 1,
    "times_competed": 0,
    "published_date": None,
}

# Insert into DB
with sqlite3.connect(db_path) as conn:
    conn.execute(
        "INSERT OR IGNORE INTO items VALUES (...)",
        tuple(item.values())
    )
```

**Key rules:**
- Use TypedDict from `scripts/models.py`, not raw dicts
- `score`, `tags`, `why`, `section` are set upstream (nullable until then)
- `status` lifecycle: `new` → `candidate` → `featured`/`appendix`/`published`/`dropped`
- Always validate URL canonicalization before storing

See [scripts/models.py](../scripts/models.py) for all TypedDicts.

---

## Environment Variable Access Pattern

**Where:** Any code that needs config from `.env` or runtime env

**Pattern:** Use `os.getenv()` with explicit defaults; document in README

```python
import os
from pathlib import Path

# LLM credentials (required)
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_API_KEY = os.getenv("LLM_API_KEY")
if not LLM_BASE_URL:
    raise RuntimeError("LLM_BASE_URL not set; see README § LLM configuration")

# Timeouts (optional, with defaults)
RANKER_TIMEOUT_S = int(os.getenv("RANKER_TIMEOUT_S", "1800"))
WRITER_TIMEOUT_S = int(os.getenv("WRITER_TIMEOUT_S", "1200"))

# Edition (from $EDITION; required for most scripts)
EDITION_SLUG = os.getenv("EDITION")
if not EDITION_SLUG:
    raise RuntimeError("EDITION env var not set")
```

**Key rules:**
- Required vars: raise RuntimeError with helpful message if missing
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
from scripts.llm import call_llm
from scripts.models import RankerOutput

# Plain text response
response: str = call_llm(
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is an agent?"},
    ]
)

# Structured output (validates against schema)
from typing import TypedDict

class MyResponse(TypedDict):
    score: int
    tags: list[str]
    reasoning: str

response: MyResponse = call_llm(
    messages=[...],
    json_schema=MyResponse,
    timeout_seconds=1800,  # override default
    max_tokens=32000,      # override default
)
```

**Key rules:**
- Import `call_llm` from `scripts/llm.py` only
- Pass `json_schema` as a TypedDict for structured output
- All configuration comes from environment variables (set in `.env`)
- Retries are automatic; do not implement your own
- Log all calls (automatic via `llm.py`)

See [scripts/llm.py](../scripts/llm.py) and [docs/llm-integration-guide.md](llm-integration-guide.md).

---

## Testing Pattern: Unit Tests

**Where:** Tests for individual functions

**Pattern:** Fixtures + arrange-act-assert

```python
import pytest
from pathlib import Path
from scripts.prefilter import recency_gate

@pytest.fixture
def sample_item():
    return {
        "id": "abc123",
        "source": "rss:simonw",
        "published_at": "2026-11-01T10:00:00Z",
        "fetched_at": "2026-11-02T10:00:00Z",
        # ...
    }

def test_recency_gate_recent_item_passes(sample_item):
    # Arrange
    result = recency_gate(
        item=sample_item,
        family="rss",
        recency_days=30,  # 30 days for RSS
    )
    # Act & Assert
    assert result is True

def test_recency_gate_old_item_fails(sample_item):
    sample_item["published_at"] = "2026-09-01T10:00:00Z"  # 2 months old
    result = recency_gate(sample_item, "rss", 30)
    assert result is False
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
import pytest
from pathlib import Path
import subprocess
import sqlite3

@pytest.fixture
def tmp_pipeline(tmp_path, sample_edition_dir):
    """Full pipeline in a temp directory."""
    # Copy edition template
    edition_path = tmp_path / "editions" / "test"
    shutil.copytree(sample_edition_dir, edition_path)
    
    # Create throwaway DB
    db_path = edition_path / "state.db"
    init_db("test", str(db_path))
    
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
from scripts.prompts import render_rank_prompt
from scripts.edition import Edition

edition = Edition.load("agents")

# Render the rank prompt with edition-specific fragments
prompt_text = render_rank_prompt(edition)

# Pass to LLM
response = call_llm(
    messages=[
        {"role": "system", "content": "You are a ranking assistant."},
        {"role": "user", "content": prompt_text},
    ],
    json_schema=RankerOutput,
)
```

**Key rules:**
- Never hand-assemble prompts from file reads
- Use `scripts/prompts.py` helpers (`render_rank_prompt`, `render_write_prompt`)
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
from scripts.llm import call_llm

logger = logging.getLogger(__name__)

try:
    response = call_llm(messages=[...], json_schema=RankerOutput)
except ValueError as e:
    logger.error(f"Ranker returned invalid JSON: {e}")
    raise RuntimeError("Ranker validation failed") from e
except TimeoutError as e:
    logger.warning(f"Ranker timeout; check LLM endpoint")
    raise RuntimeError("Ranker timed out") from e
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
from pathlib import Path

# Get repo root
REPO_ROOT = Path(__file__).parent.parent
db_path = REPO_ROOT / ".worktrees" / "content" / "editions" / slug / "state.db"

# Write to CONTENT_ROOT (set by run.sh)
import os
content_root = Path(os.getenv("CONTENT_ROOT", "."))
issue_file = content_root / "editions" / slug / "issues" / f"{date}.md"
issue_file.parent.mkdir(parents=True, exist_ok=True)
issue_file.write_text(markdown_content)
```

### Date and Time

```python
from datetime import datetime, timedelta

# ISO 8601 format (stored in DB, used in logs)
now = datetime.utcnow().isoformat() + "Z"
today = datetime.utcnow().date()
yesterday = today - timedelta(days=1)

# Parsing
published_dt = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
```

### Logging

```python
import logging

logger = logging.getLogger(__name__)

logger.info("Fetched %d items from %s", count, source)
logger.warning("Source %s returned 429; retrying in 10s", source)
logger.error("Database write failed: %s", exc, exc_info=True)
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

# Project imports
from scripts.edition import Edition
from scripts.models import Item, RankerOutput, Section  # TypedDicts
from scripts.db import init_db  # schema + migrations
from scripts.llm import call_llm  # LLM calls only
from scripts.prompts import render_rank_prompt  # prompt rendering
from scripts.candidates import load_candidates_from_db  # shared queries
```

**Don't import:**
- `openai` directly (use `scripts/llm.py`)
- Other stage scripts (e.g., from `rank.py` inside `write.py`)
- Relative imports across packages (use absolute imports from repo root)

---

## See Also

- [CONTRIBUTING.md § Code style](../CONTRIBUTING.md#code-style) — conventions
- [testing-patterns-guide.md](testing-patterns-guide.md) — in-depth test patterns
- [SPEC.md § Components](../SPEC.md#components) — architecture reference
- [Source code](../scripts/) — read the implementations

