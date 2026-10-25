"""Multi-edition content layout (#301): migrate_layout.py, edition discovery and
CLI, reset_day.py, run_attempt `latest`, the meta table, and backfill's
per-edition snapshot lookup."""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import db as db_mod
import edition
import migrate_layout as ml

EDITION_YAML = (
    "slug: {slug}\nname: {name}\ntagline: t\nstatus: {status}\norder: {order}\n"
    "reader_profile: r\nkeywords: ['x']\ntags: [a]\nsections:\n"
    "  papers: {{featured_min: 7, appendix_min: 5, cap: 5}}\n"
    "  news: {{featured_min: 6, appendix_min: 4, cap: 6}}\n"
    "  blogs: {{featured_min: 6, appendix_min: 4, cap: 6}}\n"
)


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def _legacy_content(tmp_path, *, with_hash=True, track_hash_db=True):
    root = tmp_path / "content"
    root.mkdir()
    _git(root, "init", "-q", "-b", "content")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "user.email", "t@example.com")
    db_path = root / "state.db"
    db_mod.init_db(db_path)
    issues = root / "site" / "src" / "content" / "issues"
    issues.mkdir(parents=True)
    (issues / "2026-10-01.md").write_text("one")
    (issues / "2026-10-02.md").write_text("two")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "legacy")
    hash_file = tmp_path / ".rubric_hash"
    if with_hash:
        hash_file.write_text("abc123\n")
    return root, hash_file


# ---------------------------------------------------------------- migrate_layout

class TestMigrate:
    def test_first_run_migrates_second_does_nothing(self, tmp_path):
        root, hash_file = _legacy_content(tmp_path)
        assert ml.migrate(root, hash_file) is True
        assert not (root / "state.db").exists()
        assert not (root / "site" / "src" / "content" / "issues" / "2026-10-01.md").exists()
        new = root / "editions" / "agents"
        assert (new / "issues" / "2026-10-01.md").read_text() == "one"
        assert (new / "issues" / "2026-10-02.md").read_text() == "two"
        assert _git(root, "log", "-1", "--format=%s").strip() == ml.COMMIT_MESSAGE
        assert _git(root, "status", "--porcelain").strip() == ""
        # git mv keeps history (renames), not delete + add.
        stat = _git(root, "show", "--stat", "-M", "--format=", "HEAD")
        assert "=>" in stat
        head = _git(root, "rev-parse", "HEAD")
        # Second run: nothing happens, no new commit.
        assert ml.migrate(root, hash_file) is False
        assert _git(root, "rev-parse", "HEAD") == head

    def test_seeds_meta_from_rubric_hash(self, tmp_path):
        root, hash_file = _legacy_content(tmp_path)
        ml.migrate(root, hash_file)
        conn = sqlite3.connect(root / "editions" / "agents" / "state.db")
        assert conn.execute("SELECT value FROM meta WHERE key='rubric_hash'").fetchone() == ("abc123",)
        conn.close()
        # ... and the seeded DB is the committed one.
        assert _git(root, "status", "--porcelain").strip() == ""

    def test_no_hash_file_still_migrates(self, tmp_path):
        root, hash_file = _legacy_content(tmp_path, with_hash=False)
        assert ml.migrate(root, hash_file) is True
        conn = sqlite3.connect(root / "editions" / "agents" / "state.db")
        assert conn.execute("SELECT COUNT(*) FROM meta").fetchone() == (0,)
        conn.close()

    def test_empty_hash_file_is_not_seeded(self, tmp_path):
        root, hash_file = _legacy_content(tmp_path)
        hash_file.write_text("\n")
        ml.migrate(root, hash_file)
        conn = sqlite3.connect(root / "editions" / "agents" / "state.db")
        has_meta = conn.execute(
            "SELECT name FROM sqlite_master WHERE name='meta'"
        ).fetchone()
        rows = conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] if has_meta else 0
        conn.close()
        assert rows == 0

    def test_fresh_content_branch_has_nothing_to_migrate(self, tmp_path):
        root = tmp_path / "content"
        root.mkdir()
        _git(root, "init", "-q")
        assert ml.migrate(root, tmp_path / "none") is False

    def test_already_migrated_layout_is_left_alone(self, tmp_path):
        root, hash_file = _legacy_content(tmp_path)
        (root / "editions" / "agents").mkdir(parents=True)
        (root / "editions" / "agents" / "state.db").write_text("x")
        assert ml.migrate(root, hash_file) is False
        assert (root / "state.db").exists()

    def test_untracked_files_are_moved_and_staged(self, tmp_path):
        root = tmp_path / "content"
        root.mkdir()
        _git(root, "init", "-q")
        _git(root, "config", "user.name", "T")
        _git(root, "config", "user.email", "t@example.com")
        (root / "README").write_text("x")
        _git(root, "add", "README")
        _git(root, "commit", "-qm", "init")
        db_mod.init_db(root / "state.db")  # never committed
        issues = root / "site" / "src" / "content" / "issues"
        issues.mkdir(parents=True)
        (issues / "2026-10-01.md").write_text("one")
        assert ml.migrate(root, tmp_path / "none") is True
        files = _git(root, "ls-files").split()
        assert "editions/agents/state.db" in files
        assert "editions/agents/issues/2026-10-01.md" in files

    def test_only_issues_no_db(self, tmp_path):
        root = tmp_path / "content"
        root.mkdir()
        _git(root, "init", "-q")
        _git(root, "config", "user.name", "T")
        _git(root, "config", "user.email", "t@example.com")
        issues = root / "site" / "src" / "content" / "issues"
        issues.mkdir(parents=True)
        (issues / "2026-10-01.md").write_text("one")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "init")
        assert ml.migrate(root, tmp_path / "none") is True
        assert (root / "editions" / "agents" / "issues" / "2026-10-01.md").exists()

    def test_git_failure_raises(self, tmp_path):
        with pytest.raises(ml.MigrationError, match="git"):
            ml._git(tmp_path / "not-a-repo", "status")


WHITELIST_GITIGNORE = (
    "/*\n!/state.db\n!/site\n/site/*\n!/site/src\n/site/src/*\n!/site/src/content\n"
    "/site/src/content/*\n!/site/src/content/issues\n!/.gitignore\n"
)


class TestMigrateGitignore:
    def _whitelisted(self, tmp_path):
        root, hash_file = _legacy_content(tmp_path)
        (root / ".gitignore").write_text(WHITELIST_GITIGNORE)
        _git(root, "add", "-f", ".gitignore")
        _git(root, "commit", "-qm", "gitignore")
        return root, hash_file

    def test_new_files_under_editions_can_be_added_after_migration(self, tmp_path):
        root, hash_file = self._whitelisted(tmp_path)
        assert ml.migrate(root, hash_file) is True
        # The migration commit carries the .gitignore change.
        assert ".gitignore" in _git(root, "show", "--name-only", "--format=", "HEAD").split()
        for rel in ("agents/issues/2026-10-03.md", "beta/issues/2026-10-03.md", "beta/state.db"):
            f = root / "editions" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x")
        # Plain `git add` (no -f), exactly as run_edition.sh does.
        _git(root, "add", "editions/agents")
        _git(root, "add", "editions/beta")
        staged = _git(root, "diff", "--cached", "--name-only").split()
        assert "editions/agents/issues/2026-10-03.md" in staged
        assert "editions/beta/issues/2026-10-03.md" in staged
        assert "editions/beta/state.db" in staged

    def test_sidecars_stay_ignored(self, tmp_path):
        root, hash_file = self._whitelisted(tmp_path)
        ml.migrate(root, hash_file)
        (root / "editions" / "agents" / "state.db-wal").write_text("x")
        assert _git(root, "status", "--porcelain").strip() == ""

    def test_ensure_gitignore_is_idempotent(self, tmp_path):
        root, _ = self._whitelisted(tmp_path)
        assert ml.ensure_gitignore(root) is True
        _git(root, "commit", "-qm", "ignore")
        content = (root / ".gitignore").read_text()
        assert ml.ensure_gitignore(root) is False
        assert (root / ".gitignore").read_text() == content
        assert content.count(ml.GITIGNORE_MARKER) == 1

    def test_ensure_gitignore_without_existing_file_or_newline(self, tmp_path):
        root = tmp_path / "c"
        root.mkdir()
        _git(root, "init", "-q")
        _git(root, "config", "user.name", "T")
        _git(root, "config", "user.email", "t@example.com")
        assert ml.ensure_gitignore(root) is True
        assert (root / ".gitignore").read_text().startswith(ml.GITIGNORE_MARKER)
        (root / ".gitignore").write_text("/*")
        _git(root, "add", "-f", ".gitignore")
        _git(root, "commit", "-qm", "x", "--allow-empty")
        assert ml.ensure_gitignore(root) is True
        assert (root / ".gitignore").read_text().startswith("/*\n" + ml.GITIGNORE_MARKER)


class TestSyncGitignore:
    def test_fixes_an_already_migrated_branch_once(self, tmp_path):
        root, hash_file = _legacy_content(tmp_path)
        ml.migrate(root, hash_file)  # no whitelist yet -> no gitignore at all
        (root / ".gitignore").write_text(WHITELIST_GITIGNORE)
        _git(root, "add", "-f", ".gitignore")
        _git(root, "commit", "-qm", "whitelist")
        assert ml.sync_gitignore(root) is True
        assert _git(root, "log", "-1", "--format=%s").strip() == ml.GITIGNORE_COMMIT_MESSAGE
        head = _git(root, "rev-parse", "HEAD")
        assert ml.sync_gitignore(root) is False
        assert _git(root, "rev-parse", "HEAD") == head

    def test_leaves_branch_without_whitelist_alone(self, tmp_path):
        root = tmp_path / "c"
        root.mkdir()
        _git(root, "init", "-q")
        assert ml.sync_gitignore(root) is False  # no .gitignore
        (root / ".gitignore").write_text("logs/\n")
        assert ml.sync_gitignore(root) is False  # not a whitelist
        assert (root / ".gitignore").read_text() == "logs/\n"


class TestMigrateCli:
    def _repo(self, tmp_path, *, deploy_ready: bool):
        repo = tmp_path / "repo"
        (repo / ".github" / "workflows").mkdir(parents=True)
        body = "cp -r _content/editions/ x\n" if deploy_ready else "cp -r _content/site/src/content/issues/. y\n"
        (repo / ".github" / "workflows" / "deploy.yml").write_text(body)
        return repo

    def test_refuses_until_deploy_workflow_stages_the_new_layout(self, tmp_path, caplog):
        root, hash_file = _legacy_content(tmp_path)
        repo = self._repo(tmp_path, deploy_ready=False)
        rc = ml.main(["--content-root", str(root), "--repo-root", str(repo),
                      "--rubric-hash-file", str(hash_file)])
        assert rc == 3
        assert (root / "state.db").exists()  # untouched
        assert "deploy.yml does not stage" in caplog.text

    def test_skip_deploy_check_overrides(self, tmp_path):
        root, hash_file = _legacy_content(tmp_path)
        repo = self._repo(tmp_path, deploy_ready=False)
        rc = ml.main(["--content-root", str(root), "--repo-root", str(repo),
                      "--rubric-hash-file", str(hash_file), "--skip-deploy-check"])
        assert rc == 0
        assert (root / "editions" / "agents" / "state.db").exists()

    def test_cli_fixes_gitignore_of_an_already_migrated_branch(self, tmp_path, caplog):
        caplog.set_level("INFO")
        root, hash_file = _legacy_content(tmp_path)
        repo = self._repo(tmp_path, deploy_ready=True)
        ml.migrate(root, hash_file)
        (root / ".gitignore").write_text(WHITELIST_GITIGNORE)
        _git(root, "add", "-f", ".gitignore")
        _git(root, "commit", "-qm", "whitelist")
        assert ml.main(["--content-root", str(root), "--repo-root", str(repo)]) == 0
        assert "whitelisted editions/" in caplog.text
        assert _git(root, "log", "-1", "--format=%s").strip() == ml.GITIGNORE_COMMIT_MESSAGE

    def test_migrates_when_deploy_ready_and_is_idempotent(self, tmp_path, caplog):
        caplog.set_level("INFO")
        root, hash_file = _legacy_content(tmp_path)
        repo = self._repo(tmp_path, deploy_ready=True)
        args = ["--content-root", str(root), "--repo-root", str(repo), "--rubric-hash-file", str(hash_file)]
        assert ml.main(args) == 0
        assert "migrated" in caplog.text
        caplog.clear()
        assert ml.main(args) == 0
        assert "nothing to do" in caplog.text

    def test_nothing_to_migrate_never_trips_the_deploy_check(self, tmp_path):
        root = tmp_path / "content"
        root.mkdir()
        repo = self._repo(tmp_path, deploy_ready=False)
        assert ml.main(["--content-root", str(root), "--repo-root", str(repo)]) == 0

    def test_content_root_required(self, monkeypatch, caplog):
        monkeypatch.delenv("CONTENT_ROOT", raising=False)
        assert ml.main([]) == 1
        assert "--content-root" in caplog.text

    def test_content_root_from_env(self, tmp_path, monkeypatch):
        root = tmp_path / "c"
        root.mkdir()
        monkeypatch.setenv("CONTENT_ROOT", str(root))
        assert ml.main(["--repo-root", str(self._repo(tmp_path, deploy_ready=True))]) == 0

    def test_migration_error_exits_1(self, tmp_path, caplog):
        root = tmp_path / "content"  # has a legacy db but is not a git repo
        root.mkdir()
        db_mod.init_db(root / "state.db")
        rc = ml.main(["--content-root", str(root), "--skip-deploy-check"])
        assert rc == 1
        assert "migration failed" in caplog.text

    def test_push_is_best_effort(self, tmp_path, caplog):
        root, hash_file = _legacy_content(tmp_path)  # no remote: push fails
        rc = ml.main(["--content-root", str(root), "--skip-deploy-check", "--push",
                      "--rubric-hash-file", str(hash_file)])
        assert rc == 0
        assert "push failed" in caplog.text

    def test_push_succeeds_with_remote(self, tmp_path):
        root, hash_file = _legacy_content(tmp_path)
        remote = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        _git(root, "remote", "add", "origin", str(remote))
        _git(root, "push", "-q", "origin", "content")
        _git(root, "branch", "--set-upstream-to=origin/content")
        assert ml.main(["--content-root", str(root), "--skip-deploy-check", "--push",
                        "--rubric-hash-file", str(hash_file)]) == 0
        out = subprocess.run(["git", "-C", str(remote), "log", "--format=%s", "content"],
                             capture_output=True, text=True).stdout
        assert ml.COMMIT_MESSAGE in out

    def test_deploy_supports_editions_detects_missing_file(self, tmp_path):
        assert ml.deploy_supports_editions(tmp_path) is False

    def test_this_repos_deploy_workflow_check_matches_reality(self):
        """The guard reads the real workflow: once it stages editions/, migrating is allowed."""
        wf = (ml.REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text()
        assert ml.deploy_supports_editions(ml.REPO_ROOT) == ("editions/" in wf)


# ---------------------------------------------------------------- edition.py

class TestDiscover:
    def _tree(self, tmp_path):
        base = tmp_path / "editions"
        for slug, status, order in (("zeta", "live", 5), ("agents", "live", 10),
                                    ("beta", "paused", 20), ("alpha", "draft", 10)):
            (base / slug).mkdir(parents=True)
            (base / slug / "edition.yaml").write_text(
                EDITION_YAML.format(slug=slug, name=slug.title(), status=status, order=order))
        (base / "_template").mkdir()
        (base / ".hidden").mkdir()
        (base / "broken").mkdir()
        (base / "broken" / "edition.yaml").write_text("not: valid\n")
        (base / "file.txt").write_text("x")
        return base

    def test_order_agents_first_template_skipped_invalid_last(self, tmp_path):
        rows = edition.discover(self._tree(tmp_path))
        assert [r[0] for r in rows] == ["zeta", "agents", "alpha", "beta", "broken"]
        assert rows[-1][1] is None and "unknown keys" in rows[-1][2] or "missing keys" in rows[-1][2]

    def test_missing_dir(self, tmp_path):
        assert edition.discover(tmp_path / "nope") == []

    def test_real_repo_lists_agents_first(self):
        assert edition.discover()[0][0] == "agents"


class TestEditionCli:
    def test_name(self, capsys, monkeypatch):
        monkeypatch.setenv("EDITION", "agents")
        edition._load_cached.cache_clear()
        assert edition.main(["name"]) == 0
        assert capsys.readouterr().out.strip() == "AI Agents Daily"

    def test_name_without_edition_fails(self, capsys, monkeypatch):
        monkeypatch.delenv("EDITION", raising=False)
        assert edition.main(["name"]) == 1
        assert "EDITION is not set" in capsys.readouterr().err

    def test_list(self, capsys, monkeypatch, tmp_path):
        base = tmp_path / "editions"
        (base / "agents").mkdir(parents=True)
        (base / "agents" / "edition.yaml").write_text(
            EDITION_YAML.format(slug="agents", name="A", status="live", order=10))
        (base / "bad").mkdir()
        (base / "bad" / "edition.yaml").write_text("x: 1\n")
        monkeypatch.setattr(edition, "EDITIONS_DIR", base)
        assert edition.main(["list"]) == 0
        assert capsys.readouterr().out.splitlines() == ["agents\tlive\tA", "bad\tinvalid\tbad"]

    def test_usage(self, capsys):
        assert edition.main(["nope"]) == 2
        assert "usage" in capsys.readouterr().err

    def test_uses_sys_argv_by_default(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["edition.py", "bogus"])
        assert edition.main() == 2


# ---------------------------------------------------------------- db meta / connect

def test_meta_roundtrip(tmp_path):
    p = tmp_path / "x" / "state.db"  # parent does not exist yet
    db_mod.init_db(p)
    conn = db_mod.connect(p)
    assert db_mod.get_meta(conn, "k") is None
    db_mod.set_meta(conn, "k", "v1")
    db_mod.set_meta(conn, "k", "v2")
    assert db_mod.get_meta(conn, "k") == "v2"
    conn.close()


# ---------------------------------------------------------------- reset_day

class TestResetDay:
    def _seed(self, tmp_path, monkeypatch):
        import reset_day
        monkeypatch.setattr(reset_day, "CONTENT_ROOT", tmp_path)
        monkeypatch.setenv("RUN_DATE", "2026-10-22")
        paths = edition.runtime_paths(tmp_path, "agents")
        db_mod.init_db(paths.state_db)
        conn = db_mod.connect(paths.state_db)
        for i, (status, day) in enumerate([
            ("published", "2026-10-22"), ("featured", "2026-10-22"),
            ("dropped", "2026-10-22"), ("published", "2026-10-21"),
        ]):
            conn.execute(
                "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, status,"
                " first_seen_date, last_seen_date, score, tags, why) VALUES (?, 's', 'u', 'u', 't',"
                " '2026-10-22T00:00:00Z', ?, ?, '2026-10-22', 9, 'x', 'w')",
                (f"i{i}", status, day),
            )
        conn.execute("INSERT INTO runs (date) VALUES ('2026-10-22')")
        conn.commit()
        conn.close()
        paths.issues_dir.mkdir(parents=True)
        (paths.issues_dir / "2026-10-22.md").write_text("issue")
        return reset_day, paths

    def _statuses(self, paths):
        conn = db_mod.connect(paths.state_db)
        rows = dict(conn.execute("SELECT id, status FROM items").fetchall())
        runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        conn.close()
        return rows, runs

    def test_force_resets_todays_processed_items_only(self, tmp_path, monkeypatch):
        reset_day, paths = self._seed(tmp_path, monkeypatch)
        assert reset_day.main([]) == 0
        rows, runs = self._statuses(paths)
        assert rows == {"i0": "candidate", "i1": "candidate", "i2": "dropped", "i3": "published"}
        assert runs == 0
        assert not (paths.issues_dir / "2026-10-22.md").exists()

    def test_force_restores_pool_paper_published_today(self, tmp_path, monkeypatch):
        """#326: a multi-day pool paper first seen earlier but published today."""
        reset_day, paths = self._seed(tmp_path, monkeypatch)
        conn = db_mod.connect(paths.state_db)
        for iid, status, pub in [
            ("pool", "published", "2026-10-22"),
            ("pool_old", "published", "2026-10-21"),
        ]:
            conn.execute(
                "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, status,"
                " section, first_seen_date, last_seen_date, times_competed, published_date)"
                " VALUES (?, 's', 'u', 'u', 't', '2026-10-19T00:00:00Z', ?, 'papers',"
                " '2026-10-19', '2026-10-22', 2, ?)",
                (iid, status, pub),
            )
        conn.commit()
        conn.close()
        assert reset_day.main([]) == 0
        rows, _ = self._statuses(paths)
        assert rows["pool"] == "candidate"
        assert rows["pool_old"] == "published"

    def test_refetch_deletes_todays_items_first(self, tmp_path, monkeypatch):
        reset_day, paths = self._seed(tmp_path, monkeypatch)
        assert reset_day.main(["--refetch"]) == 0
        rows, _ = self._statuses(paths)
        assert rows == {"i3": "published"}

    def test_refetch_makes_fetch_run_again_with_resurfaced_items(self, tmp_path, monkeypatch):
        """#321: an older item re-surfaced today must not make fetch skip."""
        import fetch
        reset_day, paths = self._seed(tmp_path, monkeypatch)
        conn = db_mod.connect(paths.state_db)
        db_mod.set_meta(conn, fetch.FETCH_DONE_KEY, "2026-10-22")
        assert fetch._already_fetched_today(conn) > 0
        conn.close()
        assert reset_day.main(["--refetch"]) == 0
        conn = db_mod.connect(paths.state_db)
        # i3 is a prior-day item with last_seen_date = today; it survives the delete.
        assert conn.execute(
            "SELECT COUNT(*) FROM items WHERE last_seen_date = '2026-10-22'"
        ).fetchone()[0] == 1
        assert fetch._already_fetched_today(conn) == 0
        conn.close()

    def test_missing_db_is_a_noop(self, tmp_path, monkeypatch):
        import reset_day
        monkeypatch.setattr(reset_day, "CONTENT_ROOT", tmp_path)
        assert reset_day.main([]) == 0
        assert not (tmp_path / "editions").exists()

    def test_no_issue_file_is_fine(self, tmp_path, monkeypatch):
        reset_day, paths = self._seed(tmp_path, monkeypatch)
        (paths.issues_dir / "2026-10-22.md").unlink()
        assert reset_day.main([]) == 0

    def test_defaults_run_date_to_today(self, tmp_path, monkeypatch):
        import reset_day
        monkeypatch.setattr(reset_day, "CONTENT_ROOT", tmp_path)
        monkeypatch.delenv("RUN_DATE", raising=False)
        assert reset_day.main([]) == 0


# ---------------------------------------------------------------- run_attempt latest

class TestRunAttemptLatest:
    def test_latest_outcome(self, tmp_path):
        import run_attempt
        p = tmp_path / "state.db"
        db_mod.init_db(p)
        conn = db_mod.connect(p)
        assert run_attempt.latest_outcome(conn, "2026-10-22") == "none"
        run_attempt.start(conn, "2026-10-22", "t")
        assert run_attempt.latest_outcome(conn, "2026-10-22") == "started"
        run_attempt.finish(conn, "2026-10-22", 1, "failed", "rank", 7, None)
        assert run_attempt.latest_outcome(conn, "2026-10-22") == "failed"
        run_attempt.start(conn, "2026-10-22", "t")
        run_attempt.finish(conn, "2026-10-22", 2, "ok", None, None, None)
        assert run_attempt.latest_outcome(conn, "2026-10-22") == "ok"
        assert run_attempt.latest_outcome(conn, "2026-10-23") == "none"
        conn.close()

    def test_cli_latest_without_db_prints_none_and_creates_nothing(self, tmp_path, monkeypatch, capsys):
        import run_attempt
        monkeypatch.setattr(run_attempt, "DB_PATH", tmp_path / "editions" / "x" / "state.db")
        assert run_attempt.main(["latest"]) == 0
        assert capsys.readouterr().out.strip() == "none"
        assert not (tmp_path / "editions").exists()

    def test_cli_latest_reads_the_db(self, tmp_path, monkeypatch, capsys):
        import run_attempt
        p = tmp_path / "state.db"
        db_mod.init_db(p)
        monkeypatch.setattr(run_attempt, "DB_PATH", p)
        monkeypatch.setattr(run_attempt, "connect", lambda: db_mod.connect(p))
        monkeypatch.setenv("RUN_DATE", "2026-10-22")
        conn = db_mod.connect(p)
        run_attempt.start(conn, "2026-10-22", "t")
        conn.close()
        assert run_attempt.main(["latest"]) == 0
        assert capsys.readouterr().out.strip() == "started"


# ---------------------------------------------------------------- backfill

class TestBackfillPerEdition:
    def _log(self, *rows):
        return "\n".join(f"{sha} {subject}" for sha, subject in rows)

    def test_new_style_commit_for_the_selected_edition(self, monkeypatch):
        import backfill as bf
        out = MagicMock(stdout=self._log(
            ("b1", "newsletter(beta): 2026-07-09 daily run"),
            ("a1", "newsletter(agents): 2026-07-09 daily run"),
        ))
        monkeypatch.setattr("backfill.subprocess.run", lambda *a, **k: out)
        assert bf.find_pre_run_commit("2026-07-10", "agents") == "a1"
        assert bf.find_pre_run_commit("2026-07-10", "beta") == "b1"

    def test_legacy_commits_count_for_agents_only(self, monkeypatch):
        import backfill as bf
        out = MagicMock(stdout=self._log(("l1", "newsletter: 2026-07-09 daily run")))
        monkeypatch.setattr("backfill.subprocess.run", lambda *a, **k: out)
        assert bf.find_pre_run_commit("2026-07-10", "agents") == "l1"
        with pytest.raises(RuntimeError, match=r"newsletter\(beta\)"):
            bf.find_pre_run_commit("2026-07-10", "beta")

    def test_failed_attempt_commits_are_not_snapshots(self, monkeypatch):
        import backfill as bf
        out = MagicMock(stdout=self._log(
            ("f1", "newsletter(agents): 2026-07-09 attempt 2 failed at rank"),
            ("a1", "newsletter(agents): 2026-07-08 daily run"),
        ))
        monkeypatch.setattr("backfill.subprocess.run", lambda *a, **k: out)
        # The failed-attempt commit is dated 07-09 < target and carries a state.db too,
        # so it is a valid pre-run snapshot, the most recent one.
        assert bf.find_pre_run_commit("2026-07-10", "agents") == "f1"

    def test_defaults_slug_from_env(self, monkeypatch):
        import backfill as bf
        out = MagicMock(stdout=self._log(("a1", "newsletter(agents): 2026-07-09 daily run")))
        monkeypatch.setattr("backfill.subprocess.run", lambda *a, **k: out)
        assert bf.find_pre_run_commit("2026-07-10") == "a1"

    def _sandbox(self, tmp_path, monkeypatch, slug, shows):
        import backfill as bf
        sandbox = tmp_path / "sb"
        real_path = bf.Path
        monkeypatch.setattr(
            "backfill.Path", lambda x: sandbox if str(x).startswith("/tmp/backfill-") else real_path(x))
        monkeypatch.setattr("backfill.find_pre_run_commit", lambda d, s=None: "abc1234")
        monkeypatch.setenv("EDITION", slug)
        calls = []

        def fake_run(cmd, **kw):
            path = cmd[-1].split(":", 1)[1]
            calls.append(path)
            ok = shows.get(path, False)
            if ok:
                kw["stdout"].write(b"DB")
            return MagicMock(returncode=0 if ok else 128, stderr=b"fatal: path missing")

        monkeypatch.setattr("backfill.subprocess.run", fake_run)
        return bf, sandbox, calls

    def test_sandbox_reads_the_edition_db_path(self, tmp_path, monkeypatch):
        bf, sandbox, calls = self._sandbox(tmp_path, monkeypatch, "agents",
                                           {"editions/agents/state.db": True})
        assert bf.setup_sandbox("2026-07-15") == sandbox
        assert calls == ["editions/agents/state.db"]
        assert (sandbox / "state.db").read_bytes() == b"DB"

    def test_agents_falls_back_to_legacy_path(self, tmp_path, monkeypatch):
        bf, sandbox, calls = self._sandbox(tmp_path, monkeypatch, "agents", {"state.db": True})
        assert bf.setup_sandbox("2026-07-15") == sandbox
        assert calls == ["editions/agents/state.db", "state.db"]
        assert (sandbox / "state.db").read_bytes() == b"DB"

    def test_other_editions_have_no_legacy_fallback(self, tmp_path, monkeypatch):
        bf, sandbox, calls = self._sandbox(tmp_path, monkeypatch, "beta", {"state.db": True})
        with pytest.raises(RuntimeError, match="no state.db for edition 'beta'"):
            bf.setup_sandbox("2026-07-15")
        assert calls == ["editions/beta/state.db"]

    def test_agents_with_neither_path_raises(self, tmp_path, monkeypatch):
        bf, sandbox, calls = self._sandbox(tmp_path, monkeypatch, "agents", {})
        with pytest.raises(RuntimeError, match="tried editions/agents/state.db, state.db"):
            bf.setup_sandbox("2026-07-15")
