# Site Development Guide

How to work on the Astro site in `site/`: run it, change it, test it, and understand what it builds from. [site-architecture.md](site-architecture.md) describes the structure; this guide is the workflow. Everything below was checked against `site/` on `main`.

## 1. Quick start

Requirements: Node.js 22 (CI builds on 22 and 24; CONTRIBUTING.md states 18.17.1 as the minimum) and pnpm 10 (the version CI uses).

```bash
pnpm --prefix site install      # once, and after package.json / lockfile changes
pnpm --prefix site dev          # dev server, http://localhost:4321
pnpm --prefix site build        # astro build, then pagefind indexes dist/
pnpm --prefix site preview      # serve dist/ (needed to try search)
pnpm --prefix site test         # node:test suite
```

With no issues present, the site still builds: pages show "No issues yet". To see real content, put issue files where the build expects them (section 5).

Environment variables the site reads:

| Variable | Effect |
|---|---|
| `SHOW_DRAFTS=1` | include `status: draft` editions (hidden otherwise) |
| `SITE_URL`, `SITE_BASE` | set `site` and `base` in `astro.config.mjs`; deploy sets them so links work under `/<repo>/` |
| `EDITIONS_DIR` | where to read `edition.yaml` files (default `../editions` from `site/`); tests use it |

## 2. Where things are

```
site/
├── astro.config.mjs          site/base from env, directory output, Shiki themes
├── package.json              scripts and the few dependencies (astro, marked, isomorphic-dompurify, yaml, @astrojs/rss, pagefind)
├── src/
│   ├── content.config.ts     the `issues` collection: Zod schema, edition-aware loader
│   ├── layouts/BaseLayout.astro   page shell: CSP, header, footer, RSS link
│   ├── pages/
│   │   ├── index.astro                    edition picker  (/)
│   │   ├── rss.xml.js                     root feed
│   │   └── [edition]/
│   │       ├── index.astro                latest month    (/<slug>/)
│   │       ├── search.astro               search          (/<slug>/search/)
│   │       ├── rss.xml.js                 per-edition feed
│   │       ├── issues/[date].astro        one issue
│   │       ├── months/[yyyymm].astro      month archive
│   │       └── tags/index.astro, [tag].astro
│   ├── components/           AppendixSection, ArchiveSidebar, Callout, FeaturedItem, MonthView, ScoreBar, Search, TagChip
│   ├── scripts/              editions.ts, months.ts, tag-slug.ts, safe-href.ts, feed.js (plain TS/JS, unit-testable)
│   └── styles/global.css     design tokens and base styles
└── tests/                    *.test.ts plus seed-editions.ts
```

There is no `src/content/` directory in git. Issue files are generated and gitignored (`site/src/content/issues/*/`).

## 3. Key concepts

**Static output.** Every page is prerendered. Dynamic routes (`[edition]`, `[date]`, `[tag]`, `[yyyymm]`) export `getStaticPaths()` to list the pages to build. There is no server at runtime.

**The edition registry.** `scripts/editions.ts` reads `../editions/*/edition.yaml` (slug, name, tagline, status, order only; the rest of the file is validated by the Python side) and decides which editions exist and which are listed. `_template` and dot-folders are ignored. Pages get editions from `loadRegistry().listed`, so a draft edition has no pages unless `SHOW_DRAFTS=1`.

**The content collection.** `content.config.ts` defines `issues`, globbing `src/content/issues/<slug>/*.md` for each listed edition. The first path segment of an entry's `id` is its edition (`editionOf()`), and `issuesFor(issues, slug)` filters by it. The build fails (on purpose) if `src/content/issues` has a folder no edition owns, or a stray flat `YYYY-MM-DD.md`.

**Components** are `.astro` files with a `Props` interface in the frontmatter fence and scoped `<style>`. They render to HTML at build time. There is no client framework; the only client script is the Pagefind loader.

**Base path.** The site can be deployed under a sub-path. Build links from `import.meta.env.BASE_URL` (trim the trailing slash, as `TagChip.astro` and `ArchiveSidebar.astro` do), never a hard-coded `/`.

## 4. The content model

An issue is `site/src/content/issues/<slug>/YYYY-MM-DD.md`, written by the pipeline (`scripts/write.py`). The schema (`content.config.ts`) requires:

```yaml
---
date: "2026-10-28"            # YYYY-MM-DD, also the URL slug
theme: "One or two sentences"  # optional; render only if non-empty
featured:                      # may be empty
  - id: "<sha256 of canonical URL>"
    section: papers            # papers | news | blogs
    source: "arxiv:cs-ai"
    url: "https://example.com/post"   # http(s) only; other schemes fail the build
    title: "..."
    author: "..."              # optional, nullable
    score: 8                   # integer 0-10
    tags: [tool-use, evals]    # default []
    summary: "markdown, inline"
    takeaway: "..."            # optional
    open_question: "..."       # optional
appendix:
  papers: [ { id, section, source, url, title } ]
  news: []
  blogs: []
metadata:
  items_considered: 120
  items_featured_total: 14
  items_featured_papers: 5
  items_featured_news: 5
  items_featured_blogs: 4
  items_appendix: 30
  cost_usd: null               # optional
  duration_seconds: 420        # optional
---
```

The `section` enums, tag URL rules (`tag-slug.ts`) and the http(s) check mirror rules in the Python side (`scripts/models.py`, `scripts/db.py`). If you change a field here, change the writer in `scripts/write.py` in the same PR, or the next daily run breaks the build.

To look at a page with real content locally, copy a few issue files from the content branch:

```bash
git worktree add .worktrees/content content        # once
mkdir -p site/src/content/issues/agents
cp .worktrees/content/editions/agents/issues/*.md site/src/content/issues/agents/
pnpm --prefix site dev
```

These copies are gitignored. The deploy workflow does the same copy for every edition.

## 5. Everyday tasks

### Add a page

1. Create `src/pages/[edition]/<name>.astro` (per-edition) or `src/pages/<name>.astro` (site-wide).
2. For a per-edition page, copy the `getStaticPaths()` from `search.astro`:

   ```astro
   export function getStaticPaths() {
     return loadRegistry().listed.map((edition) => ({
       params: { edition: edition.slug },
       props: { edition },
     }));
   }
   ```

3. Wrap the content in `<BaseLayout title=... edition={edition}>`.
4. If it lists issues, read them with `issuesFor(await getCollection("issues"), edition.slug)` so editions never leak into each other.
5. Add a test (section 7).

### Add a component

Create `src/components/Thing.astro` with a typed `Props`, scoped styles using the CSS variables from `global.css`, and import it where used. Anything that renders text from an issue must be escaped by default (`{value}` in a template does this). Never use `set:html` on content from issues.

If you must render inline markdown from the summary, follow `FeaturedItem.astro`: `marked.parseInline` then `DOMPurify.sanitize` with an explicit tag and attribute allow-list. Summaries are LLM-written from untrusted feed text, so treat them as hostile.

### Add a link to a content URL

Always `href={safeHref(item.url)}` and `rel="noopener noreferrer external"`, as `FeaturedItem.astro` does. `safeHref` returns `#` for anything that is not http(s). The schema also rejects other schemes; the two checks are deliberate duplicates.

### Change navigation

The header and footer are in `BaseLayout.astro` (brand, tagline, Search link, RSS link). Archive navigation is `ArchiveSidebar.astro`, fed by `buildArchive()` in `scripts/months.ts`. Month tabs on the front page come from `buildMonthList()`.

### Filter or sort content

Do it in a plain function in `src/scripts/` (so it can be unit-tested without a build) and call it from the page. `months.ts` is the model: `groupByMonth(issues)` returns buckets newest first. `tags/[tag].astro` sorts by date descending then score descending.

### Add a field to the schema

1. Add it to `content.config.ts` (use `.optional()` or a `.default()` so older issue files still validate; the archive contains every past issue).
2. Update `scripts/write.py` to emit it, and its tests.
3. Use it in the component.
4. Add it to a test fixture.

### Dark mode

There is no toggle. `global.css` defines colours as custom properties on `:root` and redefines them inside `@media (prefers-color-scheme: dark)`, so the site follows the visitor's operating-system setting. Shiki code blocks use matching `github-light` and `github-dark` themes (`astro.config.mjs`). To add a colour, define it in **both** blocks and use `var(--name)` in components. Do not hard-code colours in components.

### Styling

Tokens (colours, `--s-1` to `--s-8` spacing, `--fs-xs` to `--fs-4xl` type scale, radii, `--content-max: 760px`) are in `global.css`. Use them. Page-wide primitives (`.container`, `.site-header`, `.chip`, `.item`) live there; component-specific rules go in the component's `<style>`.

The page sets a strict Content-Security-Policy in `BaseLayout.astro` (`default-src 'none'`, with the minimum for Pagefind and the Inter and JetBrains Mono fonts). A new external script, font host or image host needs an explicit change there, and a reason in the PR.

## 6. Search (Pagefind)

`pnpm build` runs `astro build` and then `pagefind --site dist`, which indexes the built HTML into `dist/pagefind/`.

- Issue pages opt in with `data-pagefind-body`, plus `data-pagefind-meta` (date) and `data-pagefind-filter="edition:<slug>"`. The homepage uses `data-pagefind-ignore`.
- `Search.astro` loads `pagefind-ui.js` at runtime and pre-applies the edition filter, so each edition searches only its own issues.
- **Search returns nothing in `pnpm dev`**, because the index does not exist there. Run `pnpm --prefix site build && pnpm --prefix site preview`.
- A new page type is searchable only if it carries `data-pagefind-body`; a section that should not be indexed gets `data-pagefind-ignore`.

## 7. Testing

The suite uses Node's built-in test runner (`node --test`, with `--experimental-strip-types` so `.ts` tests run directly), not Vitest or Jest. The `pnpm test` script also prints coverage.

```bash
pnpm --prefix site test                                  # all
cd site && node --experimental-strip-types --test tests/editions.test.ts   # one file
```

Imports use `node:test` and `node:assert/strict`. Two kinds of test:

1. **Unit tests** for plain modules (`editions.test.ts`, `months.test.ts`, `safe-href.test.ts`, `rss.test.ts`): import the function and assert.
2. **Build tests** for components and pages (`tag-chip.test.ts`, `issue-page.test.ts`, `multi-edition.test.ts`): copy `src/` and config into a temp directory, symlink `node_modules`, write fixture issues and editions, run a real `astro build`, then read the generated HTML and assert on it. Each fixture gets its own Astro and Vite cache directory (see `buildFixtureSite` in `tag-chip.test.ts`) so concurrent builds do not race. These are slower; use them when the output HTML is the thing under test.

`tests/seed-editions.ts` gives a fixture its edition registry: with no argument it copies the real `agents/edition.yaml`, or pass `{ slug: editionYaml({...}) }` for custom ones.

Rules of thumb:

- Prefer extracting logic into `src/scripts/` and unit testing it; reserve build tests for markup.
- Cover the hostile cases for anything that renders item text or URLs: a `javascript:` URL, HTML in a title, a tag that is not a valid slug.
- Tests must not need the network or a real `content` branch.

CI runs `site-tests.yml` (this suite) and `build.yml` (a build on Node 22 and 24, against an empty issues tree) on every PR.

## 8. Performance

The site is static and small. What costs build time is the number of issues (every issue is a page, plus tag, month and search entries). Keep `getStaticPaths()` functions linear in issues, and avoid calling `getCollection("issues")` once per item inside a loop; call it once and filter (`issuesFor`). The build job has a 10-minute timeout in `deploy.yml`.

## 9. Deploy

`deploy.yml` runs on every push to `main` or `content`: it checks out both branches, copies each `editions/<slug>/issues/` from `content` into `site/src/content/issues/<slug>/`, runs `pnpm install --frozen-lockfile` and `pnpm build` with `SITE_URL` and `SITE_BASE` set for GitHub Pages, and publishes `site/dist`. If the build fails (bad frontmatter, orphan folder, Astro error), nothing is published and the previous site stays live.

Consequences for development:

- A bad issue file on `content` breaks the build for everyone. This is why the schema is strict, and why schema changes must stay backward compatible.
- Commit `pnpm-lock.yaml` with dependency changes: CI installs with `--frozen-lockfile`.
- Workflow files are changed by maintainers, not in feature PRs.

## 10. Troubleshooting

| Problem | Cause and fix |
|---|---|
| Build fails: `src/content/issues contains "x", which is not an edition` | a folder or stray file under `site/src/content/issues` has no `editions/x/edition.yaml`; remove it or add the edition |
| `editions/x/ has no edition.yaml` | an edition folder without a config; add the file or remove the folder |
| `slug "x" does not match its folder name` | `slug:` in `edition.yaml` must equal the folder name |
| Schema error naming an issue file | read the Zod message: usual causes are a score outside 0-10, `url` not http(s), missing `metadata`, a `section` that is not papers, news or blogs |
| Edition missing from the site | it is `status: draft`; use `SHOW_DRAFTS=1` locally |
| Search empty in dev | expected; build and preview |
| Links broken under a sub-path | a hard-coded `/`; use `import.meta.env.BASE_URL` |
| Tag chip not a link | the tag fails `isTagSlug` (lowercase letters, digits and hyphens only); this is intentional |
| Stale or odd build state | delete `site/.astro` and `site/dist` and rebuild |

## 11. Resources

- Astro docs: https://docs.astro.build (routing, content collections, `getStaticPaths`)
- Pagefind: https://pagefind.app
- In this repo: [site-architecture.md](site-architecture.md), [multi-edition-operations.md](multi-edition-operations.md) (why orphan issue folders fail the build), `SECURITY-AI.md` (untrusted content rules).
