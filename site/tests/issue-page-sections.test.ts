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

// Covers src/pages/issues/[...slug].astro's section bucketing
// (sectionGroups/itemsBySection: group featured items by section, render a
// section header only when its count > 0) and src/components/ScoreBar.astro
// (filled-cell count, aria-label), end to end via a real Astro build.
//
// issue-page.test.ts (sanitization coverage) only ever builds a single
// featured item in a single section (news) with metadata counts hardcoded
// to papers=0/news=1/blogs=0, so it never exercises:
//   - a section with count=0 being omitted from the page entirely
//   - multiple sections rendering side by side, each with its own items
//     filtered correctly by `item.section`
//   - ScoreBar's cell-fill math at a score other than the one value it
//     happens to use (7), or ScoreBar's aria-label
//   - multiple tags rendering as multiple separate chips (vs. its one test
//     of a single malicious tag's escaping)
//   - the optional `item.author` field's conditional "— Author" rendering,
//     or its omission when absent

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function quoted(value: string) {
  return JSON.stringify(value);
}

function featuredItem({
  id,
  section,
  title,
  score,
  tags = [],
  author,
}: {
  id: string;
  section: "papers" | "news" | "blogs";
  title: string;
  score: number;
  tags?: string[];
  author?: string;
}) {
  const lines = [
    `  - id: ${id}`,
    `    section: ${section}`,
    '    source: "Example"',
    `    url: ${quoted(`https://example.test/${id}`)}`,
    `    title: ${quoted(title)}`,
    `    score: ${score}`,
    `    summary: ${quoted("Summary text.")}`,
  ];
  if (tags.length > 0) {
    lines.push(`    tags: [${tags.map(quoted).join(", ")}]`);
  }
  if (author !== undefined) {
    lines.push(`    author: ${quoted(author)}`);
  }
  return lines.join("\n");
}

function issueMarkdown({
  date,
  featuredEntries,
  papers,
  news,
  blogs,
}: {
  date: string;
  featuredEntries: string[];
  papers: number;
  news: number;
  blogs: number;
}) {
  return [
    "---",
    `date: ${quoted(date)}`,
    "featured:",
    ...featuredEntries,
    "appendix:",
    "  papers: []",
    "  news: []",
    "  blogs: []",
    "metadata:",
    "  items_considered: 0",
    `  items_featured_total: ${papers + news + blogs}`,
    `  items_featured_papers: ${papers}`,
    `  items_featured_news: ${news}`,
    `  items_featured_blogs: ${blogs}`,
    "  items_appendix: 0",
    "---",
    "",
  ].join("\n");
}

async function buildFixtureSite() {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-sections-"));
  const fixtureSite = join(fixtureRoot, "site");

  await mkdir(fixtureSite, { recursive: true });
  await symlink(join(siteRoot, "node_modules"), join(fixtureSite, "node_modules"), "dir");
  await writeFile(
    join(fixtureSite, "package.json"),
    await readFile(join(siteRoot, "package.json")),
  );
  // Astro's content-layer cache defaults to <root>/node_modules/.astro, AND
  // Vite's own dependency-optimization cache defaults separately to
  // <root>/node_modules/.vite (hardcoded in astro/dist/core/create-vite.js,
  // NOT derived from Astro's `cacheDir` option). The fixture's node_modules
  // is a *symlink* to the real site's (so Astro's own dependency tree
  // doesn't need reinstalling per test), which means BOTH default cache
  // paths resolve to the SAME real directory for every fixture — including
  // the ones sibling Astro-build suites (issue-page.test.ts, rss.test.ts,
  // month-view.test.ts, appendix-section.test.ts) run concurrently. Two
  // `astro build` runs racing on either shared cache corrupts whichever
  // build loses the race (see PR #238's thread for the reproduction: 4/5
  // concurrent runs failed with only `cacheDir` set). `vite: { cacheDir }`
  // is required too — Astro merges `settings.config.vite` into Vite's own
  // config, overriding the hardcoded `node_modules/.vite` default (see
  // astro/dist/core/create-vite.js). Point both caches at this fixture's own
  // tmp root so each build gets fully isolated caches.
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

  return { fixtureRoot, fixtureSite };
}

function runBuild(fixtureSite: string) {
  execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
    cwd: fixtureSite,
    env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test" },
    stdio: "pipe",
  });
}

test("issue page buckets featured items by section, omits empty sections, and renders ScoreBar/tags/author", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite();

  try {
    const issueDirectory = join(fixtureSite, "src", "content", "issues");
    await mkdir(issueDirectory, { recursive: true });

    const featuredEntries = [
      featuredItem({ id: "pa1", section: "papers", title: "Paper One", score: 10 }),
      featuredItem({
        id: "ne1",
        section: "news",
        title: "News With Tags And Author",
        score: 3,
        tags: ["evals", "safety"],
        author: "Jane Doe",
      }),
      featuredItem({ id: "ne2", section: "news", title: "News No Author", score: 0 }),
      // blogs intentionally has zero entries: metadata.items_featured_blogs
      // stays 0, so the "Blogs" section header must not render at all.
    ];

    await writeFile(
      join(issueDirectory, "2026-06-01.md"),
      issueMarkdown({
        date: "2026-06-01",
        featuredEntries,
        papers: 1,
        news: 2,
        blogs: 0,
      }),
    );

    runBuild(fixtureSite);

    const html = await readFile(
      join(fixtureSite, "dist", "issues", "2026-06-01", "index.html"),
      "utf8",
    );

    // Papers and News sections render (count > 0 for both)...
    assert.ok(html.includes('<section class="section" id="papers">'));
    assert.ok(html.includes('<section class="section" id="news">'));
    // ...but Blogs (count === 0) must be omitted entirely, not rendered
    // empty — this is the sectionGroups.map(... g.count > 0 && (...)) guard.
    assert.ok(
      !html.includes('<section class="section" id="blogs">'),
      "a section with zero featured items must not render at all",
    );
    assert.ok(
      !html.includes('<h2 class="section__title">Blogs</h2>'),
      "an empty section's heading must not render",
    );

    // Items are filtered into the right section bucket: Paper One must
    // appear inside the papers section's article block, not news/blogs.
    const papersBlockStart = html.indexOf('<section class="section" id="papers">');
    const newsBlockStart = html.indexOf('<section class="section" id="news">');
    const paperOneIndex = html.indexOf("Paper One");
    assert.ok(
      paperOneIndex > papersBlockStart && paperOneIndex < newsBlockStart,
      "Paper One (section: papers) must render inside the papers section, before the news section starts",
    );

    // ScoreBar: score=10/max=10 -> all 10 cells filled, aria-label reflects
    // both score and max.
    assert.ok(html.includes('aria-label="score 10 out of 10"'));
    const scoreTenBlockStart = html.indexOf('aria-label="score 10 out of 10"');
    const scoreTenBlockEnd = html.indexOf("scorebar__num", scoreTenBlockStart);
    const scoreTenTrack = html.slice(scoreTenBlockStart, scoreTenBlockEnd);
    assert.equal(
      (scoreTenTrack.match(/scorebar__cell is-filled/g) || []).length,
      10,
      "a score of 10 should fill all 10 scorebar cells",
    );
    assert.ok(html.includes(">10/10<"), "ScoreBar should render the numeric score/max label");

    // ScoreBar: score=0 -> zero cells filled, label still renders "0/10".
    assert.ok(html.includes('aria-label="score 0 out of 10"'));
    const scoreZeroBlockStart = html.indexOf('aria-label="score 0 out of 10"');
    const scoreZeroBlockEnd = html.indexOf("scorebar__num", scoreZeroBlockStart);
    const scoreZeroTrack = html.slice(scoreZeroBlockStart, scoreZeroBlockEnd);
    assert.equal(
      (scoreZeroTrack.match(/scorebar__cell is-filled/g) || []).length,
      0,
      "a score of 0 should fill zero scorebar cells",
    );
    assert.ok(html.includes(">0/10<"));

    // Multiple tags each render as their own chip (not merged/truncated).
    assert.ok(html.includes('<span class="chip">evals</span>'));
    assert.ok(html.includes('<span class="chip">safety</span>'));

    // Author renders as "— Name" when present...
    assert.ok(
      html.includes('<span class="item__author">— Jane Doe</span>'),
      "author should render prefixed with an em dash",
    );
    // ...and the author span must be entirely absent for an item with no
    // author field (not rendered empty).
    const newsNoAuthorIndex = html.indexOf("News No Author");
    const nextArticleIndex = html.indexOf("</article>", newsNoAuthorIndex);
    const newsNoAuthorBlock = html.slice(newsNoAuthorIndex, nextArticleIndex);
    assert.ok(
      !newsNoAuthorBlock.includes('class="item__author"'),
      "an item with no author must not render an item__author span at all",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
