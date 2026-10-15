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

// Covers src/components/AppendixSection.astro end to end via a real Astro
// build, since the component isn't exported for unit testing in isolation.
// Before this test, AppendixSection had zero coverage: issue-page.test.ts and
// rss.test.ts both always set `appendix: { papers: [], news: [], blogs: [] }`
// in their fixtures (the empty-appendix no-render branch), so the non-empty
// rendering path — the summary count, the per-section grouping, and the
// escaping of item titles/sources — was never exercised.

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function quoted(value: string) {
  return JSON.stringify(value);
}

function appendixItem({
  id,
  section,
  source,
  url,
  title,
}: {
  id: string;
  section: "papers" | "news" | "blogs";
  source: string;
  url: string;
  title: string;
}) {
  return [
    `  - id: ${id}`,
    `    section: ${section}`,
    `    source: ${quoted(source)}`,
    `    url: ${quoted(url)}`,
    `    title: ${quoted(title)}`,
  ].join("\n");
}

function issueMarkdown({
  date,
  appendixPapers,
  appendixNews,
  appendixBlogs,
}: {
  date: string;
  appendixPapers: string[];
  appendixNews: string[];
  appendixBlogs: string[];
}) {
  const section = (label: string, items: string[]) =>
    items.length === 0 ? `  ${label}: []` : [`  ${label}:`, ...items].join("\n");
  const appendixTotal = appendixPapers.length + appendixNews.length + appendixBlogs.length;
  return [
    "---",
    `date: ${quoted(date)}`,
    "featured: []",
    "appendix:",
    section("papers", appendixPapers),
    section("news", appendixNews),
    section("blogs", appendixBlogs),
    "metadata:",
    "  items_considered: 0",
    "  items_featured_total: 0",
    "  items_featured_papers: 0",
    "  items_featured_news: 0",
    "  items_featured_blogs: 0",
    `  items_appendix: ${appendixTotal}`,
    "---",
    "",
  ].join("\n");
}

async function buildFixtureSite() {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-appendix-"));
  const fixtureSite = join(fixtureRoot, "site");

  await mkdir(fixtureSite, { recursive: true });
  await symlink(join(siteRoot, "node_modules"), join(fixtureSite, "node_modules"), "dir");
  await writeFile(
    join(fixtureSite, "package.json"),
    await readFile(join(siteRoot, "package.json")),
  );
  // See issue-page.test.ts / rss.test.ts: each fixture needs its own Astro
  // content-layer cache dir so concurrent `astro build` runs in this suite
  // (and sibling suites) don't race on a shared data-store.json.
  await writeFile(
    join(fixtureSite, "astro.config.mjs"),
    (await readFile(join(siteRoot, "astro.config.mjs"), "utf8")).replace(
      'trailingSlash: "ignore",',
      `trailingSlash: "ignore",\n  cacheDir: ${JSON.stringify(join(fixtureRoot, ".astro-cache"))},`,
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

test("appendix section renders grouped items with escaped titles/sources when non-empty", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite();

  try {
    const issueDirectory = join(fixtureSite, "src", "content", "issues");
    await mkdir(issueDirectory, { recursive: true });

    await writeFile(
      join(issueDirectory, "2026-06-01.md"),
      issueMarkdown({
        date: "2026-06-01",
        appendixPapers: [
          appendixItem({
            id: "p1",
            section: "papers",
            source: "arxiv:cs.AI",
            url: "https://arxiv.org/abs/p1",
            title: "A Paper Title <script>alert(1)</script>",
          }),
          appendixItem({
            id: "p2",
            section: "papers",
            source: "arxiv:cs.LG",
            url: "https://arxiv.org/abs/p2",
            title: "Second Paper",
          }),
        ],
        appendixNews: [
          appendixItem({
            id: "n1",
            section: "news",
            source: "Example News & Co",
            url: "https://example.test/news1",
            title: "A News Item",
          }),
        ],
        // blogs intentionally empty: the per-group `.length > 0` guard
        // should skip rendering a "Blogs" group entirely.
        appendixBlogs: [],
      }),
    );

    runBuild(fixtureSite);

    const html = await readFile(
      join(fixtureSite, "dist", "issues", "2026-06-01", "index.html"),
      "utf8",
    );

    // Summary line totals papers + news + blogs (2 + 1 + 0 = 3).
    assert.ok(
      html.includes("3 more items the ranker flagged but didn't feature"),
      "summary should show the total appendix count across all sections",
    );

    // Both non-empty groups render their heading...
    assert.ok(html.includes('<h4 class="appendix__group-title">Papers</h4>'));
    assert.ok(html.includes('<h4 class="appendix__group-title">News</h4>'));
    // ...but the empty "Blogs" group must not render at all.
    assert.ok(
      !html.includes('<h4 class="appendix__group-title">Blogs</h4>'),
      "an empty appendix section (blogs) must not render its group heading",
    );

    // Each item links to its raw url with the safe-link rel attributes, and
    // carries its source as a sibling span.
    assert.ok(
      html.includes(
        '<a href="https://arxiv.org/abs/p2" rel="noopener noreferrer external">Second Paper</a>',
      ),
      "appendix item should link to its url with noopener/noreferrer/external rel",
    );
    assert.ok(html.includes('<span class="appendix__source">arxiv:cs.LG</span>'));
    assert.ok(
      html.includes(
        '<a href="https://example.test/news1" rel="noopener noreferrer external">A News Item</a>',
      ),
    );
    assert.ok(
      html.includes('<span class="appendix__source">Example News &#x26; Co</span>') ||
        html.includes('<span class="appendix__source">Example News &amp; Co</span>'),
      "ampersand in source should be HTML-escaped",
    );

    // Title and source are plain interpolation ({it.title} / {it.source}),
    // not set:html, so a malicious title string must render as escaped,
    // inert text rather than live markup — mirrors the tag-chip escaping
    // assertion in issue-page.test.ts.
    assert.ok(
      !html.includes("<script>alert(1)</script>"),
      "appendix item title must not inject an executable script tag",
    );
    assert.ok(
      html.includes("&lt;script&gt;alert(1)&lt;/script&gt;"),
      "appendix item title should be HTML-escaped, not parsed as markup",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("appendix section does not render at all when every section is empty", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite();

  try {
    const issueDirectory = join(fixtureSite, "src", "content", "issues");
    await mkdir(issueDirectory, { recursive: true });

    await writeFile(
      join(issueDirectory, "2026-06-02.md"),
      issueMarkdown({
        date: "2026-06-02",
        appendixPapers: [],
        appendixNews: [],
        appendixBlogs: [],
      }),
    );

    runBuild(fixtureSite);

    const html = await readFile(
      join(fixtureSite, "dist", "issues", "2026-06-02", "index.html"),
      "utf8",
    );

    assert.ok(
      !html.includes('<details class="appendix">'),
      "an all-empty appendix must not render the <details> wrapper at all",
    );
    assert.ok(!html.includes("more items the ranker flagged"));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
