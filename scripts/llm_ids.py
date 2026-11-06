"""Short per-call aliases for the ids an LLM is asked to echo back.

Both LLM stages key the model's output on an item's primary key, a 64-char
sha256 hex (``urls.item_id``). Asking a model to copy that verbatim goes wrong
in a few recurring ways: a typo in the hex, a ``_dup`` suffix, or an entry
that is silently dropped. Rather than patching each shape after the fact, the
prompt carries a short alias (``i1`` … ``iN``) per item and the response is
mapped back to the real ids here, once, for ``rank.py`` and ``write.py``.

Pure helpers, no I/O. Stages call :func:`alias_items` before building the
prompt and :func:`resolve_ids` on the parsed response; they must not parse LLM
ids themselves (``tests/test_llm_ids_single_source.py`` enforces this).
"""
from __future__ import annotations

import logging

log = logging.getLogger("llm_ids")

ALIAS_PREFIX = "i"
DUP_SUFFIX = "_dup"


def alias_items(items: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Return shallow copies of ``items`` with ``id`` replaced by ``i1…iN``.

    The aliases are per call (numbered in input order), and the second value
    maps each alias back to the real id. The input items are not modified.
    """
    aliased: list[dict] = []
    alias_to_id: dict[str, str] = {}
    for n, item in enumerate(items, start=1):
        alias = f"{ALIAS_PREFIX}{n}"
        alias_to_id[alias] = item["id"]
        aliased.append({**item, "id": alias})
    return aliased, alias_to_id


def _norm(raw: str) -> str:
    return raw.strip().lower()


def resolve_ids(
    entries: list, alias_to_id: dict[str, str]
) -> tuple[list, list[str]]:
    """Map the ids in an LLM response back to real ids.

    Returns ``(resolved, unknown)``. ``resolved`` keeps the input order, with
    each recognised entry's ``id`` replaced by the real id; ``unknown`` lists
    the ids that matched nothing (those entries are left out of ``resolved``).

    Recognised: an alias, ignoring case and surrounding whitespace; a real id
    that was sent (so replays of older responses still resolve); and either of
    those with a ``_dup`` suffix, but only when no other entry already carries
    the base id. Entries that are not dicts, or whose ``id`` is not a string,
    pass through untouched: shape validation belongs to the calling stage.
    """
    known: dict[str, str] = {_norm(a): r for a, r in alias_to_id.items()}
    for real in alias_to_id.values():
        known.setdefault(_norm(real), real)
    present = {
        _norm(e["id"]) for e in entries
        if isinstance(e, dict) and isinstance(e.get("id"), str)
    }

    resolved: list = []
    unknown: list[str] = []
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str):
            resolved.append(e)
            continue
        key = _norm(e["id"])
        if key not in known:
            base = key.removesuffix(DUP_SUFFIX)
            if base != key and base in known and base not in present:
                log.warning("llm id %r: stripped %r suffix", e["id"], DUP_SUFFIX)
                present.add(base)
                key = base
            else:
                unknown.append(e["id"])
                continue
        resolved.append({**e, "id": known[key]})
    return resolved, unknown
