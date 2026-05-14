import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

const SECTIONS = ["papers", "news", "blogs"] as const;

const featuredItem = z.object({
  id: z.string(),
  section: z.enum(SECTIONS),
  source: z.string(),
  url: z.string().url(),
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
  url: z.string().url(),
  title: z.string(),
});

const issues = defineCollection({
  loader: glob({ pattern: "*.md", base: "./src/content/issues" }),
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
