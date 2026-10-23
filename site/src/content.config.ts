import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

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

const issues = defineCollection({
  // Issues live at issues/<edition>/YYYY-MM-DD.md (multi-edition layout, #301).
  // The flat issues/YYYY-MM-DD.md form is still accepted so the site keeps
  // building from a content branch that has not been migrated yet; the edition
  // is the first path segment of the entry id, and URLs are built from
  // `data.date`, never from the id, so both layouts produce the same URLs.
  loader: glob({ pattern: ["*/*.md", "*.md"], base: "./src/content/issues" }),
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
