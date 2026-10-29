"""Scaffold a new edition: ``editions/<slug>/``, optionally drafted by an LLM.

    uv run scripts/new_edition.py <slug> --name "Name" \\
        --describe "One paragraph: subject, audience, what matters, what doesn't." \\
        [--seed-source URL ...] [--no-llm]

1. Copy ``editions/_template/`` to ``editions/<slug>/`` and set slug, name,
   ``status: draft`` and an ``order`` after the existing editions.
2. Unless ``--no-llm``: call the LLM (``WRITER_MODEL``, structured JSON) with
   ``prompts/draft_edition.md`` and the full ``agents`` edition as a worked
   example. It proposes the tagline, reader profile, keywords, tags, the prompt
   fragments and starter sources.
3. **Validate everything the LLM proposed.** LLMs invent feed URLs. Each RSS
   feed is fetched and parsed, each arXiv query must return a result, each
   subreddit's ``.json`` must respond, each regex must compile, and tags are
   lower-cased and de-duplicated. Feeds that fail are commented out in
   ``sources.yaml`` with the reason, never silently dropped.
4. Write ``editions/<slug>/REVIEW.md``, the checklist a human works through
   before the edition goes live (including what was dropped and why).
5. Run ``check_edition.py <slug>`` (lint + a no-LLM dry run of fetch/prefilter).

The new edition is ``draft``: it runs daily and sends failure notifications but
stays off the site unless ``SHOW_DRAFTS=1``. Going live is a one-line PR.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import feedparser
import httpx
import yaml

import check_edition
import edition as edition_mod
import llm
from edition import EditionError

TEMPLATE_SLUG = "_template"
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_DELAY_S = 3.0  # arXiv asks for >= 3 s between requests
USER_AGENT = "agent-newsletter/0.1 (+https://github.com/; bot)"
HTTP_TIMEOUT = 20.0

_SLUG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")

# LLM fragment key -> file under editions/<slug>/prompts/
FRAGMENTS = {
    "rank_subject": "rank.subject.md",
    "rank_intro": "rank.intro.md",
    "rank_reader": "rank.reader.md",
    "rank_relevance": "rank.relevance.md",
    "rank_sections": "rank.sections.md",
    "write_subject": "write.subject.md",
    "write_intro": "write.intro.md",
    "write_voice": "write.voice.md",
    "write_sections": "write.sections.md",
    "write_theme_examples": "write.theme-examples.md",
}


class NewEditionError(Exception):
    """A usage or validation problem; reported without a traceback."""


def _str_prop(**extra: Any) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, **extra}


DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tagline", "reader_profile", "downweighted", "keywords", "tags", "prompts", "sources"],
    "properties": {
        "tagline": _str_prop(),
        "reader_profile": _str_prop(),
        "downweighted": {"type": "array", "items": _str_prop()},
        "keywords": {"type": "array", "items": _str_prop()},
        "tags": {"type": "array", "items": _str_prop()},
        "prompts": {
            "type": "object",
            "additionalProperties": False,
            "required": list(FRAGMENTS),
            "properties": {k: _str_prop() for k in FRAGMENTS},
        },
        "sources": {
            "type": "object",
            "additionalProperties": False,
            "required": ["rss", "arxiv", "hn", "reddit"],
            "properties": {
                "rss": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "name", "url", "keyword_gate_bypass", "recency_days"],
                    "properties": {
                        "id": _str_prop(), "name": _str_prop(), "url": _str_prop(),
                        "keyword_gate_bypass": {"type": "boolean"},
                        "recency_days": {"type": ["integer", "null"]},
                    },
                }},
                "arxiv": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "name", "query", "max_results"],
                    "properties": {
                        "id": _str_prop(), "name": _str_prop(), "query": _str_prop(),
                        "max_results": {"type": "integer"},
                    },
                }},
                "hn": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "name", "query", "min_points"],
                    "properties": {
                        "id": _str_prop(), "name": _str_prop(), "query": _str_prop(),
                        "min_points": {"type": "integer"},
                    },
                }},
                "reddit": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "name", "subreddit", "limit"],
                    "properties": {
                        "id": _str_prop(), "name": _str_prop(), "subreddit": _str_prop(),
                        "limit": {"type": "integer"},
                    },
                }},
            },
        },
    },
}


# ------------------------------------------------------------ validation

@dataclass
class Dropped:
    """Something the LLM proposed that did not survive validation."""
    kind: str      # "rss" | "arxiv" | "reddit" | "keyword" | "tag"
    what: str
    reason: str
    entry: dict | None = None  # the source entry, so it can be commented out


@dataclass
class Validated:
    tagline: str
    reader_profile: str
    downweighted: list[str]
    keywords: list[str]
    tags: list[str]
    prompts: dict[str, str]
    sources: dict[str, list[dict]]
    dropped: list[Dropped] = field(default_factory=list)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def clean_tags(raw: list[str]) -> tuple[list[str], list[Dropped]]:
    """Lower-case, kebab-case and de-duplicate; drop anything that is not a tag."""
    out: list[str] = []
    dropped: list[Dropped] = []
    for t in raw:
        norm = slugify(str(t))
        if not norm:
            dropped.append(Dropped("tag", str(t), "empty after normalisation"))
        elif norm in out:
            dropped.append(Dropped("tag", str(t), f"duplicate of {norm!r}"))
        else:
            out.append(norm)
    return out, dropped


def clean_keywords(raw: list[str]) -> tuple[list[str], list[Dropped]]:
    """Keep regexes that compile; drop (and record) the rest, plus exact duplicates."""
    out: list[str] = []
    dropped: list[Dropped] = []
    for kw in raw:
        if not kw.strip():
            # An empty pattern compiles, and matches every item.
            dropped.append(Dropped("keyword", kw, "empty pattern"))
            continue
        try:
            re.compile(kw)
        except re.error as e:
            dropped.append(Dropped("keyword", kw, f"does not compile: {e}"))
            continue
        if kw in out:
            dropped.append(Dropped("keyword", kw, "duplicate"))
            continue
        out.append(kw)
    return out, dropped


def _client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def check_rss(client: httpx.Client, url: str) -> str | None:
    """None if *url* serves a parseable feed with entries, else the reason it does not."""
    if urlparse(url).scheme not in ("http", "https"):
        return "not an http(s) URL"
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return f"fetch failed: {type(e).__name__}: {e}"[:160]
    parsed = feedparser.parse(resp.content)
    if not parsed.entries:
        return "no feed entries found (empty feed, or not a feed)"
    return None


def check_arxiv(client: httpx.Client, query: str) -> str | None:
    try:
        resp = client.get(
            ARXIV_API,
            params={"search_query": query, "start": 0, "max_results": 1,
                    "sortBy": "submittedDate", "sortOrder": "descending"},
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return f"query failed: {type(e).__name__}: {e}"[:160]
    if not feedparser.parse(resp.text).entries:
        return "query returned no results"
    return None


def check_subreddit(client: httpx.Client, url: str) -> str | None:
    """Probe the subreddit's Atom feed (the endpoint ``fetch_reddit`` reads).

    Reddit answers 403 to the ``.json`` endpoints without OAuth, so the feed
    URL is derived from the ``top.json`` URL the same way fetch.py does.
    """
    parts = urlparse(url)
    path = parts.path if parts.path.endswith(".rss") else parts.path.removesuffix(".json").rstrip("/") + "/.rss"
    rss_url = parts._replace(path=path, query=f"{parts.query}&limit=1" if parts.query else "limit=1").geturl()
    try:
        resp = client.get(rss_url)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return f"fetch failed: {type(e).__name__}: {e}"[:160]
    parsed = feedparser.parse(resp.content)
    if not parsed.version:
        return "response was not a feed"
    return None


def _unique_id(raw: str, fallback: str, seen: set[str]) -> str:
    base = slugify(raw) or fallback
    sid, n = base, 2
    while sid in seen:
        sid, n = f"{base}-{n}", n + 1
    seen.add(sid)
    return sid


def _host_id(url: str) -> str:
    return slugify(urlparse(url).netloc.removeprefix("www.")) or "source"


def validate_sources(
    proposed: dict[str, list[dict]],
    seed_urls: list[str],
    client: httpx.Client,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, list[dict]], list[Dropped]]:
    """Check every proposed source against the live internet.

    Returns the surviving sources, normalised to the sources.yaml schema, and a
    :class:`Dropped` record (carrying the entry, for commenting out) for each
    that failed.
    """
    dropped: list[Dropped] = []
    out: dict[str, list[dict]] = {"rss": [], "arxiv": [], "hn": [], "reddit": []}

    rss = list(proposed.get("rss") or [])
    have = {str(e.get("url", "")).rstrip("/") for e in rss}
    for url in seed_urls:  # a seed the LLM did not use is still tried as a feed
        if url.rstrip("/") not in have:
            rss.append({"id": _host_id(url), "name": _host_id(url), "url": url,
                        "keyword_gate_bypass": False, "recency_days": None})
    seen: set[str] = set()
    for e in rss:
        url = str(e.get("url", "")).strip()
        entry: dict[str, Any] = {
            "id": _unique_id(str(e.get("id") or _host_id(url)), "feed", seen),
            "name": str(e.get("name") or e.get("id") or url),
            "url": url,
        }
        if e.get("keyword_gate_bypass") is True:
            entry["keyword_gate_bypass"] = True
        rd = e.get("recency_days")
        if isinstance(rd, int) and not isinstance(rd, bool) and rd > 0:
            entry["recency_days"] = rd
        reason = check_rss(client, url)
        if reason:
            dropped.append(Dropped("rss", url, reason, entry))
        else:
            out["rss"].append(entry)

    seen = set()
    for i, e in enumerate(proposed.get("arxiv") or []):
        if i:
            sleep(ARXIV_DELAY_S)
        query = str(e.get("query", "")).strip()
        mr = e.get("max_results")
        entry = {
            "id": _unique_id(str(e.get("id") or "arxiv"), "arxiv", seen),
            "name": str(e.get("name") or e.get("id") or "arXiv"),
            "query": query,
            "max_results": mr if isinstance(mr, int) and not isinstance(mr, bool) and 0 < mr <= 100 else 30,
        }
        reason = check_arxiv(client, query) if query else "empty query"
        if reason:
            dropped.append(Dropped("arxiv", query or "(empty)", reason, entry))
        else:
            out["arxiv"].append(entry)

    seen = set()
    for e in proposed.get("hn") or []:
        query = str(e.get("query", "")).strip()
        if not query:
            dropped.append(Dropped("hn", "(empty)", "empty query"))
            continue
        mp = e.get("min_points")
        out["hn"].append({
            "id": _unique_id(str(e.get("id") or f"hn-{query}"), "hn", seen),
            "name": str(e.get("name") or f"HN: {query}"),
            "query": query,
            "hours_back": 48,
            "min_points": mp if isinstance(mp, int) and not isinstance(mp, bool) and mp > 0 else 40,
        })

    seen = set()
    for e in proposed.get("reddit") or []:
        sub = re.sub(r"^/?r/", "", str(e.get("subreddit", "")).strip())
        if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", sub):
            dropped.append(Dropped("reddit", sub or "(empty)", "not a valid subreddit name"))
            continue
        url = f"https://www.reddit.com/r/{sub}/top.json?t=day"
        lim = e.get("limit")
        entry = {
            "id": _unique_id(str(e.get("id") or f"reddit-{sub}"), "reddit", seen),
            "name": str(e.get("name") or f"r/{sub}"),
            "url": url,
            "limit": lim if isinstance(lim, int) and not isinstance(lim, bool) and lim > 0 else 10,
        }
        reason = check_subreddit(client, url)
        if reason:
            dropped.append(Dropped("reddit", f"r/{sub}", reason, entry))
        else:
            out["reddit"].append(entry)

    return out, dropped


def coerce_proposal(raw: Any) -> dict[str, Any]:
    """Shape-check the LLM's JSON before anything is written to disk."""
    if not isinstance(raw, dict):
        raise NewEditionError("the LLM did not return a JSON object")
    for key in ("tagline", "reader_profile"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise NewEditionError(f"the LLM's {key!r} is missing or empty")
    for key in ("downweighted", "keywords", "tags"):
        v = raw.get(key)
        # Blank entries are tolerated here and dropped (and reported) by the cleaners.
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise NewEditionError(f"the LLM's {key!r} must be a list of strings")
    prompts = raw.get("prompts")
    if not isinstance(prompts, dict):
        raise NewEditionError("the LLM's 'prompts' must be an object")
    for key in FRAGMENTS:
        text = prompts.get(key)
        if not isinstance(text, str) or not text.strip():
            raise NewEditionError(f"the LLM's prompt fragment {key!r} is missing or empty")
        if "{{" in text:
            raise NewEditionError(f"the LLM's prompt fragment {key!r} contains '{{{{' (would read as a slot)")
    sources = raw.get("sources")
    if not isinstance(sources, dict):
        raise NewEditionError("the LLM's 'sources' must be an object")
    for fam in ("rss", "arxiv", "hn", "reddit"):
        v = sources.get(fam, [])
        if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
            raise NewEditionError(f"the LLM's sources.{fam} must be a list of objects")
    return raw


def validate_proposal(
    raw: Any, seed_urls: list[str], client: httpx.Client,
    sleep: Callable[[float], None] = time.sleep,
) -> Validated:
    raw = coerce_proposal(raw)
    keywords, dropped = clean_keywords(raw["keywords"])
    if not keywords:
        raise NewEditionError("none of the LLM's keyword regexes compile; nothing to gate on")
    tags, tag_dropped = clean_tags(raw["tags"])
    if not tags:
        raise NewEditionError("the LLM proposed no usable tags")
    dropped += tag_dropped
    sources, src_dropped = validate_sources(raw["sources"], seed_urls, client, sleep)
    dropped += src_dropped
    return Validated(
        tagline=raw["tagline"].strip(),
        reader_profile=raw["reader_profile"].strip(),
        downweighted=[d.strip() for d in raw["downweighted"] if d.strip()],
        keywords=keywords,
        tags=tags,
        prompts={k: v.strip() + "\n" for k, v in raw["prompts"].items()},
        sources=sources,
        dropped=dropped,
    )


# ------------------------------------------------------------ rendering

def _next_order(editions_dir: Path) -> int:
    orders = []
    for d in sorted(editions_dir.iterdir()):
        if d.name.startswith("_") or not (d / "edition.yaml").is_file():
            continue
        try:
            orders.append(edition_mod.load(d.name, editions_dir).order)
        except EditionError:
            continue
    return (max(orders) // 10 + 1) * 10 if orders else 10


def _dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88, default_flow_style=False)


def render_edition_yaml(slug: str, name: str, order: int, v: Validated, sections: dict) -> str:
    header = (
        f"# {name}: drafted by scripts/new_edition.py. Read REVIEW.md before going live.\n"
        f"# Field reference: editions/_template/edition.yaml.\n"
    )
    body = _dump({
        "slug": slug, "name": name, "tagline": v.tagline, "status": "draft", "order": order,
        "reader_profile": v.reader_profile, "downweighted": v.downweighted,
        "keywords": v.keywords, "tags": v.tags,
    })
    sec_lines = ["sections:"]
    for sec, rules in sections.items():
        sec_lines.append(f"  {sec}: {{ " + ", ".join(f"{k}: {n}" for k, n in rules.items()) + " }")
    return header + body + "\n" + "\n".join(sec_lines) + "\n"


def render_sources_yaml(name: str, sources: dict[str, list[dict]], dropped: list[Dropped]) -> str:
    out = [
        f"# Starter sources for {name}, drafted by scripts/new_edition.py and checked against",
        "# the live internet. Entries commented out below failed their check and are kept",
        "# so a human can fix or delete them. See editions/_template/sources.yaml.",
        "",
    ]
    for fam in ("rss", "arxiv", "hn", "reddit"):
        entries = sources.get(fam) or []
        if entries:
            out.append(_dump({fam: entries}).rstrip())
        else:
            out.append(f"{fam}: []")
        for d in (x for x in dropped if x.kind == fam and x.entry):
            # One line: a reason with a newline would break out of the comment.
            out.append(f"  # DROPPED ({' '.join(d.reason.split())}):")
            for line in _dump([d.entry]).rstrip().splitlines():
                out.append(f"  # {line}")
        out.append("")
    out += ["html: []", "github_releases: []", ""]
    return "\n".join(out)


def render_review(slug: str, name: str, v: Validated | None, seed_urls: list[str]) -> str:
    lines = [
        f"# Review checklist: {name} (`{slug}`)",
        "",
        "Generated by `scripts/new_edition.py`. Work through this before changing `status` to `live`.",
        "",
    ]
    if v is None:
        lines += [
            "This edition was scaffolded with `--no-llm`: every `TODO` in the folder is yours to fill in.",
            "",
        ]
    else:
        lines += [
            "Everything below was drafted by an LLM. Feeds, queries, subreddits and regexes were",
            "machine-checked; **the wording and the judgement calls were not**.",
            "",
        ]
    lines += [
        "## Read and edit",
        "",
        "- [ ] `edition.yaml`: tagline, reader profile, down-weighted list. Is this the reader you mean?",
        "- [ ] `edition.yaml`: `keywords` (the prefilter gate) and `tags` (the ranker's closed vocabulary).",
        "- [ ] `edition.yaml`: `sections` thresholds and caps (defaults copied from the template).",
        "- [ ] `prompts/*.md`: all ten fragments. `rank.relevance.md` and `write.voice.md` matter most.",
        "- [ ] `sources.yaml`: every source belongs, and `keyword_gate_bypass` is set only on low-volume, hand-curated blogs.",
        "- [ ] `order` is where this edition should sit on the homepage and in the daily run.",
        "",
    ]
    if seed_urls:
        lines += ["## Seed sources you named", ""] + [f"- {u}" for u in seed_urls] + [""]
    if v is not None:
        lines += ["## Dropped during validation", ""]
        if v.dropped:
            lines += [f"- **{d.kind}** `{d.what}`: {' '.join(d.reason.split())}" for d in v.dropped]
            lines += ["", "Dropped feeds, queries and subreddits are commented out in `sources.yaml`."]
        else:
            lines += ["Nothing was dropped."]
        lines += [""]
    lines += [
        "## Tune and go live",
        "",
        "1. `uv run scripts/check_edition.py " + slug + "` shows, per source, how many items survive",
        "   recency, the keyword gate and section assignment. Adjust until the counts look sane.",
        "2. Preview the site: `SHOW_DRAFTS=1 pnpm dev` in `site/`.",
        "3. Let the draft run daily for about a week (it sends failure notifications).",
        "4. Delete this file, then open a one-line PR changing `status: draft` to `status: live`.",
        "",
    ]
    return "\n".join(lines)


def build_prompt(slug: str, name: str, describe: str, seed_urls: list[str], repo_root: Path) -> str:
    ex = repo_root / "editions" / "agents"
    frags = []
    for p in sorted((ex / "prompts").glob("*.md")):
        frags.append(f"### editions/agents/prompts/{p.name}\n\n```markdown\n{p.read_text().rstrip()}\n```")
    values = {
        "slug": slug,
        "name": name,
        "describe": describe.strip(),
        "seed_sources": "\n".join(f"- {u}" for u in seed_urls) or "(none)",
        "example_edition": (ex / "edition.yaml").read_text().rstrip(),
        "example_sources": (ex / "sources.yaml").read_text().rstrip(),
        "example_fragments": "\n\n".join(frags),
    }
    template = (repo_root / "prompts" / "draft_edition.md").read_text()
    # Single pass so a value that happens to contain "{{x}}" is not expanded again.
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


# ------------------------------------------------------------------ main

def scaffold(
    slug: str,
    name: str,
    describe: str,
    seed_urls: list[str],
    *,
    use_llm: bool,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Create ``editions/<slug>/``. Returns its path. Raises :class:`NewEditionError`."""
    if not _SLUG_RE.fullmatch(slug) or slug.startswith("_"):
        raise NewEditionError(f"invalid slug {slug!r}: use lowercase letters, digits and hyphens")
    if not name.strip():
        raise NewEditionError("--name must not be empty")
    if use_llm and not describe.strip():
        raise NewEditionError("--describe is required unless --no-llm is given")
    editions_dir = edition_mod.EDITIONS_DIR
    template = editions_dir / TEMPLATE_SLUG
    if not template.is_dir():
        raise NewEditionError(f"{template} does not exist")
    dest = editions_dir / slug
    if dest.exists():
        raise NewEditionError(f"{dest} already exists; refusing to overwrite it")
    for u in seed_urls:
        if urlparse(u).scheme not in ("http", "https"):
            raise NewEditionError(f"--seed-source {u!r} must be an http(s) URL")

    order = _next_order(editions_dir)
    tmpl_yaml = yaml.safe_load((template / "edition.yaml").read_text())
    proposal: Validated | None = None
    seed_only: Validated | None = None
    own_client = client is None
    client = client or _client()
    try:
        if use_llm:
            repo_root = edition_mod.REPO_ROOT
            prompt = build_prompt(slug, name, describe, seed_urls, repo_root)
            raw = llm.call_llm(
                prompt,
                DRAFT_SCHEMA,
                schema_name="edition_draft",
                model=os.environ.get("WRITER_MODEL", "gpt-4o-mini"),
                timeout_s=int(os.environ.get("WRITER_TIMEOUT_S", "1200")),
                label="new_edition",
                max_tokens=int(os.environ.get("WRITER_MAX_TOKENS", "16000")),
            )
            proposal = validate_proposal(raw, seed_urls, client, sleep)
        elif seed_urls:
            seeds = [{"id": _host_id(u), "name": _host_id(u), "url": u,
                      "keyword_gate_bypass": False, "recency_days": None} for u in seed_urls]
            srcs, dropped = validate_sources({"rss": seeds}, [], client, sleep)
            seed_only = Validated("", "", [], [], [], {}, srcs, dropped)
    finally:
        if own_client:
            client.close()

    shutil.copytree(template, dest)
    (dest / "prompts" / "README.md").unlink(missing_ok=True)
    try:
        if proposal is not None:
            (dest / "edition.yaml").write_text(
                render_edition_yaml(slug, name, order, proposal, tmpl_yaml["sections"])
            )
            (dest / "sources.yaml").write_text(render_sources_yaml(name, proposal.sources, proposal.dropped))
            for key, fname in FRAGMENTS.items():
                (dest / "prompts" / fname).write_text(proposal.prompts[key])
        else:
            text = (dest / "edition.yaml").read_text()
            for old, new in (
                ("slug: your-slug-here", f"slug: {slug}"),
                ("name: Your Edition Name", f"name: {yaml.safe_dump(name, width=10**6).splitlines()[0]}"),
                ("status: draft", "status: draft"),
                ("order: 100", f"order: {order}"),
            ):
                if old not in text:  # pragma: no cover - guards against template drift
                    raise NewEditionError(f"template edition.yaml lost the line {old!r}")
                text = text.replace(old, new, 1)
            (dest / "edition.yaml").write_text(text)
            if seed_only is not None:
                (dest / "sources.yaml").write_text(
                    render_sources_yaml(name, seed_only.sources, seed_only.dropped)
                )
        (dest / "REVIEW.md").write_text(render_review(slug, name, proposal, seed_urls))
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)  # never leave a half-written edition behind
        raise
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug", help="kebab-case folder name; permanent once live (it is in URLs)")
    ap.add_argument("--name", required=True, help="display name")
    ap.add_argument("--describe", default="", help="one paragraph: subject, audience, what matters, what doesn't")
    ap.add_argument("--seed-source", action="append", default=[], metavar="URL",
                    help="a source to include (repeatable)")
    ap.add_argument("--no-llm", action="store_true", help="copy the template only; fill in the TODOs by hand")
    args = ap.parse_args(argv)
    try:
        dest = scaffold(args.slug, args.name, args.describe, args.seed_source, use_llm=not args.no_llm)
    except (NewEditionError, EditionError) as e:
        print(f"new_edition: {e}", file=sys.stderr)
        return 2
    print(f"created {dest}")
    print(f"review checklist: {dest / 'REVIEW.md'}\n")
    return check_edition.check(args.slug)


if __name__ == "__main__":
    sys.exit(main())
