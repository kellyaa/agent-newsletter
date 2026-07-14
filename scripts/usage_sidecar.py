"""Per-stage token/cost usage sidecar files.

Each pipeline stage that calls the LLM (rank, write) flushes its accumulated
usage log to a JSON file in logs/. publish.py then reads all today's
sidecars and writes a single aggregated row to the `runs` table
(tokens_in, tokens_out, cost_usd).

Sidecar shape:
    {
      "stage": "rank",                     # rank | write | ...
      "date":  "2026-07-13",
      "entries": [
        {"label": "papers", "model": "gpt-4o-mini",
         "prompt_tokens": 12345, "completion_tokens": 678, "total_tokens": 13023},
        ...
      ]
    }

Cost estimation uses a small price table in prices.json (USD per million
tokens for input/output). Missing models produce a NULL cost_usd rather
than an error — this is an audit log, not a billing system.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable

from db import CONTENT_ROOT, REPO_ROOT

log = logging.getLogger("usage_sidecar")

# LOGS_DIR lives under CONTENT_ROOT so per-test tmp_path fixtures (which
# set CONTENT_ROOT to a scratch dir) can't cross-contaminate the repo's
# real logs/ directory. The default price table is bundled next to
# pyproject.toml and discovered relative to REPO_ROOT.
LOGS_DIR = CONTENT_ROOT / "logs"
PRICES_PATH_ENV = "LLM_PRICES_PATH"
DEFAULT_PRICES_PATH = REPO_ROOT / "prices.json"


def sidecar_path(stage: str, date: str) -> Path:
    """Deterministic path so publish.py can find today's sidecars."""
    return LOGS_DIR / f"usage-{date}-{stage}.json"


def flush(stage: str, date: str, entries: Iterable[dict[str, Any]]) -> Path:
    """Write a sidecar. Overwrites any existing file for the same
    (stage, date) so re-runs don't double-count on retry."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = sidecar_path(stage, date)
    payload = {
        "stage": stage,
        "date": date,
        "entries": [dict(e) for e in entries],
    }
    path.write_text(json.dumps(payload, indent=2))
    log.info("wrote usage sidecar: %s (%d entries)", path, len(payload["entries"]))
    return path


def _load_prices() -> dict[str, dict[str, float]]:
    """Load the model → {in, out} USD-per-million-tokens table. Absent or
    unreadable file → empty map (cost estimate falls back to NULL)."""
    override = os.environ.get(PRICES_PATH_ENV)
    path = Path(override) if override else DEFAULT_PRICES_PATH
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("could not read %s: %s", path, e)
        return {}
    if not isinstance(raw, dict):
        log.warning("%s is not a JSON object; ignoring", path)
        return {}
    out: dict[str, dict[str, float]] = {}
    for model, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        try:
            out[model] = {"in": float(entry["in"]), "out": float(entry["out"])}
        except (KeyError, TypeError, ValueError):
            log.warning("skipping malformed price entry for %r", model)
    return out


def aggregate(date: str) -> dict[str, Any]:
    """Sum all today's sidecar entries into a single {tokens_in, tokens_out,
    cost_usd} dict. cost_usd is None when at least one model used has no
    entry in the price table (partial estimates are misleading)."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    prices = _load_prices()
    tokens_in = 0
    tokens_out = 0
    unknown_models: set[str] = set()
    any_entries = False
    for path in sorted(LOGS_DIR.glob(f"usage-{date}-*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("skipping unreadable sidecar %s: %s", path, e)
            continue
        for entry in data.get("entries", []):
            any_entries = True
            model = entry.get("model", "")
            pin = int(entry.get("prompt_tokens", 0) or 0)
            pout = int(entry.get("completion_tokens", 0) or 0)
            tokens_in += pin
            tokens_out += pout
            if model not in prices:
                unknown_models.add(model)

    cost_usd: float | None
    if not any_entries:
        return {"tokens_in": None, "tokens_out": None, "cost_usd": None}
    if unknown_models:
        log.warning(
            "cost_usd = NULL: no price entry for models: %s",
            sorted(unknown_models),
        )
        cost_usd = None
    else:
        # Recompute per-entry so different models bill at different rates.
        cost = 0.0
        for path in sorted(LOGS_DIR.glob(f"usage-{date}-*.json")):
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            for entry in data.get("entries", []):
                model = entry.get("model", "")
                pin = int(entry.get("prompt_tokens", 0) or 0)
                pout = int(entry.get("completion_tokens", 0) or 0)
                p = prices[model]
                cost += (pin * p["in"] + pout * p["out"]) / 1_000_000.0
        cost_usd = round(cost, 6)

    return {"tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd}
