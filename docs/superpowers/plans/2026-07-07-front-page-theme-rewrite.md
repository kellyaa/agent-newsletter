# Front-page theme rewrite implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the writer LLM's `theme` rubric in `prompts/write.md` so front-page summaries follow a lede + territory structure — readable to a cold reader, specific enough to distinguish issues day-to-day, without collapsing to either generic category labels or dense system-name catalogs.

**Architecture:** Single-file prompt change. No schema change, no template change, no new LLM call. The `theme` field in `WRITER_SCHEMA` (`scripts/write.py:60`) stays a nullable string ≤800 chars. Verification is a manual replay against three recent runs that produced known-bad themes.

**Tech Stack:** Markdown prompt file (`prompts/write.md`), Python 3 replay helper (`scripts/replay_writer.py`), SQLite (`state.db`), the existing `scripts/write.py` writer entry point invoked via `scripts/llm.py`.

**Reference:** `docs/superpowers/specs/2026-07-07-front-page-theme-rewrite-design.md`

---

## File structure

- **Modify:** `prompts/write.md` — replace the `theme` line in the output-rules bullet list, and replace the entire `## Theme` section (currently lines 92-127).
- **Create:** `scripts/replay_writer.py` — a small stand-alone helper for replaying the writer against a past date's featured set without mutating `state.db` or the archived issue file. Not wired into `run.sh`. Deliberately kept separate from `scripts/write.py` so replay is exploratory, not a supported product path.
- **Create:** `logs/theme-replay-<date>.json` (per replay, gitignored) — captures each replay's writer output for eyeball comparison. `logs/` already exists.

No changes to `scripts/write.py`, `scripts/publish.py`, the Astro templates, or `SPEC.md`.

---

## Task 1: Rewrite `prompts/write.md`

**Files:**
- Modify: `prompts/write.md:42` (the theme entry in the JSON output-shape block)
- Modify: `prompts/write.md:59` (the theme bullet in the "Output rules" section)
- Modify: `prompts/write.md:92-127` (the entire `## Theme` section, from "## Theme" through "Return `null` only if there are fewer than 3 featured items…")

- [ ] **Step 1: Read the current `prompts/write.md` to confirm the byte ranges above.**

The line numbers in the "Files" block are from the version at commit `68b5c88`. If any preceding section has grown, adjust — the edits below are keyed by the *content* of the old strings, not line numbers.

- [ ] **Step 2: Update the `theme` field description in the output-shape JSON block.**

Find in `prompts/write.md`:
```
  "theme": "2-3 sentence concrete digest of today's specific contributions: name actual mechanisms, papers, numbers, or systems. Never a category label. May be null only when fewer than 3 featured items exist.",
```

Replace with:
```
  "theme": "Front-page card copy in two parts: a lede (1-2 sentences introducing 1-2 featured items in plain framing) plus a territory sentence (gesturing at what else is in the issue without naming systems). ~60 words. May be null only when fewer than 3 featured items exist.",
```

- [ ] **Step 3: Update the `theme` bullet in the Output rules section.**

Find in `prompts/write.md`:
```
- `theme`: a 2-3 sentence digest that names *specific* things from today — paper titles, mechanisms, system names, numbers, claims. It is **not** a category label, **not** a meta-narrative about the field, and **not** a framing of "today's items focus on X." If you find yourself writing about "the field," "production-readiness," or "moving past capability," delete it and start over with the actual content. See the "Theme" section below.
```

Replace with:
```
- `theme`: two-part front-page card copy — a lede (1-2 sentences framing 1-2 featured items plainly, for a cold reader) plus a territory sentence (gesturing at the rest of the day by kinds of work, not system names). ~60 words total. It is **not** a category label, **not** a meta-narrative about the field, **not** a run-on list of system names, and **not** a framing of "today's items focus on X." See the "Theme" section below.
```

- [ ] **Step 4: Replace the entire `## Theme` section.**

Find the block starting with `## Theme` and ending at the last bullet before `## Handling sparse input` (in the current file: from line 92 to line 127 inclusive).

Replace it with:

```
## Theme

The theme is a two-part front-page card. It is the reader's one-glance answer to "what's actually in today's issue," written for a **cold reader who has not clicked through and does not know any of the paper or system names.**

### Shape

**Part 1 — Lede (1-2 sentences).** Introduce 1-2 featured items using the "front-page test" below. Frame each item by what it *is* (a paper looking at X, a postmortem of Y, a benchmark measuring Z) *before* quoting its finding. Prefer plain English over the paper's internal jargon. Project or paper names are **optional** here — include a name only when it will mean something to the reader without further context; otherwise defer names to the per-item summary.

**Voice patterns.** *"A new paper looks at X and finds Y."* / *"A production postmortem describes how Z broke and what fixed it."* / *"Two blog posts push back on the assumption that…"* The subject of the sentence is the *kind of work* and its *finding*, not the project name.

**Part 2 — Territory (1 sentence).** Gestures at what else is in the issue *without naming systems*. Talks about kinds of work — "three more papers on memory attacks", "two production postmortems", "a batch of eval methodology posts". Reader learns the shape of the day.

Cap: ~60 words total. Tight beats comprehensive.

### The front-page test

Of today's featured items, pick 1-2 that best fit at least one of:

- **(a)** most surprising or counterintuitive claim
- **(b)** sharpest contradiction with prior work or another featured item
- **(c)** most actionable finding for a senior engineer
- **(d)** explainable in one sentence to a reader who has not heard of the specific project — if you can't frame it plainly, it's probably not the right lede

Highest score is **not** automatically the lede. A smaller item with a striking claim outranks a solid-but-expected paper.

### Source constraint

The items you introduce in the lede **must appear in the input's `featured` array**. Do not name or describe papers, systems, or claims from the appendix, from `previous_newsletter`, or from your training data. If the item you want to lead with is not in `featured[]`, pick a different one.

### What a good theme looks like

> A new paper looks at what happens to code review inside teams that adopt AI-generated PRs — and finds that reviewers approve more (+14.5pp) but write 22% fewer comments, an erosion that survives four organizational controls. Two other papers explore related memory-store attacks; a Simon Willison post argues the same review-atrophy pattern shows up at his consulting clients.

This works because the lede *frames* the paper (what it looked at) before quoting its finding — a cold reader understands what is interesting without needing to know the paper's name. The territory sentence gestures at the rest of the day by kind of work, not by more system names.

### What a bad theme looks like (dense list)

> FARMA details attacks on remembered reasoning history, MemGhost shows how to inject stealthy memories via a single email, ADI bypasses instruction-focused defenses by poisoning metadata, Governed Individuation proposes an architectural fix using cryptographic identity, and R2Act reveals models choose valid recovery actions 37-60% of the time.

This is a catalog, not editorial. A cold reader has no idea what any of these names are.

### What a bad theme looks like (abstract-shaped)

> Habituation at the Gate documents a 22% drop in reviewer comments as AI PR exposure grows, even as approval rates rise +14.5pp — a review-erosion signal that survives four organizational controls.

Reads like the paper's own abstract. Cold reader can't tell what "Habituation at the Gate" is or what the paper is *about* — only what it *found*.

### What a bad theme looks like (generic)

> Today's papers and posts focus on the engineering of production-ready agentic systems, moving beyond proofs-of-concept to introduce concrete architectural patterns, programming models, and evaluation frameworks for building and operating them reliably.

A category label dressed as prose. Would describe 60% of every issue.

### Banned framings

The following templates are forbidden. If your theme paraphrases any of these, rewrite it from scratch:

- "moving past 'can it work?' to 'how does it fail?'"
- "the field is maturing / shifting from X to Y"
- "production-ready" / "production reality" / "the engineering of robust agents"
- "today's items focus on / converge on / highlight"
- "a wave of new benchmarks/papers/research"
- "moving beyond proofs-of-concept"
- "the hard engineering of"
- Any sentence whose subject is "the field," "the focus," "today's research," "this week's work"
- Any lede that names 3+ items in a row (that's a catalog, not a lede)
- Any territory sentence that names a specific system or paper (territory is kinds-of-work only)

### Construction rules

- Lede first: pick 1-2 items via the front-page test, frame each by *what it is* before quoting its number or claim.
- Territory second: one sentence gesturing at what else is in the issue by category — "three more papers on X", "two postmortems", "a batch of Y."
- Cap ~60 words total.
- Return `null` only if there are fewer than 3 featured items. With 3+, you can always assemble a lede+territory pair from what's there.
```

- [ ] **Step 5: Diff-check the edit.**

Run: `git diff prompts/write.md`

Expected: two-line replacements at the schema field and rules-bullet lines, plus a full-section replacement in `## Theme`. No unintended changes to per-item summary rules, voice/style, section context, sparse-input handling, or constraints sections.

- [ ] **Step 6: Commit.**

```bash
git add prompts/write.md
git commit -m "$(cat <<'EOF'
writer: rewrite theme rubric as lede + territory

Front-page themes had collapsed into dense catalogs (5+ system names
per sentence, unreadable to a cold reader) after the 2026-06-23
rewrite. The rule "name at least 3 specific things" was doing the
damage.

New rubric: a lede (1-2 sentences introducing 1-2 featured items in
plain framing — "a new paper looks at X and finds Y" — before any
project name or number) plus a territory sentence gesturing at the
rest of the day by kinds of work. ~60 words total. Project names are
optional in the lede; the territory sentence must not name systems.

The banlist against generic category-label themes stays. Added bans
against 3+ named items in a row and against named systems in the
territory sentence.

Spec: docs/superpowers/specs/2026-07-07-front-page-theme-rewrite-design.md
EOF
)"
```

---

## Task 2: Build a replay helper for cold verification

**Files:**
- Create: `scripts/replay_writer.py`
- Modify: `.gitignore` (if `logs/theme-replay-*.json` is not already covered)

The helper reads a past day's `status = 'published'` items from `state.db`, reconstructs the exact writer input JSON, invokes the writer LLM with the current prompt, and writes the raw output to `logs/theme-replay-<date>.json`. It does **not** touch `state.db` or `site/src/content/issues/`.

- [ ] **Step 1: Confirm `.gitignore` covers replay logs.**

Run: `grep -E '^logs/|^\*\.log$' /Users/kelly/git/incubation/.gitignore`

If nothing matches or `logs/` is not present as a pattern, append `logs/theme-replay-*.json` to `.gitignore` and stage that in the same commit as the script.

Expected: either the existing `.gitignore` already ignores `logs/` (most likely), or you'll add the one line.

- [ ] **Step 2: Write the replay helper.**

Create `scripts/replay_writer.py`:

```python
"""Replay the writer stage against a past day's featured items.

Reconstructs the writer's input JSON from state.db (using status='published'
rows for the given date, which is what 'featured' items become post-publish)
and invokes the writer LLM with the current prompt. Writes the raw output
to logs/theme-replay-<date>.json. Does NOT mutate state.db or the archived
issue file.

Usage: python scripts/replay_writer.py <YYYY-MM-DD>
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Reuse the writer's building blocks so replay uses the same input shape
# as production. If write.py's assembly changes, replay picks it up.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import REPO_ROOT, connect, init_db  # noqa: E402
from write import (  # noqa: E402
    PROMPT_PATH,
    READER_PROFILE,
    RAW_TEXT_MAX,
    PREV_NEWSLETTER_MAX,
    build_writer_input,
    find_previous_newsletter,
    invoke_writer,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("replay_writer")


def load_published_items(conn, date: str):
    """Same shape as write.load_today_items, but selects status='published'
    (the terminal state for what were featured items) for the given date.
    """
    featured_rows = conn.execute(
        """
        SELECT id, source, url, canonical_url, title, author, published_at,
               raw_text, score, tags, why, section
        FROM items
        WHERE status = 'published' AND last_seen_date = ?
        ORDER BY section, score DESC, id
        """,
        (date,),
    ).fetchall()

    appendix_rows = conn.execute(
        """
        SELECT id, source, url, canonical_url, title, section
        FROM items
        WHERE status = 'appendix' AND last_seen_date = ?
        ORDER BY section, id
        """,
        (date,),
    ).fetchall()

    featured: list[dict] = []
    for r in featured_rows:
        try:
            tags = json.loads(r["tags"]) if r["tags"] else []
        except json.JSONDecodeError:
            tags = []
        raw = r["raw_text"] or ""
        if len(raw) > RAW_TEXT_MAX:
            raw = raw[:RAW_TEXT_MAX] + "\n...[truncated]"
        featured.append({
            "id": r["id"],
            "section": r["section"],
            "source": r["source"],
            "url": r["url"],
            "title": r["title"],
            "author": r["author"],
            "published_at": r["published_at"],
            "raw_text": raw,
            "score": r["score"],
            "tags": tags,
            "why": r["why"],
        })

    appendix_by_section: dict[str, list[dict]] = {"papers": [], "news": [], "blogs": []}
    for r in appendix_rows:
        section = r["section"] or "blogs"
        appendix_by_section.setdefault(section, []).append({
            "id": r["id"],
            "section": section,
            "source": r["source"],
            "url": r["url"],
            "title": r["title"],
        })

    counts = {"papers": 0, "news": 0, "blogs": 0}
    for it in featured:
        counts[it["section"]] = counts.get(it["section"], 0) + 1
    appendix_total = sum(len(v) for v in appendix_by_section.values())
    metadata = {
        "items_considered": None,
        "items_featured_total": len(featured),
        "items_featured_papers": counts["papers"],
        "items_featured_news": counts["news"],
        "items_featured_blogs": counts["blogs"],
        "items_appendix": appendix_total,
    }
    return featured, appendix_by_section, metadata


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/replay_writer.py <YYYY-MM-DD>", file=sys.stderr)
        return 2

    date = sys.argv[1]
    init_db()
    conn = connect()
    featured, appendix_by_section, metadata = load_published_items(conn, date)
    conn.close()

    if not featured:
        log.error("no published items for %s; nothing to replay", date)
        return 1

    log.info(
        "replay date=%s featured=%d (papers=%d news=%d blogs=%d)",
        date, len(featured),
        metadata["items_featured_papers"],
        metadata["items_featured_news"],
        metadata["items_featured_blogs"],
    )

    prev = find_previous_newsletter(date)
    payload = build_writer_input(date, featured, appendix_by_section, metadata, prev)

    rubric = PROMPT_PATH.read_text()
    prompt = (
        "You are writing today's AI Agents newsletter prose. The input JSON "
        "is below; the rubric and output schema follow.\n\n"
        f"## Input\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        f"---\n\n{rubric}"
    )

    writer_output = invoke_writer(prompt)

    out_path = REPO_ROOT / "logs" / f"theme-replay-{date}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps({
        "date": date,
        "theme": writer_output.get("theme"),
        "items": writer_output.get("items", []),
    }, indent=2))
    log.info("wrote %s", out_path)
    log.info("theme: %s", writer_output.get("theme"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Why this shape:

- Imports `build_writer_input`, `find_previous_newsletter`, and `invoke_writer` from `scripts/write.py` so replay uses the exact same input assembly and LLM call as production. Any drift in production behavior is picked up automatically.
- Uses `status = 'published'` filtered by `last_seen_date`, which is the terminal state for items that were `'featured'` at run time (verified via `sqlite3 state.db "SELECT DISTINCT status ..."` — after publish.py runs, featured items land at `'published'`).
- `items_considered` is set to `null` in the reconstructed metadata since the original count at run time isn't recoverable from row status alone. This is a known cosmetic gap; the theme rubric doesn't depend on it.
- Writes the entire writer output (theme + per-item summaries) so the reviewer can also spot regressions in per-item summaries as a side effect. Only the `theme` field is under test.

- [ ] **Step 3: Verify the helper runs end-to-end on one date.**

Run: `python scripts/replay_writer.py 2026-06-24`

Expected:
- Log line reporting `featured=12 (papers=... news=... blogs=...)` matching what `runs.items_featured` recorded for that date.
- Log line `wrote /Users/kelly/git/incubation/logs/theme-replay-2026-06-24.json`.
- Final log line `theme: <the newly-generated theme text>`.
- File exists at `logs/theme-replay-2026-06-24.json` with valid JSON containing keys `date`, `theme`, `items`.
- No mutation of `state.db` (check `sqlite3 state.db "SELECT COUNT(*) FROM items WHERE status='published' AND last_seen_date='2026-06-24';"` — should still return the same count as before the replay).
- No new file in `site/src/content/issues/`.

If the LLM call fails (network, quota, timeout), the failure surfaces via the existing `invoke_writer` retry-and-raise path; nothing in `state.db` is touched.

- [ ] **Step 4: Commit the helper.**

```bash
git add scripts/replay_writer.py
# only add .gitignore if it was modified in Step 1
git status --porcelain -- .gitignore | grep -q . && git add .gitignore
git commit -m "$(cat <<'EOF'
scripts: add replay_writer for cold theme verification

Reads a past day's published items from state.db, reconstructs the
writer input, invokes the writer LLM with the current prompt, and
writes the raw output to logs/theme-replay-<date>.json. Read-only
against state.db and the archived issue file. Reuses build_writer_input
and invoke_writer from write.py so replay tracks production behavior.

Used to verify prompt changes against known-bad-theme dates before
deploying. Not wired into run.sh; invoke manually per date.
EOF
)"
```

---

## Task 3: Verify the new rubric against three known-bad dates

**Files:** none modified. Read-only verification producing three JSON files under `logs/` (gitignored).

- [ ] **Step 1: Run the replay against 2026-06-24.**

Run: `python scripts/replay_writer.py 2026-06-24`

Read the resulting theme from `logs/theme-replay-2026-06-24.json`. Check against all four criteria below.

- [ ] **Step 2: Run the replay against 2026-06-27.**

Run: `python scripts/replay_writer.py 2026-06-27`

Same check against `logs/theme-replay-2026-06-27.json`.

- [ ] **Step 3: Run the replay against 2026-07-07.**

Run: `python scripts/replay_writer.py 2026-07-07`

Same check against `logs/theme-replay-2026-07-07.json`.

- [ ] **Step 4: Score each replay's theme against the four success criteria.**

For each of the three replay files, the theme must satisfy all four:

1. **Named-item count in the lede is 1 or 2** (not 3+). Count only project/paper/system names in the *lede* portion (before the territory sentence). A count of 0 is allowed if the lede is written in the "a new paper looks at X and finds Y" pattern without naming the paper.

2. **The lede frames each named item by what it *is* before quoting a number or claim.** Read the first sentence aloud: does a cold reader who has never heard of this paper understand what kind of work it is (paper studying X / postmortem of Y / benchmark measuring Z) before hitting the finding? If it starts with the project name and jumps straight to the finding, that's a fail.

3. **The territory sentence describes kinds of work, not more system names.** Look for phrases like "two more papers on…", "a batch of…", "three postmortems"; flag any specific project/paper name inside the territory sentence.

4. **The three themes read distinguishably from each other.** Print the three themes side by side (no date labels). If you can't tell which is which by content alone, at least one is still too generic.

Record a pass/fail table in your working notes for each replay × criterion.

- [ ] **Step 5: Decide next step based on scores.**

- **All three replays pass all four criteria:** proceed to Task 4.
- **1 replay fails 1-2 criteria:** the rubric is broadly working. Read the failing theme carefully — is the failure driven by the rubric or by that specific day's featured mix? If rubric, refine `prompts/write.md` and re-run the failing replay. If day-specific (e.g. genuinely no strong lede among the featured items), note it and proceed to Task 4.
- **2+ replays fail the same criterion:** the rubric needs work. Stop and surface the specific failure pattern to the user before iterating. Do not push forward silently.

- [ ] **Step 6: If any prompt refinements happened, commit them separately.**

```bash
git add prompts/write.md
git commit -m "writer: refine theme rubric after replay round <N>"
```

Then re-run the failing replays and re-score. Repeat until all three pass.

---

## Task 4: Ship

**Files:** none modified. Push to `main` and let the daily cron pick up the new prompt on the next run.

- [ ] **Step 1: Confirm branch state.**

Run: `git log --oneline origin/main..HEAD`

Expected: two commits (the prompt rewrite and the replay helper) plus any prompt-refinement commits from Task 3.

- [ ] **Step 2: Push.**

Run: `git push origin main`

Expected: fast-forward push accepted. If there are conflicts with `origin/main` (a nightly cron pushed in the meantime), rebase locally and re-push.

- [ ] **Step 3: Verify the next daily run picks up the new prompt.**

The daily cron runs at 07:00 local (`SPEC.md` §Scheduler). After the next scheduled run:

Run: `git log --oneline -5 -- site/src/content/issues/`

Expected: a new `newsletter: <date> daily run` commit landing after the push. Read the `theme` line from the newest issue file to confirm the new rubric is producing sensible output on live data.

If the next daily run produces a theme that fails the four criteria, revert the prompt commit and re-open Task 3 with the new failure as an additional replay date:

```bash
git revert <prompt-commit-sha>
git push origin main
```

Rollback is idempotent — the next daily run picks up the reverted prompt automatically.

---

## Self-review

**Spec coverage check** (against `docs/superpowers/specs/2026-07-07-front-page-theme-rewrite-design.md`):

- Lede + territory shape — Task 1 Step 4.
- Front-page test (a-d) — Task 1 Step 4.
- Source constraint — Task 1 Step 4.
- Worked examples (good, dense-bad, abstract-bad, generic-bad) — Task 1 Step 4.
- Banlist additions — Task 1 Step 4.
- Dropped rules — Task 1 Step 4 (the "name at least 3 specific things" rule is not present in the replacement text).
- Cost/risk — no explicit task; addressed structurally by the retry monitor in Task 4 Step 3.
- Testing method (replay against three known-bad dates, don't mutate state.db, restore state) — Tasks 2 and 3. The plan uses `status = 'published'` in a read-only helper rather than the spec's "move issue file aside" wording; both satisfy the spec's intent (replay against the same featured set without mutating archived history) and the helper is more robust to concurrent runs.

**Placeholder scan:** no TBDs, TODOs, or "handle edge cases" wording. All test commands have expected output. Every code step includes the exact code.

**Type/name consistency:** `build_writer_input`, `find_previous_newsletter`, `invoke_writer`, `RAW_TEXT_MAX`, `PREV_NEWSLETTER_MAX`, `READER_PROFILE`, `PROMPT_PATH`, `REPO_ROOT`, `connect`, `init_db` — all match the actual names in `scripts/write.py` and `scripts/db.py` per the earlier Read. The published-item filter uses `last_seen_date`, matching the column name in `state.db` (verified during planning).
