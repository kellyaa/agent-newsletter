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

The pipeline generates RSS feeds that you can consume or republish. **These are the most stable integration points.**

### Feed URLs

**Root feed** (all editions):
```
https://kellyaa.github.io/agent-newsletter/rss.xml
```

**Per-edition feed** (e.g., "agents" edition):
```
https://kellyaa.github.io/agent-newsletter/agents/rss.xml
```

(Replace the URL base with your own if you deploy to a different host.)

### Feed Format

Both feeds follow the **[RSS 2.0](https://www.rssboard.org/rss-specification)** specification, published by `@astrojs/rss` from Astro. Each feed entry includes:

| Field | Content | Example |
|-------|---------|---------|
| `title` | Issue date + edition name | "2026-11-17 — AI Agents Daily" |
| `link` | URL to the issue page | `https://kellyaa.github.io/agent-newsletter/agents/issues/2026-11-17/` |
| `pubDate` | Issue publication date (RFC 822) | "Mon, 17 Nov 2026 13:00:00 GMT" |
| `description` | HTML content (all featured items with links, tags, scores) | (HTML with featured items and their metadata) |
| `guid` | Unique stable identifier | Date-based, stable across republishes |
| `author` | Generator | "AI Agents Daily" |

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
date: "2026-11-17"                    # ISO date, matches filename
theme: "Multi-agent orchestration trends"  # Optional theme line
featured:
  - id: "<item-id>"                   # Unique per-issue identifier
    section: "papers"                 # papers | news | blogs
    source: "arxiv:cs-ai"             # Source identifier
    url: "https://example.com/paper"  # URL to original item
    title: "Multi-Agent Reinforcement Learning"
    author: "Smith, J. et al"         # Optional; may be null
    score: 9                           # 0–10 ranking score
    tags: ["multi-agent", "rl"]       # Optional tags
    why: "Recent advances in cooperative multi-agent systems..." # Rationale
appendix: []                           # Lower-scored items (optional)
---

# AI Agents Daily — November 17

Featured section prose here (rendered from LLM output).

## Papers

- **[Multi-Agent Reinforcement Learning](https://example.com/paper)** (9/10) — …

## News

- **[Company releases new agentic framework](https://example.com)** (7/10) — …
```

### Parsing Issue Files

The schema is defined in `site/src/content.config.ts` (Zod schema). To parse:

1. **Extract frontmatter**: Split on `---`, parse the first YAML block
2. **Extract markdown**: Everything after the second `---` is the rendered prose
3. **Validate structure**: featured/appendix arrays, scores 0–10, sections in {papers, news, blogs}

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
        "appendix": frontmatter.get("appendix", []),
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

The pipeline can POST alerts and statistics to external services.

### Watchdog Webhook

When the watchdog detects a stale pipeline (no successful run in >36 hours), it can POST an alert:

```bash
# In .env:
WATCHDOG_WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
```

**Payload (JSON):**
```json
{
  "text": "🚨 Newsletter pipeline stale: no run since 2026-11-15T13:00Z",
  "timestamp": "2026-11-17T22:15:00Z",
  "edition": "agents"
}
```

You can use this with:
- **Slack Incoming Webhooks**
- **Discord Webhooks**
- **Microsoft Teams**
- **ntfy.sh** (POST-based notifications)
- **Your own service**

### Setting Up an Alert Handler

Example handler for Slack:

```python
from fastapi import FastAPI, Request
import httpx

app = FastAPI()

@app.post("/newsletter-alert")
async def handle_alert(request: Request):
    data = await request.json()
    
    # Build Slack message
    slack_msg = {
        "text": data["text"],
        "channel": "#alerts",
        "icon_emoji": ":warning:",
    }
    
    # POST to Slack
    await httpx.post(
        "https://hooks.slack.com/services/YOUR/WEBHOOK",
        json=slack_msg,
    )
    
    return {"status": "sent"}
```

Configure the pipeline to use it:

```bash
WATCHDOG_WEBHOOK_URL="https://your-service.com/newsletter-alert"
```

---

## 6. Contracts and Stability Guarantees

### What's Stable (Won't Break)

These are considered public contracts; breaking changes will be announced:

| Interface | Stability | Notes |
|-----------|-----------|-------|
| RSS feed URLs | High | `/rss.xml` and `/<edition>/rss.xml` are stable |
| RSS feed format | High | Follows RSS 2.0 spec; fields documented above |
| Issue file format | High | Markdown + YAML frontmatter; schema in `site/src/content.config.ts` |
| Git commit subjects | Medium | Pattern `newsletter(<slug>): <date> daily run` is stable |
| `state.db` schema | Medium | Core tables (`runs`, `items`) are stable; new columns may be added |
| Static site URLs | Medium | Route structure may change; use RSS instead |
| Webhook payload | Low | Format may change; ping the maintainer if you depend on it |

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
            strftime('%Y-%m-%d', date) as date,
            section,
            AVG(score) as avg_score,
            COUNT(*) as count
        FROM items
        WHERE status = 'published'
          AND date >= date('now', ? || ' days')
        GROUP BY date, section
        ORDER BY date DESC
    """, (f"-{days}",))
    
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

