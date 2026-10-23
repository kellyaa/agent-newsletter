"""One-time content-branch migration to the multi-edition layout (#301).

    python scripts/migrate_layout.py [--content-root DIR] [--push]

Before::

    state.db
    site/src/content/issues/YYYY-MM-DD.md

After (``agents`` was the only edition that ever existed)::

    editions/agents/state.db
    editions/agents/issues/YYYY-MM-DD.md

It also moves the cached rubric hash out of the untracked ``.rubric_hash`` file
into the ``meta`` table of the migrated DB, so the first run after the move
does not needlessly re-score the paper pool. Everything is committed as
``content: migrate to multi-edition layout``.

Idempotent: it does nothing if ``editions/agents/state.db`` already exists, or
if there is no legacy layout to migrate (a fresh content branch).

Exit codes: 0 migrated or nothing to do; 1 error; 3 refused because the deploy
workflow on ``main`` cannot yet stage the new layout (migrating then would
leave the site build with nothing to copy). ``--skip-deploy-check`` overrides.

Deliberately imports neither ``db`` nor ``edition``: run.sh calls it before any
edition is selected, and ``db`` refuses to import without ``EDITION``.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEGACY_EDITION = "agents"
COMMIT_MESSAGE = "content: migrate to multi-edition layout"

log = logging.getLogger("migrate_layout")


class MigrationError(Exception):
    pass


def deploy_supports_editions(repo_root: Path) -> bool:
    """True when the deploy workflow stages ``editions/<slug>/issues``."""
    wf = repo_root / ".github" / "workflows" / "deploy.yml"
    return wf.is_file() and "editions/" in wf.read_text()


def _git(content_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(content_root), *args], capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise MigrationError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _is_tracked(content_root: Path, rel: str) -> bool:
    return bool(_git(content_root, "ls-files", "--", rel).strip())


def _move(content_root: Path, src: str, dst: str) -> None:
    """``git mv`` when tracked, a plain rename otherwise; dst is staged either way."""
    (content_root / dst).parent.mkdir(parents=True, exist_ok=True)
    if _is_tracked(content_root, src):
        _git(content_root, "mv", src, dst)
    else:
        (content_root / src).rename(content_root / dst)
        _git(content_root, "add", "--", dst)


def _seed_meta(db_path: Path, rubric_hash: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('rubric_hash', ?)",
            (rubric_hash,),
        )
        conn.commit()
    finally:
        conn.close()


def migrate(content_root: Path, rubric_hash_file: Path) -> bool:
    """Migrate in place and commit. Returns False when there was nothing to do."""
    new_db = content_root / "editions" / LEGACY_EDITION / "state.db"
    old_db = content_root / "state.db"
    old_issues = content_root / "site" / "src" / "content" / "issues"
    legacy_files = sorted(old_issues.glob("*.md")) if old_issues.is_dir() else []

    if new_db.exists():
        return False
    if not old_db.exists() and not legacy_files:
        return False

    base = f"editions/{LEGACY_EDITION}"
    if old_db.exists():
        # Fold the WAL into the main file before it moves; sidecars don't follow a git mv.
        conn = sqlite3.connect(old_db)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        _move(content_root, "state.db", f"{base}/state.db")
        for sidecar in ("state.db-wal", "state.db-shm"):
            (content_root / sidecar).unlink(missing_ok=True)
    for f in legacy_files:
        _move(content_root, f"site/src/content/issues/{f.name}", f"{base}/issues/{f.name}")

    if new_db.exists() and rubric_hash_file.is_file():
        h = rubric_hash_file.read_text().strip()
        if h:
            _seed_meta(new_db, h)
            _git(content_root, "add", "--", f"{base}/state.db")

    _git(content_root, "commit", "-q", "-m", COMMIT_MESSAGE)
    return True


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--content-root", default=os.environ.get("CONTENT_ROOT"),
                    help="content worktree (default: $CONTENT_ROOT)")
    ap.add_argument("--rubric-hash-file", default=str(REPO_ROOT / ".rubric_hash"),
                    help="legacy cached rubric hash to seed meta from")
    ap.add_argument("--push", action="store_true", help="push the migration commit (best effort)")
    ap.add_argument("--skip-deploy-check", action="store_true",
                    help="migrate even if deploy.yml cannot stage the new layout")
    ap.add_argument("--repo-root", default=str(REPO_ROOT), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if not args.content_root:
        log.error("--content-root (or $CONTENT_ROOT) is required")
        return 1
    content_root = Path(args.content_root)
    new_db = content_root / "editions" / LEGACY_EDITION / "state.db"
    needs = not new_db.exists() and (
        (content_root / "state.db").exists()
        or any((content_root / "site" / "src" / "content" / "issues").glob("*.md"))
    )
    if needs and not args.skip_deploy_check and not deploy_supports_editions(Path(args.repo_root)):
        log.error(
            "refusing to migrate: .github/workflows/deploy.yml does not stage "
            "editions/<slug>/issues yet, so the next deploy would find nothing to copy. "
            "Land the deploy workflow change first (or pass --skip-deploy-check)."
        )
        return 3
    try:
        migrated = migrate(content_root, Path(args.rubric_hash_file))
    except (MigrationError, OSError, sqlite3.Error) as e:
        log.error("migration failed: %s", e)
        return 1
    if not migrated:
        log.info("layout already migrated (or nothing to migrate); nothing to do")
        return 0
    log.info("migrated %s to the multi-edition layout", content_root)
    if args.push:
        try:
            _git(content_root, "push", "--quiet")
        except MigrationError as e:
            log.warning("push failed (the next edition push will carry it): %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
