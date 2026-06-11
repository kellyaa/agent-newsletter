"""Reconstruct a missed day's newsletter from the candidate pool snapshot
that was committed before that day's run was supposed to fire.

Usage:
  uv run python scripts/backfill.py --date 2026-06-10

What it does, end-to-end:
  1. Finds the last `newsletter: <date> daily run` commit dated < --date and
     extracts its state.db into a sandbox under /tmp/backfill-<date>/.
  2. Re-binds db.connect/db.init_db to the sandbox so the real state.db is
     never touched.
  3. Re-applies prefilter's age-out rule using --date as the synthetic 'now'
     (julianday('now') would otherwise overshoot by however many days are
     between the snapshot and --date).
  4. Builds the candidates.json snapshot from the surviving candidate rows.
  5. Runs the ranker — usually with no LLM call, since on a missed-day
     backfill all candidates are typically prescored from prior runs.
  6. Calls the writer pinned to --date and writes
     site/src/content/issues/<date>.md.

Limitations, all forced by the missing fetch:
  - Papers only. News and blogs items don't survive across days in the
    candidate pool — they're scored and sealed (featured/appendix/dropped)
    the same day they're fetched. Without that day's network fetch, no
    news/blogs are recoverable. The reader sees a papers-only issue with
    empty appendix.
  - No `runs` row is recorded in the live state.db. The site renders fine
    from the issue file alone; per-day stats queries (`SELECT … FROM runs`)
    won't show the backfilled date.
  - times_competed counters in the live DB are not bumped for the synthetic
    day, so the multi-day pool's aging is off by one against what would
    have happened with a real run. Minor — affects when a paper ages out
    by at most one day.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("backfill")


def find_pre_run_commit(target_date: str) -> str:
    """Return the SHA of the last newsletter commit dated strictly before
    target_date. That commit's state.db is the snapshot the missed run
    would have started from.
    """
    out = subprocess.run(
        ["git", "log", "--format=%H %s", "--all"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    # Parse the oneline log; pick newsletter commits dated < target.
    for line in out.splitlines():
        sha, _, subject = line.partition(" ")
        # Match exactly the run.sh commit message format: "newsletter: YYYY-MM-DD daily run"
        if not subject.startswith("newsletter: "):
            continue
        rest = subject[len("newsletter: "):]
        date_str = rest.split(" ", 1)[0]
        if date_str < target_date:
            return sha
    raise RuntimeError(
        f"no `newsletter: <date> daily run` commit dated before {target_date}"
    )


def setup_sandbox(target_date: str) -> Path:
    """Extract the pre-run state.db into /tmp/backfill-<date>/state.db."""
    sandbox = Path(f"/tmp/backfill-{target_date}")
    if sandbox.exists():
        log.info("sandbox %s exists — wiping for a clean run", sandbox)
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)

    sha = find_pre_run_commit(target_date)
    log.info("using pre-run snapshot from commit %s", sha[:12])
    db_path = sandbox / "state.db"
    with db_path.open("wb") as f:
        subprocess.run(
            ["git", "show", f"{sha}:state.db"],
            cwd=REPO, check=True, stdout=f,
        )
    return sandbox


def bind_db_to_sandbox(sandbox_db: Path) -> None:
    """Patch db.connect/init_db so all callers land on the sandbox.

    Must run BEFORE rank/write modules are imported, because their
    `from db import connect, init_db` captures the function references at
    import time. Patching db_mod.connect first means those imports pick up
    the patched function.
    """
    import db as db_mod

    orig_connect = db_mod.connect
    orig_init_db = db_mod.init_db

    def sandbox_connect(db_path=None):
        return orig_connect(db_path or sandbox_db)

    def sandbox_init_db(db_path=None):
        return orig_init_db(db_path or sandbox_db)

    db_mod.DB_PATH = sandbox_db
    db_mod.connect = sandbox_connect
    db_mod.init_db = sandbox_init_db


def age_out_for_synthetic_date(conn, target_date: str) -> int:
    """Mirror prefilter.main()'s age-out SQL with target_date as 'now'.

    Without this, papers that were live on the snapshot day but should have
    aged out by the target date would still appear in the candidate pool.
    """
    import prefilter as prefilter_mod
    cur = conn.execute(
        """
        UPDATE items
        SET status = 'dropped'
        WHERE status = 'candidate'
          AND section = 'papers'
          AND (times_competed >= ?
               OR julianday(?) - julianday(COALESCE(published_at, fetched_at)) >= ?)
        """,
        (
            prefilter_mod.PAPER_POOL_MAX_COMPETES,
            target_date,
            prefilter_mod.PAPER_POOL_MAX_AGE_DAYS,
        ),
    )
    conn.commit()
    return cur.rowcount or 0


def build_candidates_snapshot(conn) -> dict:
    """Reproduce prefilter.py's candidates.json grouping from the DB.

    Only candidates already in `status = 'candidate'` are emitted — this
    matches a normal idempotent re-run of prefilter when there are no new
    items to gate. Papers with cached scores go into `papers_prescored`;
    unscored papers (rare on a backfill, but possible) go into `papers`.
    News and blogs sections come out empty by construction since those
    rows don't survive across days.
    """
    grouped: dict[str, list[dict]] = {
        "papers": [],
        "papers_prescored": [],
        "news": [],
        "blogs": [],
    }
    rows = conn.execute(
        """
        SELECT id, source, url, title, author, published_at, raw_text,
               section, score, tags, why
        FROM items
        WHERE status = 'candidate'
        """
    ).fetchall()
    for r in rows:
        d = dict(r)
        emitted = {
            "id": d["id"],
            "source": d["source"],
            "url": d["url"],
            "title": d["title"],
            "author": d["author"],
            "published_at": d["published_at"],
            "raw_text": d["raw_text"],
        }
        section = d["section"] or "blogs"
        if section == "papers" and d["score"] is not None:
            try:
                tags = json.loads(d["tags"]) if d["tags"] else []
            except (TypeError, ValueError):
                tags = []
            emitted["score"] = d["score"]
            emitted["tags"] = tags
            emitted["why"] = d["why"] or ""
            grouped["papers_prescored"].append(emitted)
        elif section in grouped:
            grouped[section].append(emitted)
    return grouped


def rank_with_optional_llm(grouped: dict) -> dict:
    """Rank candidates and return the id → decision map.

    If there are unscored items (rare on a backfill), invoke the ranker LLM
    for each non-empty section. Prescored papers always pass through directly.
    """
    import rank as rank_mod

    scored_by_section: dict[str, list[dict]] = {"papers": [], "news": [], "blogs": []}

    # Prescored papers: pass through with cached scores.
    for it in grouped["papers_prescored"]:
        scored_by_section["papers"].append({
            "id": it["id"],
            "score": it["score"],
            "tags": it.get("tags", []),
            "why": it.get("why", ""),
        })

    # Any section with unscored items needs the LLM. We expect this branch
    # not to fire on a clean missed-day backfill, but handle it defensively.
    rubric = (REPO / "prompts" / "rank.md").read_text()
    for section in ("papers", "news", "blogs"):
        items = grouped.get(section, [])
        if not items:
            continue
        log.info("invoking ranker LLM for %s (%d unscored)", section, len(items))
        prompt = rank_mod.build_prompt(section, items, rubric)
        scored = rank_mod.invoke_ranker(prompt, label=section)
        scored_by_section[section].extend(scored)

    return rank_mod.assign_statuses(scored_by_section)


def persist_decisions(conn, decisions: dict) -> dict[str, int]:
    """Write rank decisions to the sandbox DB so write.py can read them
    via the normal `WHERE status = 'featured'` path.
    """
    import rank as rank_mod
    counts: dict[str, int] = {}
    for item_id, d in decisions.items():
        clean_tags = [t for t in d["tags"] if t in rank_mod.VALID_TAGS]
        conn.execute(
            "UPDATE items SET score = ?, tags = ?, why = ?, status = ? WHERE id = ?",
            (d["score"], json.dumps(clean_tags), d["why"], d["status"], item_id),
        )
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    conn.commit()
    return counts


def run_writer_for_date(conn, target_date: str, force: bool) -> Path | None:
    """Pin write.py's logic to target_date and write the issue file."""
    import write as write_mod

    issues_dir = REPO / "site" / "src" / "content" / "issues"
    out_path = issues_dir / f"{target_date}.md"
    if out_path.exists() and not force:
        log.error(
            "%s already exists (%d bytes); pass --force to overwrite",
            out_path, out_path.stat().st_size,
        )
        return None

    featured, appendix_by_section, metadata = write_mod.load_today_items(
        conn, target_date,
    )
    appendix_total = sum(len(v) for v in appendix_by_section.values())
    log.info(
        "writer input: featured=%d (papers=%d news=%d blogs=%d) appendix=%d",
        len(featured),
        metadata["items_featured_papers"],
        metadata["items_featured_news"],
        metadata["items_featured_blogs"],
        appendix_total,
    )
    if not featured and not appendix_total:
        log.warning("nothing to publish for %s; aborting", target_date)
        return None

    prev = write_mod.find_previous_newsletter(target_date)
    payload = write_mod.build_writer_input(
        target_date, featured, appendix_by_section, metadata, prev,
    )
    rubric = write_mod.PROMPT_PATH.read_text()
    prompt = (
        "You are writing today's AI Agents newsletter prose. The input JSON "
        "is below; the rubric and output schema follow.\n\n"
        f"## Input\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        f"---\n\n{rubric}"
    )

    if featured:
        writer_output = write_mod.invoke_writer(prompt)
    else:
        writer_output = {"theme": None, "items": []}

    issue_md = write_mod.assemble_issue(
        target_date, featured, appendix_by_section, metadata, writer_output,
    )
    issues_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(issue_md)
    log.info("wrote %s (%d bytes)", out_path, len(issue_md))
    return out_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill a missed day's newsletter from the pre-run DB snapshot.",
    )
    p.add_argument(
        "--date", required=True,
        help="Target newsletter date (YYYY-MM-DD). Must be strictly after the latest committed newsletter.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing issue file at site/src/content/issues/<date>.md.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        log.error("--date must be YYYY-MM-DD, got %r", args.date)
        return 2

    sandbox = setup_sandbox(args.date)
    bind_db_to_sandbox(sandbox / "state.db")

    # Imports must come AFTER bind_db_to_sandbox so their `from db import connect`
    # captures the patched function.
    import db as db_mod  # noqa: F401  (already patched, just ensure schema migrations run)
    db_mod.init_db()
    conn = db_mod.connect()

    aged = age_out_for_synthetic_date(conn, args.date)
    if aged:
        log.info("aged out %d papers candidates for synthetic %s", aged, args.date)

    grouped = build_candidates_snapshot(conn)
    log.info(
        "candidates: papers=%d prescored=%d news=%d blogs=%d",
        len(grouped["papers"]), len(grouped["papers_prescored"]),
        len(grouped["news"]), len(grouped["blogs"]),
    )
    if grouped["news"] or grouped["blogs"]:
        log.warning(
            "found news/blogs candidates in the snapshot — unusual; backfills "
            "are typically papers-only since news/blogs don't persist."
        )

    decisions = rank_with_optional_llm(grouped)
    counts = persist_decisions(conn, decisions)
    log.info(
        "rank decisions: featured=%d appendix=%d dropped=%d candidate=%d",
        counts.get("featured", 0), counts.get("appendix", 0),
        counts.get("dropped", 0), counts.get("candidate", 0),
    )

    out = run_writer_for_date(conn, args.date, args.force)
    conn.close()
    return 0 if out is not None else 1


if __name__ == "__main__":
    sys.exit(main())
