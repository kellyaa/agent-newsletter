---
name: add-edition-source
description: Add a feed (RSS, arXiv, HN, Reddit) to an existing newsletter edition and verify it with check_edition.py. Use when asked to "add <site> to the <edition> edition".
---

# Add a source to an edition

Editions are config only: `editions/<slug>/sources.yaml`. Never add edition-specific Python.

1. **Find the real feed.** A page URL such as `https://www.infoq.com/ai-coding` is usually HTML. Look for the feed with `curl -sL -A "Mozilla/5.0" -o /dev/null -w "%{http_code} %{content_type} %{url_effective}\n" <url>` and check that the content type is XML. Try `/feed`, `/rss`, `/atom.xml` and the `<link rel="alternate">` tag in the page.
2. **Pick the family** (`rss`, `arxiv`, `hn`, `reddit`, `html`, `github_releases`) and copy the shape of a neighbouring entry. `editions/agents/sources.yaml` is the fully worked example.
3. **Decide on the keyword gate.** Set `keyword_gate_bypass: true` only for low-volume, hand-picked or topic-specific feeds. Broad or high-volume feeds keep the gate. Add `recency_days` for sources that publish rarely.
4. **Dry-run it:** `uv run scripts/check_edition.py <slug>`. The per-source row shows fetched / recency / keyword counts. A source that fetches 0 items or fails the gate for everything should not be added. Reddit may return 429 from a sandbox, which is not caused by your change.
5. **Commit** only `sources.yaml`, with `git commit -s`, and open the PR with `Closes #<issue>`.

Rules from `AGENTS.md` still apply: do not edit `.github/workflows/**`, do not commit secrets, and never remove the `hold` label.
