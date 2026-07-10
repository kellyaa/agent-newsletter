"""Tests for db.py _migrate() — ALTER TABLE migration paths."""
from __future__ import annotations

import sqlite3

import pytest


class TestDbMigrate:
    def _create_old_db(self, db_path, missing_cols):
        import db as db_mod
        db_mod.init_db(db_path)
        conn = sqlite3.connect(db_path)
        cols_info = conn.execute("PRAGMA table_info(items)").fetchall()
        all_cols = [row[1] for row in cols_info]
        keep_cols = [c for c in all_cols if c not in missing_cols]
        col_defs = ", ".join(keep_cols)
        conn.execute("BEGIN")
        conn.execute(f"CREATE TABLE items_new AS SELECT {col_defs} FROM items")
        conn.execute("DROP TABLE items")
        conn.execute("ALTER TABLE items_new RENAME TO items")
        conn.commit()
        conn.close()

    def test_adds_keyword_gate_bypass(self, tmp_path):
        import db as db_mod
        db_path = tmp_path / "old.db"
        self._create_old_db(db_path, ["keyword_gate_bypass"])
        conn = sqlite3.connect(db_path)
        cols_before = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        conn.close()
        assert "keyword_gate_bypass" not in cols_before
        db_mod.init_db(db_path)
        conn = sqlite3.connect(db_path)
        cols_after = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        conn.close()
        assert "keyword_gate_bypass" in cols_after

    def test_adds_recency_days_override(self, tmp_path):
        import db as db_mod
        db_path = tmp_path / "old.db"
        self._create_old_db(db_path, ["recency_days_override"])
        db_mod.init_db(db_path)
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        conn.close()
        assert "recency_days_override" in cols

    def test_adds_times_competed(self, tmp_path):
        import db as db_mod
        db_path = tmp_path / "old.db"
        self._create_old_db(db_path, ["times_competed"])
        db_mod.init_db(db_path)
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        conn.close()
        assert "times_competed" in cols

    def test_adds_all_three_missing_columns(self, tmp_path):
        import db as db_mod
        db_path = tmp_path / "old.db"
        self._create_old_db(db_path, ["keyword_gate_bypass", "recency_days_override", "times_competed"])
        db_mod.init_db(db_path)
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        conn.close()
        assert "keyword_gate_bypass" in cols
        assert "recency_days_override" in cols
        assert "times_competed" in cols

    def test_idempotent_on_modern_db(self, tmp_path):
        import db as db_mod
        db_path = tmp_path / "modern.db"
        db_mod.init_db(db_path)
        db_mod.init_db(db_path)  # second call must not raise
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        conn.close()
        assert "keyword_gate_bypass" in cols
