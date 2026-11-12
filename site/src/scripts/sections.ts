// The newsletter section vocabulary, as the site sees it. This is the only
// place the site spells the section names; content.config.ts (zod schema),
// the issue page, the tag pages, FeaturedItem and AppendixSection all take
// them from here. Values and order match scripts/models.py `Section`
// (tests/test_site_sections_single_source.py enforces this).

export const SECTIONS = ["papers", "news", "blogs"] as const;

export type Section = (typeof SECTIONS)[number];

/** Display strings per section: heading label, and count nouns (1 / many). */
export const SECTION_DISPLAY: Record<Section, { label: string; one: string; many: string }> = {
  papers: { label: "Papers", one: "paper", many: "papers" },
  news: { label: "News", one: "news", many: "news" },
  blogs: { label: "Blogs", one: "blog", many: "blogs" },
};

/** The issue `metadata` key holding a section's featured count. */
export type FeaturedCountKey = `items_featured_${Section}`;

export function featuredCountKey(section: Section): FeaturedCountKey {
  return `items_featured_${section}`;
}

/** "1 paper", "3 blogs", "2 news". */
export function sectionCount(section: Section, n: number): string {
  const d = SECTION_DISPLAY[section];
  return `${n} ${n === 1 ? d.one : d.many}`;
}
