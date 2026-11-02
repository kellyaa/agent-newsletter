# Extending the Pipeline with New Sources

[adding-feed-sources-guide.md](adding-feed-sources-guide.md) explains the adapter contract and lists the six families. This guide is the worked, end-to-end version for contributors who need something the YAML cannot express. It has two examples:

- **Medium effort**: a new Hacker News query variant (YAML, plus one optional code change).
- **Hard effort**: a new source family backed by an authenticated API, touching `fetch.py`, `check_edition.py`, `prefilter.py`, `.env.template` and tests.

Read the "Contract" section of the feed-sources guide first. Failure behaviour is in [source-reliability-matrix.md](source-reliability-matrix.md), and the gates a fetched item meets next are in [prefilter-gates-guide.md](prefilter-gates-guide.md).

## 1. Architecture in one page

```
editions/<slug>/sources.yaml ──► fetch.load_sources()
                                   │
                    iter_collectors(sources, http_client)
                      one lazy generator per source, overrides stamped on
                                   │
                    main(): try/except per source ──► upsert_items() ──► state.db
```

- **Six families**: `rss`, `arxiv`, `hn`, `reddit`, `html` (CSS-selector scraper) and `github_releases` (shells out to `gh api`; items are prefixed `gh:`). Their keys, defaults and default sections are in the table in the feed-sources guide.
- **One edition, one `sources.yaml`.** Sources are config; no edition names appear in code.
- **The family is the prefix before the colon in `Item.source`** (`rss:simonw`, `hn:hn-agent`). The prefilter uses it for the recency window (`RECENCY_DAYS`), the default section (`SECTION_BY_FAMILY`) and the cross-source dedup winner (`SOURCE_PRIORITY`).
- **Adapters only fetch and normalise.** They make no LLM calls and do not filter by relevance; the prefilter and ranker do that.
- **Fetched text is untrusted.** It reaches the ranker and writer as data, never as instructions (see `SECURITY-AI.md`). Do not add tools or shell access to LLM calls based on item content.

## 2. The adapter pattern

```python
def fetch_xxx(source: dict, *, client: httpx.Client | None = None) -> Iterable[Item]
```

- `source` is the YAML entry. Index required keys directly (`source["query"]`) so a typo fails loudly; use `.get(key, default)` for optional ones.
- `client` is the shared `httpx.Client` (20 s timeout, project User-Agent, redirects followed). If it is `None`, create one and close it yourself, as `fetch_hn` does:

  ```python
  _client = client or _make_http_client()
  try:
      resp = _client.get(url, params=...)
      resp.raise_for_status()
  finally:
      if client is None:
          _client.close()
  ```

- It is a generator yielding `Item`. Cap `raw_text` at 4000 characters, strip titles, skip entries that have no link or title, and let HTTP and parse errors raise. `main()` catches the exception per source, so one dead source never stops the run, but an adapter that swallows its own errors makes a dead source look healthy.
- Do not handle `section`, `keyword_gate_bypass` or `recency_days` in the adapter. `iter_collectors()` validates and stamps those from the YAML.
- Do not filter URLs. `upsert_items()` rejects non-`http(s)` URLs for every adapter.

## 3. Medium effort: a new Hacker News query variant

Goal: follow a second HN topic, "model context protocol", without Show HN and Launch HN posts.

### YAML only

`fetch_hn` already supports this. Append to `editions/<slug>/sources.yaml`:

```yaml
hn:
  - id: hn-mcp
    name: "HN: model context protocol"
    query: "model context protocol"   # free text, NOT boolean: no OR, no quotes
    hours_back: 48                    # default 48
    min_points: 40                    # default 50
    exclude_launches: true            # drops Show HN / Launch HN at the API
```

Rules that bite:

- `id` must be unique within the family. It becomes `hn:hn-mcp`, and `check_edition.py` rejects duplicates.
- `query` is passed to the Algolia search as plain text. One concept per query; overlap between queries is fine because dedup is by canonical URL.
- `hours_back` and `min_points` must be positive integers. Prefer a lower `min_points` with a narrow query over a high one with a broad query.
- HN's default recency window is 3 days (`RECENCY_DAYS["hn"]`); `recency_days:` on the entry overrides it per source.

Check it without calling the LLM:

```bash
uv run scripts/check_edition.py <slug>
```

Read the `hn/hn-mcp` row: fetched, then items surviving recency, then the keyword gate. If most are lost at the keyword gate, either the topic is off for this edition or `keywords` in `edition.yaml` needs a pattern for it. Do not set `keyword_gate_bypass` on HN: it is high volume and noisy, which is exactly what the gate is for.

### A code change, only if the YAML cannot say it

Suppose you also want a minimum comment count. `fetch_hn` builds its Algolia filter in one place:

```python
"numericFilters": f"created_at_i>{cutoff},points>={min_points}",
```

Add an optional key the same way the existing ones work: read it with a default, add it to the filter, document it in the feed-sources guide table and `editions/agents/sources.yaml`'s header, and add a case to `tests/test_fetch_adapters.py` beside the HN tests asserting the request parameters:

```python
min_comments = source.get("min_comments", 0)
...
"numericFilters": f"created_at_i>{cutoff},points>={min_points},num_comments>={min_comments}",
```

`tests/test_sources_config.py::test_numeric_fields_are_positive_ints` lists the numeric keys it checks; add the new key there so a bad value in a committed `sources.yaml` fails CI.

## 4. Hard effort: a new source family with an API key

**Illustrative only.** This walks through adding a family called `x` for posts from a list of accounts, to show every place a new family touches. It is not shipped, and X/Twitter's API access, pricing and response shape change; check the current API documentation and terms before building this for real. The Item mapping and the plumbing are the part that carries over to any authenticated API.

### Step 1: decide the YAML shape

```yaml
x:
  - id: x-anthropic
    name: "X: @AnthropicAI"
    username: AnthropicAI     # required
    max_results: 20           # optional, default 20
    section: news             # optional override, handled for you
```

Keep credentials out of the YAML. They come from the environment.

### Step 2: the adapter (`scripts/fetch.py`)

Place it with the other adapters, above "Persistence". `os` must be imported at the top of the module (it is not currently).

```python
X_API_URL = "https://api.x.com/2"


def fetch_x(source: dict, *, client: httpx.Client | None = None) -> Iterable[Item]:
    username = source["username"]
    max_results = source.get("max_results", 20)
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        # Raise: a missing credential is a failed source, not "no news".
        raise RuntimeError("x: X_BEARER_TOKEN is not set")
    log.info("x: fetching %s (@%s)", source["id"], username)
    headers = {"Authorization": f"Bearer {token}"}   # never log this
    _client = client or _make_http_client()
    try:
        user = _client.get(f"{X_API_URL}/users/by/username/{username}", headers=headers)
        user.raise_for_status()
        uid = user.json()["data"]["id"]
        resp = _client.get(
            f"{X_API_URL}/users/{uid}/tweets",
            headers=headers,
            params={"max_results": max_results, "tweet.fields": "created_at,text"},
        )
        resp.raise_for_status()
    finally:
        if client is None:
            _client.close()
    for post in resp.json().get("data", []):
        text = (post.get("text") or "").strip()
        if not text:
            continue
        yield Item(
            source=f"x:{source['id']}",                      # family prefix
            url=f"https://x.com/{username}/status/{post['id']}",
            title=text.splitlines()[0][:200],
            author=username,
            published_at=_to_iso(post.get("created_at")),
            raw_text=text[:4000],
        )
```

Points that are easy to get wrong:

- The `{username}` goes into a URL path. Validate it against a pattern (like `_GH_NAME_RE` for GitHub owners) before use, because it comes from a file contributors edit.
- Do not put the token in log messages, exception text or the `Item`. `raise_for_status()` errors include the URL but not request headers; keep it that way.
- Two requests per source cost API quota every day. Fetch the user id once per run, not per post.

### Step 3: register it in the collector loop (`iter_collectors`)

```python
    for i, src in enumerate(sources.get("x") or []):
        if i > 0:
            time.sleep(X_REQUEST_DELAY)          # define next to REDDIT_REQUEST_DELAY if the API rate-limits
        yield f"x/{src['id']}", with_override(src, fetch_x(src, client=http_client))
```

The label (`x/<id>`) appears in logs and in the `check_edition.py` table.

### Step 4: teach lint about the family (`scripts/check_edition.py`)

```python
FAMILIES = ("rss", "arxiv", "hn", "reddit", "html", "github_releases", "x")
REQUIRED_KEYS = {
    ...,
    "x": {"id", "username"},
}
```

Without this, lint reports `unknown source families ['x']` and the edition cannot go live. `tests/test_sources_config.py` keeps its own copies of `FAMILIES` and `REQUIRED_KEYS`; update them as well so a committed `sources.yaml` using the family is validated.

### Step 5: prefilter tables (`scripts/prefilter.py`)

```python
RECENCY_DAYS = {..., "x": 3, ...}
SOURCE_PRIORITY = {..., "x": 1, ...}     # lowest: the same story elsewhere wins the collapse
SECTION_BY_FAMILY = {..., "x": "news", ...}
```

A missing entry does not error: it silently becomes a 3-day window, the `blogs` section and priority 0.

### Step 6: configuration and documentation

- The new variable goes into `.env.template` with a comment (commented-out like `# X_BEARER_TOKEN=`). `tests/test_env_template.py` fails if code reads an `os.environ.get("X_BEARER_TOKEN")` that the template does not document, and the reverse. Never commit a real token or `.env`.
- Update the family table in [adding-feed-sources-guide.md](adding-feed-sources-guide.md), [edition-yaml-reference.md](edition-yaml-reference.md), the comment header in `editions/_template/sources.yaml`, and `SPEC.md` if it lists families.
- If you add a file under `scripts/`, `README.md` must mention it (`tests/test_docs_paths.py`). Adding to `fetch.py` does not need that.
- Keep the stage import rule: `fetch.py` must not import from `prefilter.py`, `rank.py` and the other stages. Put anything shared in `db.py`, `edition.py`, `llm.py`, `models.py` or `candidates.py` (`tests/test_stage_import_boundary.py`).

### Step 7: tests

Mock the network, set the environment with `monkeypatch`, and follow the patterns in `tests/test_fetch_adapters.py` and `tests/test_fetch_html.py` (a fake client returning canned responses). Cover at least:

```python
def test_x_yields_items(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "t")
    client = FakeClient([user_response, posts_response])    # sketch: a fake like _FakeClient in tests/test_fetch_html.py, returning canned JSON in order
    items = list(fetch_x({"id": "a", "username": "AnthropicAI"}, client=client))
    assert [i.source for i in items] == ["x:a"]
    assert items[0].url == "https://x.com/AnthropicAI/status/1"

def test_x_requires_token(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="X_BEARER_TOKEN"):
        list(fetch_x({"id": "a", "username": "u"}))
```

Also: an empty or text-less post is skipped, an HTTP error status raises, `raw_text` is capped at 4000, an invalid `username` is rejected, and `iter_collectors` yields the `x/<id>` label (see `test_hn_sources_called_in_main` in `tests/test_fetch_adapters.py` for the pattern of monkeypatching the adapter and calling `main()`). Remember the coverage gate: every new branch needs a test.

## 5. Checklist

1. Adapter yields `Item`s with the right family prefix; raises on HTTP and parse errors; skips malformed entries; caps `raw_text`.
2. Registered in `iter_collectors()` (with `with_override`) and rate-limited if the API requires it.
3. `FAMILIES` and `REQUIRED_KEYS` updated in `check_edition.py` and in `tests/test_sources_config.py`.
4. `RECENCY_DAYS`, `SOURCE_PRIORITY`, `SECTION_BY_FAMILY` updated in `prefilter.py`.
5. Any new env var in `.env.template`, never in YAML or logs.
6. Docs updated; no stage-to-stage imports.
7. `uv run --extra test pytest` passes the 95% gate; `uv run scripts/check_edition.py <slug>` shows the new source row with sensible counts.

## 6. Debugging a source

| Symptom | Check |
|---|---|
| A source shows `fetched=0` in `check_edition.py` | Run the request by hand (`curl -s` with the same parameters); check for a 4xx, an empty result window, or a bad query |
| `collector failed:` in the log | The traceback is logged with it; the run continues without that source |
| Items fetched but none survive | Which gate drops them: recency (`RECENCY_DAYS` / `recency_days`) or the keyword gate. `check_edition.py` prints both counts |
| Items land in the wrong section | `SECTION_BY_FAMILY` or the source's `section:` override |
| Duplicates across sources | Dedup is by canonical URL; different URLs for one story are collapsed later by title similarity, by `SOURCE_PRIORITY` |
| Works locally, not in the daily run | The env var is in your shell but not in `.env` or `~/.config/agent-newsletter/env`, which `run.sh` sources |

Re-fetching today after a fix: `./run.sh --refetch --edition <slug>` (fetch is skipped once it has completed for the day).
