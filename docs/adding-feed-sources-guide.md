# Adding Feed Sources Guide

Two different jobs share the name "adding a source":

- **A. Add a source to an edition** (most common): a new blog, subreddit, arXiv
  query or HN query using a family that already exists. This is a YAML edit.
- **B. Add a new kind of adapter** (rare): a family the pipeline cannot read yet.
  This is a Python change in `scripts/fetch.py` plus a few lookup tables.

All adapters live in `scripts/fetch.py`. Overrides and families are summarised in
[edition-yaml-reference.md](edition-yaml-reference.md) and, for the gates a
fetched item then meets, [prefilter-gates-guide.md](prefilter-gates-guide.md).

## 1. Architecture and contract

`fetch.py` makes no LLM calls. For each source in `editions/<slug>/sources.yaml`
it runs one adapter, then passes the items through `upsert_items()` into
`state.db`.

An adapter is a plain function:

```python
def fetch_xxx(source: dict, *, client: httpx.Client | None = None) -> Iterable[Item]
```

- `source` is the YAML entry as a dict (`id`, `url`, family-specific keys).
- `client` is the shared `httpx.Client` (connection reuse, 20 s timeout, the
  project User-Agent, redirects followed). When it is `None` the adapter makes
  and closes its own, so it still works standalone and in tests.
- It is a **generator** that yields `Item` objects.

`Item` fields (a dataclass in `fetch.py`):

| Field | Type | Notes |
|---|---|---|
| `source` | `str` | `"<family>:<id>"`, e.g. `rss:simonw`. The part before the colon is the **family**; the prefilter derives recency window, default section and dedup priority from it. |
| `url` | `str` | The link readers will click. |
| `title` | `str` | Stripped. Collapse newlines (arXiv does). |
| `author` | `str \| None` | |
| `published_at` | `str \| None` | ISO 8601. Use `_to_iso()` for feedparser time structs. `None` is allowed: the recency gate falls back to `fetched_at`. |
| `raw_text` | `str \| None` | Summary or excerpt, capped at 4000 chars by every existing adapter. This is what the ranker reads. |
| `section_override`, `keyword_gate_bypass`, `recency_days_override` | | Leave at defaults. They are stamped on for you from the YAML (see below). |

### What you get for free

- **Overrides.** `iter_collectors()` wraps every adapter so the optional
  `section:`, `keyword_gate_bypass:` and `recency_days:` keys in the YAML are
  validated (bad values are logged and ignored) and stamped onto each item. The
  adapter does not handle them.
- **Error isolation.** `main()` runs each source inside a try/except. An
  exception in one source is logged and counted; the run only aborts if every
  collector failed and nothing was seen. So **let network and parse errors
  raise**; do not swallow them. A source that silently yields nothing looks
  healthy.
- **URL safety.** `upsert_items()` rejects any URL that is not `http(s)`
  (`db.is_allowed_url`), so `javascript:` and `data:` links never reach
  `state.db`. This is the single choke point for all adapters; do not rely on
  your adapter to filter them.
- **Dedup.** The item id is `sha256(canonicalize_url(url))`. A URL seen again
  updates `last_seen_date` and `appearances` rather than inserting a duplicate.
  Later, title-similarity dedup collapses the same story from different sources,
  preferring higher `SOURCE_PRIORITY` families (arxiv > rss/html > gh > hn >
  reddit).
- **Skip-if-done.** Once a fetch completes for the run date, a re-run is a no-op
  unless forced with `run.sh --refetch`.

## 2. Reference: existing adapters

| Family (`sources.yaml` key) | Function | Required keys | Optional keys | Default section |
|---|---|---|---|---|
| `rss` | `fetch_rss` | `id`, `url` | `name`, overrides | blogs |
| `arxiv` | `fetch_arxiv` | `id`, `query` | `max_results` (30), overrides | papers |
| `hn` | `fetch_hn` | `id`, `query` | `hours_back` (48), `min_points` (50), `exclude_launches`, overrides | news |
| `reddit` | `fetch_reddit` | `id`, `url` | `limit` (10), overrides | news |
| `html` | `fetch_html` | `id`, `url`, `link_selector` | `title_selector`, `date_selector`, `excerpt_selector`, `max_items`, overrides | news |
| `github_releases` | `fetch_github_releases` | `owner`, `repo` | overrides | news |

Notes:

- `github_releases` shells out to `gh api` instead of using the HTTP client, and
  its items use the source prefix `gh:` (not `github_releases:`). The shipped
  `agents` edition currently has it set to `[]`.
- `check_edition.py` enforces the required keys above (`REQUIRED_KEYS`), though
  it only checks `id` and `url` for `html`; `link_selector` is validated when
  the adapter runs.

### Pattern 1: RSS/Atom (`fetch_rss`)

The simplest and the one to copy. Shape:

```python
resp = _client.get(url); resp.raise_for_status()
parsed = feedparser.parse(resp.content)
if parsed.bozo and not parsed.entries:
    raise RuntimeError(...)          # malformed AND empty = failure, not "no news"
for entry in parsed.entries:
    link, title = entry.get("link"), entry.get("title")
    if not link or not title:
        continue                     # skip malformed entries, keep the rest
    ...
    yield Item(source=f"rss:{source['id']}", ...)
```

Two details worth copying: it parses `resp.content` (bytes) so feedparser can
detect the encoding, and a `bozo` flag with entries still present is tolerated.

### Pattern 2: API query (`fetch_arxiv`, `fetch_hn`)

Same flow, with request parameters instead of a feed URL. arXiv returns an Atom
feed (parsed with feedparser); HN's Algolia endpoint returns JSON (`resp.json()`,
then `hits`). When a hit has no outbound `url`, HN falls back to the
`news.ycombinator.com/item?id=` permalink, a good model for "what URL do I use if
the source has none".

### Pattern 3: feed with quirks (`fetch_reddit`)

Reddit returns 403 for the `.json` endpoints without OAuth, so the adapter
rewrites the configured URL to the Atom feed (`_reddit_rss_url`), takes the first
`limit` entries, extracts the real outbound link from the `[link]` anchor in the
summary and strips HTML from the body. Take from it: normalise the configured
URL in code rather than making editors write the odd one, and validate numeric
config (`_validate_reddit_limit`) with a logged fallback.

### Pattern 4: HTML scraping (`fetch_html`)

For pages with no feed. Selectors are CSS (selectolax). `link_selector` may match
the `<a>` itself or a wrapper containing one; the other selectors are evaluated
relative to the link element. If `link_selector` matches nothing the adapter
logs a warning and yields nothing. Prefer RSS where it exists; scrapers break when
the page markup changes.

## 3. Job A: add a source to an edition

1. Pick the family and append an entry under its key in
   `editions/<slug>/sources.yaml`:

   ```yaml
   rss:
     - id: my-blog                      # unique within the family; becomes rss:my-blog
       name: "My Blog"
       url: https://example.com/feed.xml
       keyword_gate_bypass: true         # only for low-volume, hand-picked blogs
       recency_days: 90                  # only for slow publishers
   ```

2. Check the feed yourself first: `curl -sI <url>` should return 200 with an XML
   content type. Substack, Ghost and WordPress usually expose `/feed`; sites that
   serve HTML at the feed URL need the `html` family.
3. Lint and dry-run. This fetches every source, applies the recency and keyword
   gates (no LLM) and prints per-source counts:

   ```bash
   uv run scripts/check_edition.py <slug>
   ```

   A source with `fetched=0` or an error is the thing to look at.
4. Choose overrides deliberately (see the comments at the top of
   `editions/agents/sources.yaml`): `section:` (papers | news | blogs) when the
   family default is wrong; `keyword_gate_bypass: true` sparingly;
   `recency_days:` for slow publishers.

## 4. Job B: add a new adapter family

Only needed when none of the six families can read the source. Touch these places:

1. **`scripts/fetch.py`**: add `fetch_<family>(source, *, client=None)` following
   the template below, and call it from `iter_collectors()` with the
   `with_override(...)` wrapper, using the label `"<family>/<id>"`. If the source
   is rate-limited, sleep between sources the way arXiv (3 s) and Reddit
   (`REDDIT_REQUEST_DELAY`) do.
2. **`scripts/check_edition.py`**: add the family to `FAMILIES` and its required
   keys to `REQUIRED_KEYS`, or lint will reject the YAML as an unknown family.
3. **`scripts/prefilter.py`**: add the family to `RECENCY_DAYS`,
   `SECTION_BY_FAMILY` and `SOURCE_PRIORITY`. Missing entries fall back to a 3-day
   window, the `blogs` section and priority 0, which is rarely what you want.
4. **Docs**: the family table above, `docs/edition-yaml-reference.md`, and
   `SPEC.md` if it lists families.
5. **Tests**: see section 5.

### Adapter template

```python
def fetch_example(source: dict, *, client: httpx.Client | None = None) -> Iterable[Item]:
    url = source["url"]                       # required keys: index directly, fail loudly
    max_items = source.get("max_items", 30)   # optional keys: .get() with a default
    log.info("example: fetching %s (%s)", source["id"], url)
    _client = client or _make_http_client()
    try:
        resp = _client.get(url)
        resp.raise_for_status()               # 4xx/5xx -> exception -> isolated by main()
    finally:
        if client is None:                    # only close a client we created
            _client.close()
    data = resp.json()
    for rec in data.get("items", [])[:max_items]:
        link, title = rec.get("url"), rec.get("title")
        if not link or not title:
            continue
        yield Item(
            source=f"example:{source['id']}", # family before the colon
            url=link,
            title=title.strip(),
            author=rec.get("author"),
            published_at=_to_iso(rec.get("published")),
            raw_text=(rec.get("summary") or "")[:4000] or None,
        )
```

## 5. Testing

No test touches the real network. Existing patterns:

- `tests/test_fetch_adapters.py`: rss, arxiv, hn, reddit and github adapters, with
  helpers that build minimal RSS/Atom XML and a mocked `httpx` response.
- `tests/test_fetch_html.py`: a tiny `_FakeClient` whose `get()` returns a canned
  response and records the requested URLs.

For a new adapter, cover at least:

1. A happy-path response yields correctly populated `Item`s (check `source`
   prefix, `url`, `title`, `published_at`, `raw_text` length cap).
2. An entry missing a link or title is skipped, not fatal.
3. An HTTP error status raises (so `main()` can isolate it).
4. Empty or malformed responses behave as intended: raise for malformed,
   yield nothing for genuinely empty.
5. Every optional config key, including an invalid value.

Run the suite with `uv run pytest tests/test_fetch_adapters.py` (or the whole
`tests/` directory). Then do one live pass with `check_edition.py <slug>` to see
real counts through the gates.

## 6. Common pitfalls

- **Rate limits.** arXiv asks for 3 s between requests and Reddit throttles
  aggressively (429). Both sleep between sources in `iter_collectors()`. A new
  rate-limited family needs the same.
- **Relative links.** Scraped pages often have relative hrefs; resolve with
  `urljoin(page_url, href)` as `fetch_html` does.
- **Non-http(s) URLs.** Dropped centrally by `upsert_items()` with a warning; you
  will see the warning rather than the item.
- **URL canonicalisation and dedup.** `canonicalize_url` strips tracking
  parameters so the same article under two URLs is one row. Do not pre-mangle
  URLs in the adapter.
- **Pagination.** No adapter paginates. Each makes one request per run and the
  daily cadence plus the recency window cover the gap; use `max_results`,
  `limit` or `max_items` rather than paging.
- **Swallowed errors.** Catching an exception and returning nothing hides a dead
  source. Raise, and let the fetch log show it.
- **Missing `published_at`.** Allowed, but such items are judged on `fetched_at`,
  so a backlog crawl can look brand new. Parse the date when the source has one.
- **Huge `raw_text`.** Cap at 4000 characters like the others; it feeds an LLM
  prompt.
- **Wrong family prefix.** The `source` string prefix drives recency, section and
  dedup priority. A typo silently gives you the defaults.
