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

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function quoted(value: string) {
  return JSON.stringify(value);
}

function issueMarkdown({
  date,
  theme,
  featured,
}: {
  date: string;
  theme?: string;
  featured: string;
}) {
  return [
    "---",
    `date: ${quoted(date)}`,
    ...(theme === undefined ? [] : [`theme: ${quoted(theme)}`]),
    ...(featured === "[]" ? ["featured: []"] : ["featured:", featured]),
    "appendix:",
    "  papers: []",
    "  news: []",
    "  blogs: []",
    "metadata:",
    "  items_considered: 0",
    "  items_featured_total: 0",
    "  items_featured_papers: 0",
    "  items_featured_news: 0",
    "  items_featured_blogs: 0",
    "  items_appendix: 0",
    "---",
    "",
  ].join("\n");
}

function featuredItem({
  url,
  title,
  author,
  summary,
  takeaway,
  openQuestion,
  score = 10,
}: {
  url: string;
  title: string;
  author?: string;
  summary: string;
  takeaway?: string;
  openQuestion?: string;
  score?: number;
}) {
  return [
    "  - id: featured",
    "    section: news",
    '    source: "Example"',
    `    url: ${quoted(url)}`,
    `    title: ${quoted(title)}`,
    `    score: ${score}`,
    `    summary: ${quoted(summary)}`,
    ...(author === undefined ? [] : [`    author: ${quoted(author)}`]),
    ...(takeaway === undefined ? [] : [`    takeaway: ${quoted(takeaway)}`]),
    ...(openQuestion === undefined ? [] : [`    open_question: ${quoted(openQuestion)}`]),
  ].join("\n");
}

test("RSS renders newest issues, links, optional fields, and escaped content", async () => {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-rss-"));
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
    // NOT derived from Astro's `cacheDir` option). The fixture's
    // node_modules is a *symlink* to the real site's (so Astro's own
    // dependency tree doesn't need reinstalling per test), which means BOTH
    // default cache paths resolve to the SAME real directory for every
    // fixture — including the ones the sibling issue-page.test.ts and
    // month-view.test.ts builds run concurrently. Two `astro build` runs
    // racing on either shared cache corrupts whichever build loses the
    // race, producing a nondeterministic failure in the built output (first
    // surfaced once CI actually ran this suite in parallel, see PR adding
    // site-tests.yml). Setting only `cacheDir` (as this file originally did)
    // relocates the content-layer cache but leaves Vite's
    // `node_modules/.vite` cache shared, so the race persisted; `vite:
    // { cacheDir }` is required too (Astro merges `settings.config.vite`
    // into Vite's own config, overriding the hardcoded default — see
    // astro/dist/core/create-vite.js). Point both at this fixture's own tmp
    // root so each build gets fully isolated caches.
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
    const unsafe = {
      url: 'https://example.test/?q="><svg>&x=1',
      title: 'Title <em>markup</em> & "quoted"',
      author: 'Author <script>alert("author")</script> &',
      summary: "Summary <b>markup</b> &",
      takeaway: "Takeaway <i>markup</i> &",
      openQuestion: "Question <iframe>markup</iframe> &",
    };
    await writeFile(
      join(issueDirectory, "2026-05-02.md"),
      issueMarkdown({
        date: "2026-05-02",
        theme: 'Newest <script>alert("theme")</script> &',
        featured: [
          featuredItem(unsafe),
          featuredItem({
            url: "https://example.test/optional",
            title: "Optional fields",
            summary: "Optional summary",
            score: 5,
          }),
        ].join("\n"),
      }),
    );
    await writeFile(
      join(issueDirectory, "2026-05-01.md"),
      issueMarkdown({ date: "2026-05-01", featured: "[]" }),
    );

    execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
      cwd: fixtureSite,
      env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test" },
      stdio: "pipe",
    });

    const feed = await readFile(join(fixtureSite, "dist", "agents", "rss.xml"), "utf8");
    const newestIndex = feed.indexOf("2026-05-02");
    const olderIndex = feed.indexOf("2026-05-01");
    assert.notEqual(newestIndex, -1);
    assert.notEqual(olderIndex, -1);
    assert.ok(newestIndex < olderIndex, "newest issue should appear first");
    assert.ok(feed.includes("https://example.test/newsletter/agents/issues/2026-05-02/"));
    assert.ok(feed.includes("https://example.test/newsletter/agents/issues/2026-05-01/"));
    assert.ok(feed.indexOf("Title &amp;lt;em") < feed.indexOf("Optional fields"));
    assert.ok(feed.includes("Issue 2026-05-01"), "theme-less issue should use its fallback title");
    assert.ok(
      feed.includes("Newest &lt;script&gt;alert(&quot;theme&quot;)&lt;/script&gt; &amp;"),
    );
    assert.ok(
      feed.includes(
        "Title &amp;lt;em&amp;gt;markup&amp;lt;/em&amp;gt; &amp;amp; &amp;quot;quoted&amp;quot;",
      ),
    );
    assert.ok(feed.includes("&amp;quot;&amp;gt;&amp;lt;svg&amp;gt;&amp;amp;x=1"));
    assert.ok(
      feed.includes(
        "Author &amp;lt;script&amp;gt;alert(&amp;quot;author&amp;quot;)&amp;lt;/script&amp;gt; &amp;amp;",
      ),
    );
    assert.ok(feed.includes("Summary &amp;lt;b&amp;gt;markup&amp;lt;/b&amp;gt; &amp;amp;"));
    assert.ok(
      feed.includes(
        "Takeaway:&lt;/strong&gt; Takeaway &amp;lt;i&amp;gt;markup&amp;lt;/i&amp;gt; &amp;amp;",
      ),
    );
    assert.ok(
      feed.includes(
        "Open question:&lt;/strong&gt; Question &amp;lt;iframe&amp;gt;markup&amp;lt;/iframe&amp;gt; &amp;amp;",
      ),
    );
    assert.ok(feed.includes("&lt;h2&gt;Featured&lt;/h2&gt;"));
    const optionalItemStart = feed.indexOf("Optional fields");
    const optionalItemEnd = feed.indexOf("&lt;h3&gt;", optionalItemStart + 1);
    const optionalItem = feed.slice(
      optionalItemStart,
      optionalItemEnd === -1
        ? feed.indexOf("</content:encoded>", optionalItemStart)
        : optionalItemEnd,
    );
    assert.ok(optionalItem.includes("Optional summary"));
    assert.ok(!optionalItem.includes("Takeaway:"));
    assert.ok(!optionalItem.includes("Open question:"));
    const emptyIssue = feed.slice(olderIndex, feed.indexOf("</item>", olderIndex));
    assert.ok(emptyIssue.includes("<content:encoded/>"), "empty featured list should render empty content");
    assert.ok(
      !feed.includes("<script>") && !feed.includes("<svg>") && !feed.includes("<iframe>"),
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

// Covers two branches of src/pages/rss.xml.js that the test above doesn't
// exercise because its fixtures happen to avoid them:
//
// 1. `renderItemContent`'s `[...issue.data.featured].sort((a, b) => b.score
//    - a.score)` — the one existing test's two featured items are already
//    authored in descending-score order, so removing the sort entirely
//    (verified locally) still passes that test. This test authors a lower-
//    score item first and a higher-score item second, so only a real sort
//    produces the expected order.
// 2. The `<title>` field's `issue.data.theme.split(/[.!?]/)[0]` sentence
//    truncation — the existing test's theme contains no `.`/`!`/`?`, so the
//    split is a no-op there (and `<title>` isn't even asserted on). This
//    test gives the theme a sentence-ending punctuation mark followed by
//    more text, and asserts the item's `<title>` stops at the first one.
test("RSS orders a single issue's featured items by score (not authored order) and truncates the item title at the theme's first sentence boundary", async () => {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-rss-order-"));
  const fixtureSite = join(fixtureRoot, "site");

  try {
    await mkdir(fixtureSite, { recursive: true });
    await symlink(join(siteRoot, "node_modules"), join(fixtureSite, "node_modules"), "dir");
    await writeFile(
      join(fixtureSite, "package.json"),
      await readFile(join(siteRoot, "package.json")),
    );
    // See the test above: isolated Astro/Vite caches per fixture to avoid
    // the concurrent-build cache race (PR #238/#239).
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

    // Authored LOW score first, HIGH score second — the opposite of the
    // order a correct `b.score - a.score` sort must produce.
    await writeFile(
      join(issueDirectory, "2026-06-10.md"),
      issueMarkdown({
        date: "2026-06-10",
        // Sentence boundary ('.') followed by more text the title must NOT
        // include.
        theme: "First sentence stays. Second sentence must be dropped.",
        featured: [
          featuredItem({
            url: "https://example.test/low",
            title: "Low Score Item",
            summary: "Low score summary",
            score: 1,
          }),
          featuredItem({
            url: "https://example.test/high",
            title: "High Score Item",
            summary: "High score summary",
            score: 9,
          }),
        ].join("\n"),
      }),
    );

    execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
      cwd: fixtureSite,
      env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test" },
      stdio: "pipe",
    });

    const feed = await readFile(join(fixtureSite, "dist", "agents", "rss.xml"), "utf8");

    // Title truncates at the first sentence boundary: the date-prefixed
    // <title> must contain "First sentence stays" but not the second
    // sentence's text. (The full theme legitimately still appears
    // elsewhere — e.g. <description> and the <content:encoded> theme
    // paragraph render the untruncated theme by design — so the
    // "not dropped" half of this assertion is scoped to the <title>
    // element specifically, not the whole feed.)
    const itemTitleStart = feed.indexOf("<title>2026-06-10");
    assert.notEqual(itemTitleStart, -1, "the item's <title> should start with the issue date");
    const itemTitleEnd = feed.indexOf("</title>", itemTitleStart);
    const itemTitle = feed.slice(itemTitleStart, itemTitleEnd);
    assert.equal(
      itemTitle,
      "<title>2026-06-10 — First sentence stays",
      "issue <title> should be the date plus only the theme's first sentence",
    );
    assert.ok(
      !itemTitle.includes("Second sentence must be dropped"),
      "text after the theme's first sentence boundary must not appear in the item's <title>",
    );

    // Score ordering: "High Score Item" (score 9) must render before "Low
    // Score Item" (score 1) in this issue's <content:encoded>, despite being
    // authored second in the source file.
    const highIndex = feed.indexOf("High Score Item");
    const lowIndex = feed.indexOf("Low Score Item");
    assert.notEqual(highIndex, -1);
    assert.notEqual(lowIndex, -1);
    assert.ok(
      highIndex < lowIndex,
      "the higher-scoring featured item must render before the lower-scoring one, regardless of authored order",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
