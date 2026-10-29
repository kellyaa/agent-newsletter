# Source Reliability Matrix

What breaks in each kind of feed source, how it shows up, and what to do. It
complements [adding-feed-sources-guide.md](adding-feed-sources-guide.md) (writing
adapters) and [debugging-guide.md](debugging-guide.md) (whole-pipeline triage).

**A note on numbers.** This repository does not record per-source uptime or latency,
so this guide gives **no uptime percentages or SLAs**; any such figure would be a
guess. What it gives is (a) the failure modes the code and project history document,
(b) how each failure is surfaced, and (c) queries to measure reliability on your own
data, section 6.

## 1. How a source failure behaves

Understanding this tells you what a failure costs.

- Each source runs inside its own try/except in `fetch.py:main()`. An exception is
  logged as `<family>/<id>: collector failed: ...` and counted in `errors=N`; the
  other sources carry on.
- `fetch` exits 0 as long as at least one source produced items. It only fails (exit
  1) if **every** collector failed and nothing was seen. One dead source never stops
  an issue.
- Consequence: **a single broken source is silent unless you look.** The run is
  green, the issue publishes, and that source's items are simply missing. Check the
  `fetch` DONE line (`seen=... new=... errors=N`) rather than relying on run status.
- Fetch is skipped when it already completed today, so a re-run does not retry a
  failed source. To retry, use `./run.sh --refetch --edition <slug>` (see
  section 5).
- The adapters make **one attempt per request** and do not retry or back off. Retry
  happens at the next scheduled day or a manual `--refetch`. (The README and the
  debugging guide mention an arXiv "~17 min backoff"; `scripts/fetch.py` contains no
  retry or backoff logic, so do not rely on that. The only protection is the fixed
  pause between requests below.)
- Each HTTP request has a 20 s timeout and follows redirects.

## 2. Per-family matrix

| Family | Typical failure | How it appears in the log | Likely cause | Action |
|---|---|---|---|---|
| `rss` | HTTP 4xx/5xx | `rss/<id>: collector failed: ... HTTP status ...` | Feed moved, site down, bot blocking | Check the URL in a browser/`curl`; update or remove. |
| `rss` | Not a feed | `collector failed: feedparser bozo, no entries: ...` | Site returns an HTML page (Webflow-style sites do this) or a truncated XML | Find the real feed URL, or switch to the `html` family. |
| `rss` | Zero items, no error | `seen=0` for that source | Feed empty; or entries lack `link` or `title` and are skipped | Open the feed and compare. |
| `arxiv` | HTTP 429 | `arxiv/<id>: collector failed: ... 429` | Rate limit: arXiv wants 3 s or more between calls per IP | Wait 10+ minutes, then re-run (section 5). Avoid back-to-back `--refetch`. |
| `arxiv` | Zero results | `seen=0` | Query too narrow, or quoting/boolean syntax wrong | Test the query in the arXiv UI. |
| `hn` | HTTP error from Algolia | `hn/<id>: collector failed` | Algolia outage or rate limiting | Re-run later. |
| `hn` | Few or no items | `seen=0` | `min_points` too high for the window, or an `OR`/quoted query (the `query` is free text, not boolean) | Lower `min_points`, widen `hours_back`, one concept per query. |
| `reddit` | HTTP 403 or 429 | `reddit/<id>: collector failed: ...` | Reddit blocks unauthenticated or hammering clients; `.json` endpoints are 403 without OAuth (the adapter already uses the Atom feed) | Wait; reduce the number of subreddits; keep the 2 s spacing. |
| `reddit` | Warning `invalid limit` | `source <id>: invalid limit ... using 10` | `limit` not a positive int | Fix the YAML. |
| `html` | Silent zero items | `html: <id> - link_selector '...' matched 0 elements` or `produced 0 items` (warnings, not errors) | Page markup changed | Re-inspect the page, update selectors. This is the most fragile family. |
| `html` | Missing dates | Items appear but are judged on fetch time | `date_selector` stopped matching | Update `date_selector`. |
| `github_releases` | Warning `gh: failed for owner/repo` | uses the `gh` CLI | `gh` missing, unauthenticated, repo renamed, or timeout (30 s) | Install/auth `gh`; fix the name. Not used by the shipped `agents` edition. |
| any | Items missing but source "worked" | Warning `rejecting item with disallowed URL scheme` | Entry URL is not `http(s)` | Usually a feed quirk; nothing to do unless it is every item. |
| any | TLS or `406` from every source | All collectors fail at once | Egress proxy rejecting ALPN (see note in `_NoALPNContext`) | Environment issue, not a source issue. |

### Relative fragility, from the project's own history

This is qualitative, taken from comments in `editions/agents/sources.yaml`,
`SPEC.md` and the code, not from measurements:

- **Most stable in practice:** plain RSS/Atom from blogs and Substack-style feeds.
  Most failures there are permanent (a feed that vanished) rather than intermittent.
- **Rate-limited by design:** arXiv (3 s spacing, 429s reported when it was
  violated, and again on rapid repeat `--refetch`) and Reddit (403 on `.json`, 429
  on bursts; hence the 2 s spacing).
- **Dependent on third-party structure:** `html` scrapers break whenever the page
  changes, and the sources file itself says to "expect to update selectors
  periodically".
- **Sources already dropped for no working endpoint** (2026-05-15 notes in
  `sources.yaml`): feeds that return 404/500, serve HTML at the feed URL, or do not
  exist. A reminder to verify a feed before adding it.

## 3. Diagnosing: down, blocked, or just slow?

1. **Find the failing source.**

   ```bash
   grep -E "collector failed|DONE: seen" logs/<slug>/run-$(date +%F).log
   ```

   The `DONE` line shows total `seen`, `new` and `errors`. Per-source lines look like
   `rss/simonw: seen=12 new=3`.
2. **Reproduce outside the pipeline** with the same User-Agent, since some sites
   treat unknown clients differently:

   ```bash
   curl -sS -o /dev/null -w '%{http_code} %{time_total}s %{content_type}\n' \
     -A 'agent-newsletter/0.1' -L --max-time 20 <url>
   ```

   - `200` plus an XML content type: the feed is fine; the problem is parsing or empty
     content.
   - `200` plus `text/html`: not a feed (parse failure, section 2).
   - `403` / `429`: blocked or rate limited.
   - `404` / `410`: gone.
   - `5xx`: their outage; try again later.
   - Time near 20 s or a timeout: slow or hung. The pipeline will report it as a
     timeout error.
3. **Slow vs down.** The pipeline cannot tell you directly. A request that
   consistently takes several seconds still succeeds, so it only costs fetch time.
   Only requests over 20 s fail. Compare `fetch` stage start and ok timestamps in the
   run log across days.
4. **Dry-run the whole edition without the LLM**: `uv run scripts/check_edition.py <slug>`
   prints per source: `fetched`, items after the recency gate, items after the keyword
   gate, and an `ERROR:` line for any source that failed. Use it after any change.

## 4. Handling a broken source

| Situation | Do this |
|---|---|
| Temporary (outage, 429) | Nothing; it recovers on the next day's run. If you need today's items, `--refetch` after a wait. |
| Broken for several days | Confirm with `curl`, then fix the URL or comment the entry out with the reason and date (the convention used in `sources.yaml`). |
| Feed gone, site still has posts | Look for another feed (`/feed`, `/rss`, `/atom.xml`, `/index.xml`, the page's `<link rel="alternate">`), else use the `html` family. |
| Replace with an alternative | Add the replacement first, run `check_edition.py`, then remove the old entry in the same change. |
| Source too noisy rather than broken | Add or tighten `keywords:`; drop `keyword_gate_bypass`; lower `limit` / `max_results`. |
| Source publishes rarely and keeps expiring | Raise `recency_days` for that source. |

Removing a source does not delete items already stored in `state.db`; they are
simply no longer refreshed. Commenting out is preferred over deleting, so the next person sees why.

## 5. Re-running after a source failure

- A plain `./run.sh` after a successful fetch does **not** re-fetch.
- `./run.sh --refetch --edition <slug>` deletes today's fetched items and fetches
  again. It requires `--edition` on purpose (to avoid re-pulling arXiv for every
  edition). It also resets downstream state, so rank and write run again and **cost
  LLM tokens**; see [performance-and-cost-guide.md](performance-and-cost-guide.md).
- After an arXiv 429, wait 10 minutes or more before refetching (SPEC records that
  fast repeated refetches trigger 429 again).

## 6. Measuring reliability yourself

Because nothing aggregates per-source health, derive it.

**From the logs** (one edition, all retained days):

```bash
grep -h "collector failed" logs/<slug>/run-*.log \
  | sed -E 's/.*(rss|arxiv|hn|reddit|html|gh)\/([^:]+): collector failed.*/\1\/\2/' \
  | sort | uniq -c | sort -rn
```

That counts failures per source across days. Compare with the number of days you
have logs for to get a failure rate.

**From the database**, which sources are actually producing items:

```bash
sqlite3 editions/<slug>/state.db \
  "SELECT source, COUNT(*) AS items, MAX(last_seen_date) AS last_seen
   FROM items GROUP BY source ORDER BY last_seen ASC"
```

A source whose `last_seen` is days behind the others, while the run was green, is a
silent failure. (Slow publishers legitimately have old dates; compare with how often
they post.)

## 7. Reporting an issue

If a source in a shipped edition is persistently broken, open an issue on the
repository with: the source `id` and family, the first date it failed, the exact log
line, and the `curl` result from section 3. A pull request that comments the entry
out, with the reason and date in the YAML comment, is the quickest fix.
