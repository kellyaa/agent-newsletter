import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";
import { existsSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { loadRegistry, unknownContentEntries } from "./scripts/editions";

const SECTIONS = ["papers", "news", "blogs"] as const;

// Item URLs come from scraped/fed content (RSS, arXiv, HN, scraped HTML)
// and flow straight into rendered <a href> on the public site and in the
// RSS feed. `.url()` alone accepts javascript:/data:/file: schemes (zod 3
// only checks that the string parses as a URL, not its scheme), so without
// this refinement a malicious/compromised feed entry becomes a
// click-to-execute link. scripts/fetch.py's upsert_items() already rejects
// non-http(s) URLs before they reach state.db, but this schema check is a
// second line of defense at the content-collection boundary. See #248.
const httpUrl = z
  .string()
  .url()
  .refine((u) => /^https?:\/\//i.test(u), {
    message: "url must use the http or https scheme",
  });

const featuredItem = z.object({
  id: z.string(),
  section: z.enum(SECTIONS),
  source: z.string(),
  url: httpUrl,
  title: z.string(),
  author: z.string().nullable().optional(),
  score: z.number().int().min(0).max(10),
  tags: z.array(z.string()).default([]),
  /** The writer's prose summary in markdown, ~60-100 words. */
  summary: z.string(),
  /** Optional editorial callout. */
  takeaway: z.string().nullable().optional(),
  open_question: z.string().nullable().optional(),
});

const appendixItem = z.object({
  id: z.string(),
  section: z.enum(SECTIONS),
  source: z.string(),
  url: httpUrl,
  title: z.string(),
});

// Multi-edition layout (#291): issues live at issues/<edition>/YYYY-MM-DD.md,
// staged there from the content branch's editions/<slug>/issues/. The edition
// is the first path segment of the entry id. The registry (editions/*/edition.yaml
// on main) decides which editions exist: content for a folder no edition owns,
// or a stray flat issues/YYYY-MM-DD.md from the pre-migration layout, fails the
// build instead of being silently skipped. Draft editions are known (so their
// staged content is not an error) but are only globbed when SHOW_DRAFTS=1.
const registry = loadRegistry();
const issuesRoot = resolve(process.cwd(), "src/content/issues");
const stray = unknownContentEntries(
  existsSync(issuesRoot) ? readdirSync(issuesRoot) : [],
  registry.all,
);
if (stray.length > 0) {
  throw new Error(
    `src/content/issues contains ${stray.map((s) => JSON.stringify(s)).join(", ")}, ` +
      "which is not an edition in editions/*/edition.yaml " +
      "(issues must live at issues/<edition>/YYYY-MM-DD.md)",
  );
}

const issues = defineCollection({
  // With no listed edition the pattern must still match nothing, not everything.
  loader: glob({
    pattern: registry.listed.length > 0 ? registry.listed.map((e) => `${e.slug}/*.md`) : ["!**"],
    base: "./src/content/issues",
  }),
  schema: z.object({
    /** YYYY-MM-DD; also the slug. */
    date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
    /** Optional 1–2 sentence theme. Render only if present and non-empty. */
    theme: z.string().nullable().optional(),
    featured: z.array(featuredItem).default([]),
    appendix: z.object({
      papers: z.array(appendixItem).default([]),
      news: z.array(appendixItem).default([]),
      blogs: z.array(appendixItem).default([]),
    }),
    metadata: z.object({
      items_considered: z.number().int().nonnegative(),
      items_featured_total: z.number().int().nonnegative(),
      items_featured_papers: z.number().int().nonnegative(),
      items_featured_news: z.number().int().nonnegative(),
      items_featured_blogs: z.number().int().nonnegative(),
      items_appendix: z.number().int().nonnegative(),
      cost_usd: z.number().nullable().optional(),
      duration_seconds: z.number().int().nullable().optional(),
    }),
  }),
});

export const collections = { issues };
