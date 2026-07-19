"""Tests for usage_sidecar (issue #13: runs-table token/cost accounting)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    """Route sidecar writes into a per-test tmp dir."""
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    import importlib
    import db
    importlib.reload(db)
    import usage_sidecar
    importlib.reload(usage_sidecar)
    (tmp_path / "logs").mkdir(exist_ok=True)
    yield tmp_path


def _prices_file(tmp_path: Path) -> Path:
    p = tmp_path / "prices.json"
    p.write_text(json.dumps({
        "gpt-4o-mini": {"in": 0.15, "out": 0.60},
        "claude-sonnet-4-5": {"in": 3.00, "out": 15.00},
    }))
    return p


def test_flush_and_aggregate_single_model(tmp_path, monkeypatch):
    import usage_sidecar
    monkeypatch.setenv("LLM_PRICES_PATH", str(_prices_file(tmp_path)))
    usage_sidecar.flush("rank", "2026-07-13", [
        {"label": "papers", "model": "gpt-4o-mini",
         "prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
    ])
    result = usage_sidecar.aggregate("2026-07-13")
    assert result["tokens_in"] == 1000
    assert result["tokens_out"] == 100
    # 1000 * 0.15/M + 100 * 0.60/M = 0.00015 + 0.00006 = 0.00021
    assert result["cost_usd"] == pytest.approx(0.00021, rel=1e-6)


def test_aggregate_no_sidecars_returns_nulls():
    import usage_sidecar
    result = usage_sidecar.aggregate("1999-01-01")
    assert result == {"tokens_in": None, "tokens_out": None, "cost_usd": None}


def test_unknown_model_gives_null_cost(tmp_path, monkeypatch):
    import usage_sidecar
    monkeypatch.setenv("LLM_PRICES_PATH", str(_prices_file(tmp_path)))
    usage_sidecar.flush("rank", "2026-07-13", [
        {"label": "papers", "model": "some-unknown-model",
         "prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550},
    ])
    result = usage_sidecar.aggregate("2026-07-13")
    assert result["tokens_in"] == 500
    assert result["tokens_out"] == 50
    assert result["cost_usd"] is None


def test_multiple_stages_summed(tmp_path, monkeypatch):
    import usage_sidecar
    monkeypatch.setenv("LLM_PRICES_PATH", str(_prices_file(tmp_path)))
    usage_sidecar.flush("rank", "2026-07-13", [
        {"label": "papers", "model": "gpt-4o-mini",
         "prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
        {"label": "news", "model": "gpt-4o-mini",
         "prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550},
    ])
    usage_sidecar.flush("write", "2026-07-13", [
        {"label": "writer", "model": "gpt-4o-mini",
         "prompt_tokens": 2000, "completion_tokens": 800, "total_tokens": 2800},
    ])
    result = usage_sidecar.aggregate("2026-07-13")
    assert result["tokens_in"] == 3500
    assert result["tokens_out"] == 950


def test_flush_overwrites_prior_sidecar(tmp_path):
    import usage_sidecar
    usage_sidecar.flush("rank", "2026-07-13", [
        {"label": "x", "model": "m", "prompt_tokens": 1,
         "completion_tokens": 1, "total_tokens": 2},
    ])
    usage_sidecar.flush("rank", "2026-07-13", [
        {"label": "y", "model": "m", "prompt_tokens": 5,
         "completion_tokens": 5, "total_tokens": 10},
    ])
    result = usage_sidecar.aggregate("2026-07-13")
    # Second flush must replace, not append.
    assert result["tokens_in"] == 5
    assert result["tokens_out"] == 5


def test_malformed_sidecar_is_skipped(tmp_path, monkeypatch):
    import usage_sidecar
    (tmp_path / "logs" / "usage-2026-07-13-corrupt.json").write_text("{not json")
    result = usage_sidecar.aggregate("2026-07-13")
    # No good entries → all None
    assert result == {"tokens_in": None, "tokens_out": None, "cost_usd": None}


def test_malformed_prices_file_ignored(tmp_path, monkeypatch):
    import usage_sidecar
    bad = tmp_path / "prices.json"
    bad.write_text("not json")
    monkeypatch.setenv("LLM_PRICES_PATH", str(bad))
    usage_sidecar.flush("rank", "2026-07-13", [
        {"label": "x", "model": "gpt-4o-mini", "prompt_tokens": 1,
         "completion_tokens": 1, "total_tokens": 2},
    ])
    result = usage_sidecar.aggregate("2026-07-13")
    # Prices unreadable → unknown_models → NULL cost, tokens still counted.
    assert result["tokens_in"] == 1
    assert result["cost_usd"] is None


def test_record_usage_appends_and_reset_clears():
    from llm import get_usage_log, record_usage, reset_usage_log
    reset_usage_log()
    assert get_usage_log() == []
    record_usage(label="papers", model="m", prompt_tokens=10,
                 completion_tokens=5, total_tokens=15)
    log = get_usage_log()
    assert len(log) == 1
    assert log[0]["prompt_tokens"] == 10
    reset_usage_log()
    assert get_usage_log() == []
