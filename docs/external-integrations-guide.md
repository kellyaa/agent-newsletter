# External Integrations Guide

How to integrate the agent-newsletter pipeline with external systems. This guide documents the **contracts** (stable interfaces and data formats) that external tools can rely on.

---

## Overview

The agent-newsletter publishes through two main channels:

1. **Static web site** — generated HTML at `https://kellyaa.github.io/agent-newsletter` (or your deploy URL)
2. **Git content branch** — markdown issue files + structured metadata in SQLite
3. **RSS feeds** — machine-readable subscription feeds

External systems can integrate by:
- Consuming the **RSS feeds** (push updates to news aggregators, social media, email)
- Reading the **content branch** (extract data from git commits, issue files, database)
- Reading the **static site** (screen-scrape or parse HTML, though RSS is preferred)
- Posting **issue notifications** via GitHub (issue comments, labels, webhooks)

---

## 1. RSS Feeds

The pipeline generates RSS feeds that you can consume or republish. **This is the recommended integration point.**

### Feed URLs

**Root feed** (a copy of the `agents` edition feed, kept for existing subscribers):
```
https://kellyaa.github.io/agent-newsletter/rss.xml
```

**Per-edition feed** (e.g., "agents" edition):
```
https://kellyaa.github.io/agent-newsletter/agents/rss.xml
```

Each listed edition has its own feed at `/<edition>/rss.xml`. (Replace the URL base with your own if you deploy to a different host.)

### Feed Format

Both feeds follow the **[RSS 2.0](https://www.rssboard.org/rss-specification)** specification, published by `@astrojs/rss` from Astro. Each feed entry includes:

| Field | Content | Example |
|-------|---------|---------|
| `title` | `<date> — <first sentence of the theme>`, or `Issue <date>` when the issue has no theme | "2026-11-17 — A mixed-methods study at a large company" |
| `link` | URL to the issue page | `https://kellyaa.github.io/agent-newsletter/agents/issues/2026-11-17/` |
| `pubDate` | Issue date at 12:00 UTC (RFC 822) | "Mon, 17 Nov 2026 12:00:00 GMT" |
| `description` | The issue theme, or `<edition name> — <date>` when there is none | "A mixed-methods study…" |
| `content` (`content:encoded`) | HTML of the featured items | (HTML) |

No `author` is emitted and no `guid` is set explicitly; `@astrojs/rss` derives the `guid` from `link`. Source of truth: `buildFeed` in `site/src/scripts/feed.js`.

### Consuming the Feed

Example: Subscribe to the feed in your RSS reader

```bash
# Using curl to fetch and check the feed is valid:
curl -s https://kellyaa.github.io/agent-newsletter/rss.xml | head -50
```

Example: Republish to Slack (using a feed-to-Slack bot like Zapier, IFTTT, or custom):

```bash
# IFTTT applet: "If RSS → then Slack message"
# URL: https://kellyaa.github.io/agent-newsletter/rss.xml
# Slack message template: "{AgentNewsletter} — {Summary}"
```

Example: Republish to email (using Substack, email service, or custom):

```bash
# mailchimp / SendGrid / Amazon SES integration: import RSS feed
# Recipients subscribe and receive email digest daily
```

---

## 2. Content Branch (Git)

All pipeline output is committed to the `content` branch in this format:

```
content/
  editions/
    agents/
      state.db                        ← SQLite database with pipeline metrics
      issues/
        2026-11-17.md                 ← Issue markdown with YAML frontmatter
        2026-11-16.md
        ...
    research/
      state.db
      issues/
        ...
```

### Issue File Format

Each issue is a markdown file: `editions/<slug>/issues/YYYY-MM-DD.md`

```yaml
---
date: "2026-11-17"                    # YYYY-MM-DD, matches the filename
theme: "One or two sentences."        # Optional; may be null
featured:
- id: "d586dccc…"                     # Item id
  section: "papers"                   # papers | news | blogs
  source: "arxiv:arxiv-se-ai-dev"     # Source identifier
  url: "https://arxiv.org/abs/2611.18939v1"   # http(s) only
  title: "Level Up! AI-Driven Challenges and Adaptations to Code Review Practices"
  author: "Kaia Newman, Christian Bird"       # Optional; may be null
  score: 8                            # Integer 0–10
  tags:
  - "code-review"
  summary: "The writer's prose summary (markdown)."
  takeaway: "Optional editorial callout."     # Optional; may be null
  open_question: null                         # Optional; may be null
appendix:
  papers:                             # Lower-ranked items, no score/summary
  - id: "…"
    section: "papers"
    source: "arxiv:arxiv-se-ai-dev"
    url: "https://arxiv.org/abs/2611.19719v1"
    title: "Found, but Not Fixed"
  news: []
  blogs: []
metadata:
  items_considered: 120
  items_featured_total: 5
  items_featured_papers: 3
  items_featured_news: 2
  items_featured_blogs: 0
  items_appendix: 9
  cost_usd: 0.42                      # Optional; may be null
  duration_seconds: 310               # Optional; may be null
---
```

The file body after the frontmatter is not part of the contract. The authoritative schema is the Zod schema in `site/src/content.config.ts`; for a real file, look at any `editions/<slug>/issues/*.md` on the `content` branch.

### Parsing Issue Files

The schema is defined in `site/src/content.config.ts` (Zod schema). To parse:

1. **Extract frontmatter**: Split on `---`, parse the first YAML block
2. **Extract markdown**: Everything after the second `---` is the rendered prose
3. **Validate structure**: `featured` is an array, `appendix` is an object with `papers`/`news`/`blogs` arrays, scores 0–10, sections in {papers, news, blogs}

Example (Python):

```python
import yaml
from pathlib import Path

def load_issue(date_str: str, slug: str):
    """Load an issue from the content branch."""
    path = Path(".worktrees/content") / "editions" / slug / "issues" / f"{date_str}.md"
    text = path.read_text()
    
    # Split frontmatter
    parts = text.split("---", 2)
    frontmatter_text = parts[1]
    markdown_text = parts[2] if len(parts) > 2 else ""
    
    # Parse YAML frontmatter
    frontmatter = yaml.safe_load(frontmatter_text)
    
    return {
        "date": frontmatter.get("date"),
        "theme": frontmatter.get("theme"),
        "featured": frontmatter.get("featured", []),
        "appendix": frontmatter.get("appendix", {}),
        "metadata": frontmatter.get("metadata", {}),
        "prose": markdown_text.strip(),
    }

issue = load_issue("2026-11-17", "agents")
print(f"Featured items: {len(issue['featured'])}")
```

### Reading the Database

The `state.db` SQLite database contains pipeline metrics and item history. Key tables:

**`runs` table** (one row per daily run):
```sql
SELECT date, items_fetched, items_candidate, items_featured, 
       cost_usd, tokens_in, tokens_out, duration_seconds
FROM runs
WHERE date >= date('now', '-30 days')
ORDER BY date DESC;
```

**`items` table** (every item ever fetched):
```sql
SELECT id, source, title, published_at, status, score, tags
FROM items
WHERE source LIKE 'arxiv:%'  -- All arXiv items
ORDER BY published_at DESC
LIMIT 100;
```

**`run_attempts` table** (diagnostics: retries, failures):
```sql
SELECT date, outcome, failed_stage, exit_code
FROM run_attempts
WHERE outcome != 'ok'  -- Only failures
ORDER BY date DESC;
```

See [database-schema-guide.md](database-schema-guide.md) for full schema.

---

## 3. Git Commits and Webhooks

The `content` branch receives daily commits from the pipeline with predictable structure.

### Commit Subjects

Each successful daily run produces a commit:

```
newsletter(agents): 2026-11-17 daily run
newsletter(research): 2026-11-17 daily run attempt 2 failed at rank
```

**Format:**
```
newsletter(<slug>): <date> daily run [attempt N failed at <stage>]
```

### Using GitHub Webhooks

You can trigger integrations on new issues being published:

1. **Set up a webhook** in Settings → Webhooks → Add webhook:
   - Payload URL: `https://your-service/handle-newsletter`
   - Events: `push` (triggered when content branch updates)
   - Content type: JSON

2. **Filter for newsletter commits** in your handler:
   ```python
   def handle_webhook(event: dict):
       ref = event.get("ref")  # e.g., "refs/heads/content"
       if ref != "refs/heads/content":
           return "not content branch"
       
       for commit in event.get("commits", []):
           msg = commit.get("message", "")
           # Matches: "newsletter(agents): 2026-11-17 daily run"
           if msg.startswith("newsletter("):
               # Handle: extract slug, date, parse the issue
               print(f"New issue! {msg}")
   ```

3. **Common integrations:**
   - **Slack:** POST to a webhook with the new issue summary
   - **Discord:** Embed the featured items as a rich message
   - **Mastodon/Twitter:** Generate a summary thread and post
   - **Email:** Send a digest to subscribers
   - **Analytics:** Ingest featured items into your BI platform

---

## 4. Static Site HTML

The site at `https://kellyaa.github.io/agent-newsletter/` (or your deploy URL) is a static Astro site. All content is prerendered to HTML.

### URLs and Routes

```
/                                    ← Edition picker
/<edition>/                          ← Latest month of issues
/<edition>/issues/<YYYY-MM-DD>/      ← One issue
/<edition>/months/<YYYY-MM>/         ← Archive by month
/<edition>/tags/<tag>/               ← Issues tagged with <tag>
/<edition>/search/                   ← Full-text search page
/rss.xml                             ← Root RSS feed
/<edition>/rss.xml                   ← Per-edition RSS feed
```

### HTML Parsing (Not Recommended)

While you *can* scrape the HTML, the RSS feeds are more reliable:
- RSS is a stable contract; HTML may change with site redesigns
- Parsing HTML is fragile (depends on CSS classes, structure)
- RSS already has all the data in structured form

If you must scrape:

```python
from bs4 import BeautifulSoup
import httpx

async def get_latest_issue(edition="agents"):
    """Scrape the latest issue from the HTML site."""
    url = f"https://kellyaa.github.io/agent-newsletter/{edition}/"
    html = httpx.get(url).text
    soup = BeautifulSoup(html, "html.parser")
    
    # Find all featured items (depends on current HTML structure)
    items = soup.find_all("article", class_="featured-item")
    
    result = []
    for item in items:
        title = item.find("h3")
        url_elem = item.find("a", class_="item-url")
        result.append({
            "title": title.text if title else None,
            "url": url_elem.get("href") if url_elem else None,
        })
    
    return result
```

**Note:** This is fragile. Use RSS instead.

---

## 5. Webhooks and Notifications

The pipeline can POST failure and staleness alerts to an external URL.

### Alert Webhook

`notify()` in `scripts/runlog.sh` (used by the watchdogs and pipeline failure alerts) POSTs to `WATCHDOG_WEBHOOK_URL` when it is set:

```bash
# In .env:
WATCHDOG_WEBHOOK_URL="https://ntfy.sh/your-topic"
```

The request is a **plain-text** POST, not JSON:

```bash
curl -fsS -m 10 -H "Title: $title" -d "$message" "$WATCHDOG_WEBHOOK_URL"
```

- Header `Title`: the alert title
- Body: the alert message, as plain text

This is the [ntfy](https://ntfy.sh) format, so an ntfy topic URL works directly. Slack, Discord and Teams incoming webhooks require a JSON body and will **not** accept this request as-is; point `WATCHDOG_WEBHOOK_URL` at a small relay that translates it.

### Example: Relay Alerts to Slack

```python
from fastapi import FastAPI, Request
import httpx

app = FastAPI()

@app.post("/newsletter-alert")
async def handle_alert(request: Request):
    title = request.headers.get("Title", "Newsletter alert")
    message = (await request.body()).decode()

    async with httpx.AsyncClient() as client:
        await client.post(
            "https://hooks.slack.com/services/YOUR/WEBHOOK",
            json={"text": f"*{title}*\n{message}"},
        )
    return {"status": "sent"}
```

Then set `WATCHDOG_WEBHOOK_URL="https://your-service.com/newsletter-alert"`.

---

## 6. Contracts and Stability Guarantees

### Current Behaviour

> This table describes how the interfaces behave today and how likely they are to change. It is **not** a formal compatibility promise from the maintainers; if you depend on one of these, say so in an issue.

| Interface | Expected stability | Notes |
|-----------|--------------------|-------|
| RSS feed URLs | Relatively stable | `/rss.xml` is kept as a copy of `/agents/rss.xml` for existing subscribers; `/<edition>/rss.xml` per edition |
| RSS feed format | Relatively stable | RSS 2.0 via `@astrojs/rss`; fields documented above |
| Issue file frontmatter | May evolve | Schema in `site/src/content.config.ts` |
| Git commit subjects | May evolve | Pattern `newsletter(<slug>): <date> daily run` |
| `state.db` schema | May evolve | Columns may be added; see `scripts/db.py` |
| Static site URLs | May evolve | Use RSS instead |
| Alert webhook | May evolve | Plain-text POST with a `Title` header |

### What's Not Stable (May Change)

- HTML structure of the static site (use RSS instead)
- Internal shell library contracts (`scripts/*`)
- Python module interfaces (not intended for external import)

---

## 7. Examples

### Example: Republish to Discord

```python
import httpx
import feedparser
from datetime import datetime

async def republish_to_discord():
    # Fetch the RSS feed
    feed_url = "https://kellyaa.github.io/agent-newsletter/agents/rss.xml"
    feed = feedparser.parse(feed_url)
    
    # Get the latest entry
    latest = feed.entries[0] if feed.entries else None
    if not latest:
        return
    
    # Build Discord embed
    embed = {
        "title": latest.title,
        "url": latest.link,
        "description": f"{latest.summary[:500]}...",
        "color": 3447003,  # Blue
        "timestamp": datetime.now().isoformat(),
    }
    
    # POST to Discord
    discord_webhook = "https://discordapp.com/api/webhooks/YOUR/WEBHOOK"
    await httpx.post(discord_webhook, json={"embeds": [embed]})

# Run daily via cron or schedule
```

### Example: Extract Scores and Analyze Trends

```python
import sqlite3
from pathlib import Path

def analyze_section_scores(slug="agents", days=30):
    db_path = Path(".worktrees/content") / "editions" / slug / "state.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Scores per section over the last N days
    cursor = conn.execute("""
        SELECT
            published_date AS date,
            section,
            AVG(score) AS avg_score,
            COUNT(*) AS count
        FROM items
        WHERE status = 'published'
          AND published_date >= date('now', ?)
        GROUP BY published_date, section
        ORDER BY published_date DESC
    """, (f"-{days} days",))
    
    for row in cursor:
        print(f"{row['date']} {row['section']}: {row['avg_score']:.1f} (n={row['count']})")
    
    conn.close()

analyze_section_scores()
```

---

## Further Reading

- [database-schema-guide.md](database-schema-guide.md) — Full schema reference for `state.db`
- [site-architecture.md](site-architecture.md) — How the static site is built and deployed
- [repository-branches-and-worktrees.md](repository-branches-and-worktrees.md) — Two-branch layout and how to clone the content branch

