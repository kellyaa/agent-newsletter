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

    const issueDirectory = join(fixtureSite, "src", "content", "issues");
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

    const feed = await readFile(join(fixtureSite, "dist", "rss.xml"), "utf8");
    const newestIndex = feed.indexOf("2026-05-02");
    const olderIndex = feed.indexOf("2026-05-01");
    assert.notEqual(newestIndex, -1);
    assert.notEqual(olderIndex, -1);
    assert.ok(newestIndex < olderIndex, "newest issue should appear first");
    assert.ok(feed.includes("https://example.test/newsletter/issues/2026-05-02/"));
    assert.ok(feed.includes("https://example.test/newsletter/issues/2026-05-01/"));
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
