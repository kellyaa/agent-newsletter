# Site Architecture & Developer Guide

The published newsletter surface is an Astro 5 static site with multi-edition support, full-text search via Pagefind, RSS feeds, and tag-based browsing.

## Quick Start

```bash
# Install dependencies
pnpm --prefix site install

# Local development server (rebuilds on changes)
pnpm --prefix site dev

# Build for production (runs Astro build + Pagefind indexing)
pnpm --prefix site build

# Run tests
pnpm --prefix site test
```

The local dev server reads issue files from `.worktrees/content/site/src/content/issues/` (the content worktree, which holds the `content` branch's generated files). If you haven't set up the worktree yet:

```bash
git worktree add .worktrees/content content
```

## Directory Structure

```
site/
  src/
    pages/                 -- Astro dynamic routes
      index.astro          -- edition picker (/), also serves /rss.xml
      [edition]/
        index.astro        -- edition index (/:edition/)
        search.astro       -- full-text search UI (/:edition/search/)
        issues/
          [date].astro     -- single issue page (/:edition/issues/YYYY-MM-DD/)
        tags/
          index.astro      -- tag cloud (/:edition/tags/)
          [tag].astro      -- items tagged with :tag (/:edition/tags/:tag/)
        months/
          [yyyymm].astro   -- archive page (/:edition/months/:yyyy-mm/)
    components/            -- Astro components
      *.astro              -- reusable UI components (see Components section below)
    scripts/               -- TypeScript modules (no Astro framework code)
      editions.ts          -- edition registry loader (site build-time)
      months.ts            -- month/date helpers
      tag-slug.ts          -- URL-safe tag slugging
      safe-href.ts         -- XSS-safe URL handling
    layouts/
      BaseLayout.astro     -- app shell (nav, footer, head)
    styles/                -- CSS (scoped per component + global)
    content/               -- Astro content collection
      config.ts            -- collection schema definition
      issues/              -- populated by deploy workflow from content branch
  tests/
    *.test.ts              -- Vitest test suite (see Testing section below)
  astro.config.mjs         -- Astro build config
  package.json
  tsconfig.json
  pnpm-lock.yaml
```

## Data Flow

### Build Time (pnpm build / CI)

1. **Edition registry loader** (`src/scripts/editions.ts`, run at build):
   - Walks `editions/*/edition.yaml` on `main` branch
   - Validates slug, status, order, and required fields
   - Fails build if any edition file is malformed or status is invalid
   - Exports registry as `EditionRegistry` used by all pages

2. **Issue content** (copied by CI workflow):
   - `.github/workflows/deploy.yml` checks out `content` branch into `_content/`
   - Copies each `_content/editions/<slug>/issues/YYYY-MM-DD.md` to `site/src/content/issues/<slug>/YYYY-MM-DD.md`
   - Astro treats these as a content collection; schema is in `src/content/config.ts`
   - The build fails if an issue file exists but its edition is not in the registry (stray content guard)

3. **Pagefind indexing** (post-build):
   - After Astro finishes, `pnpm build` runs `pagefind --site dist`
   - Generates static search index in `dist/pagefind/`
   - Search is available only in the built output, not in `astro dev` (by design)

### Runtime (Browser)

- Pages are 100% pre-rendered HTML + CSS + minimal JS (Pagefind client-side search widget)
- No API calls; all routing and filtering happen client-side on pre-built page sets
- RSS feeds (`/rss.xml`, `/:edition:/rss.xml`) are generated at build time using `@astrojs/rss`

## Components

Each Astro component is a self-contained UI block. Review the interfaces in `src/components/` to understand props.

### FeaturedItem.astro
**Props:**
```typescript
interface Props {
  item: Item;  // featured article with title, source, score, tags, summary, etc.
  edition: string;  // slug for tag-link routing
}
```
Renders a single article card: title → link, source, score bar, tags, summary (sanitized markdown), and optional takeaway/question callouts. Uses `safeInline()` to convert markdown to HTML and sanitize against XSS.

### AppendixSection.astro
**Props:**
```typescript
interface Props {
  title: string;           // e.g. "Uncertain (Appendix)"
  items: AppendixItem[];   // items with id, section, source, url, title only
  edition: string;
}
```
Renders items marked as `appendix` status — lower-confidence items shown at the bottom of an issue. Each item is a minimal card (no score, tags, or summary).

### MonthView.astro
**Props:**
```typescript
interface Props {
  month: string;           // "2026-06" format
  issues: IssueSummary[];  // issues published in that month
}
```
Grid of issue cards (date + theme excerpt) for monthly archive pages.

### Callout.astro
**Props:**
```typescript
interface Props {
  type: "takeaway" | "question";
}
```
Styled container for highlighted content (takeaways or open questions within an issue).

### TagChip.astro
**Props:**
```typescript
interface Props {
  label: string;
  edition: string;
}
```
A single tag with link to `/:edition/tags/:slug/`.

### ScoreBar.astro
**Props:**
```typescript
interface Props {
  score: number;  // 0–100
}
```
Visual representation of item ranking score (visual bar).

### Search.astro (Interactive)
No props; this is a client-side search widget that loads the Pagefind index at runtime and renders results inline.

## Testing

Site tests live in `site/tests/` and run with `pnpm --prefix site test` (Vitest). Tests are _not_ end-to-end browser tests; they verify:

1. **Edition registry parsing**: `editions.test.ts`
   - YAML parsing, slug validation, status enum, required fields
   - Malformed files reject with clear error messages

2. **Page routing & URL generation**: `index-page.test.ts`, `issue-page.test.ts`, etc.
   - URLs are correctly formatted for each edition/date/tag combination
   - Draft editions are hidden unless `SHOW_DRAFTS=1`

3. **Content rendering**: `base-layout.test.ts`, `featured-item.test.ts`, `issue-page-sections.test.ts`
   - Components render their props without errors
   - Sanitization (DOMPurify) prevents XSS injection
   - Markdown is correctly parsed inline

4. **RSS feeds**: `rss.test.ts`
   - Feed structure is valid XML
   - Item counts and fields match the issue

5. **Search & tag logic**: `search.test.ts`, `tag-pages.test.ts`, `tag-chip.test.ts`
   - URL-safe slugging
   - Tag collection and filtering

6. **Multi-edition hosting**: `multi-edition.test.ts`
   - Correct routes per edition
   - Stray content (issue with no edition) is caught at build

### Writing a New Test

1. Create a `.test.ts` file in `site/tests/` following the naming convention
2. Import test utilities:
   ```typescript
   import { describe, it, expect } from "vitest";
   import { parseEdition } from "../src/scripts/editions";
   ```
3. Use mocked issue data from `seed-editions.ts` to set up test fixtures:
   ```typescript
   import { seedEditions } from "./seed-editions";
   ```
4. Test the function or component:
   ```typescript
   describe("parseEdition", () => {
     it("rejects invalid YAML", () => {
       expect(() => parseEdition("not: valid: yaml:", "test")).toThrow();
     });
   });
   ```

Run locally with `pnpm --prefix site test` or `pnpm --prefix site test --watch` (watch mode).

## Build & Deploy

The `.github/workflows/deploy.yml` workflow:

1. Triggers on push to `main` or `content` branch
2. Checks out `main` as primary
3. Fetches `content` branch into `_content/` (a subdir, not a git worktree)
4. Copies issue files from `_content/editions/` → `site/src/content/issues/`
5. Runs `pnpm --prefix site build`
6. Deploys `dist/` to GitHub Pages

**Build gates:**
- Edition registry must be valid (caught by `src/scripts/editions.ts`)
- No stray issue files (caught by Astro content collection schema)
- All tests pass (CI step before deploy)

**Important:** If any step fails, the build stops and the last-good site stays live. Push a fix to `main` and re-trigger the workflow.

## Common Development Tasks

### Preview an issue locally before it publishes

Before the daily pipeline writes and pushes an issue file to `content`:

1. Manually create (or copy from a past run) a test issue file:
   ```bash
   mkdir -p .worktrees/content/site/src/content/issues/agents/
   cat > .worktrees/content/site/src/content/issues/agents/2026-06-29.md << 'EOF'
   ---
   date: 2026-06-29
   theme: "Your test theme here..."
   ---
   
   ## Papers
   ...
   EOF
   ```
2. Run `pnpm --prefix site dev` and open http://localhost:3000/agents/issues/2026-06-29/

### Add a new tag to the tag cloud

Tags are automatically collected at build time from `items.tags` in issue files. To add a tag to an issue:

1. Edit the issue file in `.worktrees/content/site/src/content/issues/<edition>/YYYY-MM-DD.md`
2. Add the tag to an item's frontmatter or in the item summary
3. Rebuild (`pnpm --prefix site build`) — the tag will appear in the tag cloud

### Add a new component

1. Create `site/src/components/NewComponent.astro`:
   ```astro
   ---
   // Define the component's props interface
   interface Props {
     title: string;
     children?: any;
   }
   const { title } = Astro.props;
   ---
   <div class="new-component">
     <h2>{title}</h2>
     <slot />
   </div>

   <style>
     .new-component {
       /* scoped CSS */
     }
   </style>
   ```
2. Import and use in a page:
   ```astro
   import NewComponent from "../components/NewComponent.astro";
   <NewComponent title="Hello" />
   ```
3. Add a test in `site/tests/new-component.test.ts`

## Frequently Asked Questions

**Q: Why is search not working in `astro dev`?**
A: Pagefind indexing only runs after `pnpm build`. For local search testing, run `pnpm --prefix site build` and then `pnpm --prefix site preview` (serves the built output).

**Q: How do I debug a page build failure?**
A: Look at the build error message — it usually names the issue file that failed. Common causes:
- Issue file frontmatter is invalid YAML
- An issue file exists but its edition is not in `editions/*/edition.yaml` or has `status: draft` (and `SHOW_DRAFTS` is not set)
- A component is importing from Astro unsafely (must be in the `---` script block)

**Q: Can I run just one test?**
A: Yes: `pnpm --prefix site test -- editions.test.ts`

**Q: How do draft editions appear on the site?**
A: By default, draft editions are hidden. To show them in local dev:
```bash
SHOW_DRAFTS=1 pnpm --prefix site dev
```
In CI, drafts are always hidden unless explicitly built with `SHOW_DRAFTS=1`.

## Performance Notes

- Pre-rendering all pages keeps response times sub-100ms (GitHub Pages CDN)
- Pagefind indexing adds ~5–10 seconds to the build but search is instant client-side
- Content is versioned on the `content` branch, so old issues remain queryable forever
