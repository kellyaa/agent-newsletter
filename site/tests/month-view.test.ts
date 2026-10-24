import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  cp,
  mkdtemp,
  mkdir,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { seedEditions } from "./seed-editions.ts";

// Covers src/components/MonthView.astro end to end via a real Astro build.
// MonthView has its own date-formatting (parseISODate) and count-labeling
// (countsString) helpers that aren't exported for isolated unit testing, and
// the component itself (top-3 featured sort, tag dedup/slice, current-month
// highlighting) has no existing coverage — unlike FeaturedItem, months.ts,
// and rss.xml.js, which are each covered by a sibling test in this
// directory.

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function quoted(value: string) {
  return JSON.stringify(value);
}

function featuredItemYaml({
  id,
  score,
  tags,
  title = "Untitled",
}: {
  id: string;
  score: number;
  tags: string[];
  title?: string;
}) {
  const tagList = tags.map(quoted).join(", ");
  return [
    `  - id: ${quoted(id)}`,
    "    section: news",
    '    source: "Example"',
    `    url: ${quoted(`https://example.test/${id}`)}`,
    `    title: ${quoted(title)}`,
    `    score: ${score}`,
    `    tags: [${tagList}]`,
    `    summary: ${quoted("A summary.")}`,
  ].join("\n");
}

function issueMarkdown({
  date,
  theme,
  featured,
  counts,
}: {
  date: string;
  theme: string;
  featured: string[];
  counts: { papers: number; news: number; blogs: number; appendix: number };
}) {
  return [
    "---",
    `date: ${quoted(date)}`,
    `theme: ${quoted(theme)}`,
    "featured:",
    ...featured,
    "appendix:",
    "  papers: []",
    "  news: []",
    "  blogs: []",
    "metadata:",
    "  items_considered: 0",
    "  items_featured_total: 0",
    `  items_featured_papers: ${counts.papers}`,
    `  items_featured_news: ${counts.news}`,
    `  items_featured_blogs: ${counts.blogs}`,
    `  items_appendix: ${counts.appendix}`,
    "---",
    "",
  ].join("\n");
}

test("month archive page formats dates, counts, top-3 featured, and tag dedup/slice", async () => {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-monthview-"));
  const fixtureSite = join(fixtureRoot, "site");

  try {
    await mkdir(fixtureSite, { recursive: true });
    await symlink(join(siteRoot, "node_modules"), join(fixtureSite, "node_modules"), "dir");
    await writeFile(
      join(fixtureSite, "package.json"),
      await readFile(join(siteRoot, "package.json")),
    );
    // Astro's content-layer cache defaults to <root>/node_modules/.astro,
    // AND Vite's own dependency-optimization cache defaults to
    // <root>/node_modules/.vite (hardcoded in astro/dist/core/create-vite.js,
    // not derived from Astro's `cacheDir`). The fixture's node_modules is a
    // *symlink* to the real site's (so Astro's own dependency tree doesn't
    // need reinstalling per test), which means BOTH default cache paths
    // resolve to the SAME real directory for every fixture — including the
    // ones the sibling issue-page.test.ts and rss.test.ts builds run
    // concurrently. Two `astro build` runs racing on either shared cache
    // corrupts whichever build loses the race, producing a nondeterministic
    // failure in the built output. Astro's `cacheDir` option only relocates
    // the content-layer cache; Vite's cache must be relocated separately via
    // the `vite.cacheDir` passthrough (see astro/dist/core/create-vite.js:
    // `settings.config.vite` is merged into Vite's config after the
    // `node_modules/.vite` default, so a user-supplied vite.cacheDir wins).
    // The already-merged fix for issue-page.test.ts/rss.test.ts (PR #239)
    // only set the content-layer `cacheDir` and missed this — see the issue
    // filed alongside this test for the follow-up fix to those two files.
    await writeFile(
      join(fixtureSite, "astro.config.mjs"),
      (await readFile(join(siteRoot, "astro.config.mjs"), "utf8")).replace(
        'trailingSlash: "ignore",',
        [
          'trailingSlash: "ignore",',
          `  cacheDir: ${JSON.stringify(join(fixtureRoot, ".astro-cache"))},`,
          `  vite: { cacheDir: ${JSON.stringify(join(fixtureRoot, ".vite-cache"))} },`,
        ].join("\n"),
      ),
    );
    await writeFile(
      join(fixtureSite, "tsconfig.json"),
      await readFile(join(siteRoot, "tsconfig.json")),
    );
    await cp(join(siteRoot, "src"), join(fixtureSite, "src"), { recursive: true });
    await seedEditions(fixtureRoot);

    const issueDirectory = join(fixtureSite, "src", "content", "issues", "agents");
    await mkdir(issueDirectory, { recursive: true });

    // Four featured items, scores out of order, to exercise the top-3 sort
    // and the Set-based tag dedup+slice(0,6) in MonthView. "shared-tag"
    // appears on three items and must be deduped to a single chip; the
    // combined unique tag count (7) must be truncated to 6.
    const featured = [
      featuredItemYaml({ id: "item-low", score: 2, tags: ["shared-tag", "tag-a"], title: "Low score item" }),
      featuredItemYaml({ id: "item-high", score: 9, tags: ["shared-tag", "tag-b"], title: "Highest score item" }),
      featuredItemYaml({ id: "item-mid", score: 5, tags: ["shared-tag", "tag-c", "tag-d"], title: "Mid score item" }),
      featuredItemYaml({ id: "item-excluded", score: 1, tags: ["tag-e", "tag-f"], title: "Fourth item excluded from top 3" }),
    ];

    // Friday, June 5 2026 — exercises parseISODate's day-of-week/month lookup.
    await writeFile(
      join(issueDirectory, "2026-06-05.md"),
      issueMarkdown({
        date: "2026-06-05",
        theme: "June roundup theme",
        featured,
        counts: { papers: 1, news: 2, blogs: 0, appendix: 3 },
      }),
    );

    // A second, newer-month issue so groupByMonth/buildMonthList produce
    // more than one bucket — getStaticPaths only emits month pages when
    // buckets.length > 1, and the newest bucket is excluded (it's the home
    // page), so this is required for the June page to be built at all.
    await writeFile(
      join(issueDirectory, "2026-07-20.md"),
      issueMarkdown({
        date: "2026-07-20",
        theme: "July theme",
        featured: [featuredItemYaml({ id: "july-item", score: 4, tags: [], title: "July item" })],
        counts: { papers: 0, news: 1, blogs: 0, appendix: 0 },
      }),
    );

    execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
      cwd: fixtureSite,
      env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test" },
      stdio: "pipe",
    });

    const html = await readFile(
      join(fixtureSite, "dist", "agents", "months", "2026-06", "index.html"),
      "utf8",
    );

    // parseISODate: 2026-06-05 is a Friday in June.
    assert.ok(html.includes(">FRI<"), "day-of-week abbreviation should be computed from the ISO date");
    assert.ok(html.includes("JUN 5"), "month abbreviation and day-of-month should be computed from the ISO date");

    // countsString: zero-valued fields are omitted; non-zero fields render
    // with their label, in papers/news/blogs/appendix order.
    assert.ok(html.includes("1 paper"), "singular 'paper' label should render for count 1");
    assert.ok(html.includes("2 news"), "news count should render");
    assert.ok(!html.includes("blog"), "zero-valued blogs count must be omitted from the June page");
    assert.ok(html.includes("3 appendix"), "appendix count should render");

    // Top-3 featured sort: highest-first by score, 4th item excluded.
    const highIdx = html.indexOf("Highest score item");
    const midIdx = html.indexOf("Mid score item");
    const lowIdx = html.indexOf("Low score item");
    assert.ok(highIdx !== -1 && midIdx !== -1 && lowIdx !== -1, "top 3 items should all render");
    assert.ok(highIdx < midIdx && midIdx < lowIdx, "featured items should be ordered by descending score");
    assert.ok(
      !html.includes("Fourth item excluded from top 3"),
      "only the top 3 featured items by score should render in the timeline card",
    );

    // Tag dedup + slice(0, 6): "shared-tag" appears on 3 of the top-3 items
    // but must render once; the unique tag set (shared-tag, tag-a..d = 5)
    // is under the cap here, so all should appear, but duplicates must not.
    const sharedTagMatches = html.match(/>shared-tag</g) ?? [];
    assert.equal(sharedTagMatches.length, 1, "a tag shared across featured items must be deduplicated, not repeated");

    // Current-month highlighting in the nav: June is the page being viewed.
    assert.ok(
      html.includes('aria-current="page"'),
      "the archive nav should mark the current month page with aria-current",
    );
    assert.ok(
      html.includes("month-nav__item--current"),
      "the archive nav should apply the current-month CSS class to June",
    );

    // The newer (July) bucket is the latest month, so its nav link must
    // point at the base path, not a /months/2026-07/ page (home renders it).
    assert.ok(
      html.includes(`href="/newsletter/agents/"`),
      "the latest month in the nav should link to the site root, not a dedicated month page",
    );

    // Theme text for this issue should render inside the timeline card.
    assert.ok(html.includes("June roundup theme"), "the issue's theme should render in the timeline card");
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
