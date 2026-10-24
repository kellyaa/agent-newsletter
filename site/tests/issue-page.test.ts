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

// Covers scripts/components/FeaturedItem.astro's safeInline() sanitization
// pipeline (marked -> DOMPurify) end to end via a real Astro build, since
// the function isn't exported for unit testing in isolation. See sec-check
// issue #162: LLM-generated / prompt-injected content in summary, takeaway,
// and open_question fields must not reach the rendered page as live markup.

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function quoted(value: string) {
  return JSON.stringify(value);
}

function issueMarkdown({ date, featured }: { date: string; featured: string }) {
  return [
    "---",
    `date: ${quoted(date)}`,
    "featured:",
    featured,
    "appendix:",
    "  papers: []",
    "  news: []",
    "  blogs: []",
    "metadata:",
    "  items_considered: 0",
    "  items_featured_total: 0",
    "  items_featured_papers: 0",
    "  items_featured_news: 1",
    "  items_featured_blogs: 0",
    "  items_appendix: 0",
    "---",
    "",
  ].join("\n");
}

test("issue page sanitizes markdown fields and escapes tags, allowing only safe inline markup", async () => {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-issue-"));
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
    // fixture — including the ones the sibling rss.test.ts and
    // month-view.test.ts builds run concurrently. Two `astro build` runs
    // racing on either shared cache corrupts whichever build loses the
    // race, producing a nondeterministic ENOENT reading the built HTML
    // (first surfaced once CI actually ran this suite in parallel, see PR
    // adding site-tests.yml). Setting only `cacheDir` (as this file
    // originally did) relocates the content-layer cache but leaves Vite's
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

    const featured = [
      "  - id: xss-item",
      "    section: news",
      '    source: "Example"',
      `    url: ${quoted("https://example.test/item")}`,
      `    title: ${quoted("Allowed inline **bold** stays literal here")}`,
      "    score: 7",
      `    tags: [${quoted("<script>alert(1)</script>")}]`,
      `    summary: ${quoted(
        'Summary with **bold**, `code`, [a link](https://example.test), and <script>alert("xss")</script> plus <img src=x onerror=alert(1)>.',
      )}`,
      `    takeaway: ${quoted('Takeaway <svg onload=alert(1)></svg> with *emphasis* kept.')}`,
      `    open_question: ${quoted('Question <iframe src="javascript:alert(1)"></iframe> stays blocked.')}`,
    ].join("\n");

    await writeFile(
      join(issueDirectory, "2026-06-01.md"),
      issueMarkdown({ date: "2026-06-01", featured }),
    );

    execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
      cwd: fixtureSite,
      env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test" },
      stdio: "pipe",
    });

    const html = await readFile(
      join(fixtureSite, "dist", "agents", "issues", "2026-06-01", "index.html"),
      "utf8",
    );

    // Dangerous tags from summary/takeaway/open_question must never appear
    // as live markup — DOMPurify strips the elements entirely (not just
    // escapes them), including their text content for script/iframe.
    assert.ok(!html.includes("<script>"), "raw <script> must not survive sanitization");
    assert.ok(!html.includes("onerror="), "event handler attributes must be stripped");
    assert.ok(!html.includes("<svg"), "raw <svg> must not survive sanitization");
    assert.ok(!html.includes("<iframe"), "raw <iframe> must not survive sanitization");
    assert.ok(!html.includes("javascript:"), "javascript: URLs must not survive sanitization");

    // Markdown -> allowed inline HTML must still render for legitimate
    // content: this is the behavior safeInline is there to preserve, not
    // just a strip-everything sanitizer.
    assert.ok(html.includes("<strong>bold</strong>"), "markdown bold should render as <strong>");
    assert.ok(html.includes("<code>code</code>"), "markdown code span should render as <code>");
    assert.ok(
      html.includes('<a href="https://example.test">a link</a>'),
      "markdown link should render as a plain <a> (no target/rel added by marked)",
    );
    assert.ok(html.includes("<em>emphasis</em>"), "markdown emphasis should render as <em>");

    // The literal text that followed/preceded the stripped elements should
    // still be present — DOMPurify removes the dangerous element, not the
    // whole surrounding sentence.
    assert.ok(html.includes("with"), "sanitization should not eat trailing sibling text");
    assert.ok(
      html.includes("Question") && html.includes("stays blocked."),
      "sanitization should not eat text around a stripped <iframe>",
    );

    // Tags are plain interpolation ({label}), not set:html — Astro escapes
    // them by default, so a malicious tag value renders as inert text, not
    // a parsed element.
    assert.ok(
      html.includes("&lt;script&gt;alert(1)&lt;/script&gt;"),
      "tag chip content should be HTML-escaped, not parsed as markup",
    );
    assert.ok(
      !html.includes("<script>alert(1)</script>"),
      "tag chip must not inject an executable script tag",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
