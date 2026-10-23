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

// Covers content.config.ts's httpUrl schema refinement end to end via a
// real Astro build (content-collection schema validation can't be unit
// tested in isolation from the Astro content loader). See issue #248:
// item URLs flow from scraped/fed content into rendered <a href> on the
// public site and in the RSS feed; zod 3's bare `.url()` happily accepts
// javascript:/data:/file: schemes, so the schema must additionally check
// for an http(s) prefix. This is the content-collection line of defense;
// scripts/fetch.py's upsert_items() rejects the same schemes at ingest,
// and safeHref() (safe-href.test.ts) guards the render sinks as a last
// resort.

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function quoted(value: string) {
  return JSON.stringify(value);
}

async function setupFixture(fixtureRoot: string, featuredUrl: string) {
  const fixtureSite = join(fixtureRoot, "site");
  await mkdir(fixtureSite, { recursive: true });
  await symlink(join(siteRoot, "node_modules"), join(fixtureSite, "node_modules"), "dir");
  await writeFile(
    join(fixtureSite, "package.json"),
    await readFile(join(siteRoot, "package.json")),
  );
  // See issue-page.test.ts for why both cacheDir and vite.cacheDir must be
  // relocated: Astro's content-layer cache and Vite's dependency-
  // optimization cache both default into the shared node_modules symlink
  // target, and concurrent `astro build` runs in sibling test files race
  // on it otherwise.
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

  const issueDirectory = join(fixtureSite, "src", "content", "issues", "agents");
  await mkdir(issueDirectory, { recursive: true });
  await writeFile(
    join(issueDirectory, "2026-06-01.md"),
    [
      "---",
      'date: "2026-06-01"',
      "featured:",
      "  - id: scheme-item",
      "    section: news",
      '    source: "Example"',
      `    url: ${quoted(featuredUrl)}`,
      `    title: ${quoted("Item")}`,
      "    score: 7",
      "    tags: []",
      `    summary: ${quoted("Summary text")}`,
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
    ].join("\n"),
  );
  return fixtureSite;
}

function buildFixture(fixtureSite: string) {
  return execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
    cwd: fixtureSite,
    env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test" },
    stdio: "pipe",
  });
}

test("content schema rejects a javascript: URL in a featured item", async () => {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-url-scheme-"));
  try {
    const fixtureSite = await setupFixture(fixtureRoot, "javascript:alert(1)");
    assert.throws(
      () => buildFixture(fixtureSite),
      (err: any) => {
        const output = `${err.stdout ?? ""}${err.stderr ?? ""}`;
        assert.match(output, /url must use the http or https scheme/);
        return true;
      },
      "astro build should fail content-schema validation for a javascript: URL",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("content schema rejects a data: URL in a featured item", async () => {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-url-scheme-"));
  try {
    const fixtureSite = await setupFixture(fixtureRoot, "data:text/html,<script>alert(1)</script>");
    assert.throws(
      () => buildFixture(fixtureSite),
      (err: any) => {
        const output = `${err.stdout ?? ""}${err.stderr ?? ""}`;
        assert.match(output, /url must use the http or https scheme/);
        return true;
      },
      "astro build should fail content-schema validation for a data: URL",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("content schema accepts a plain https: URL in a featured item", async () => {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-url-scheme-"));
  try {
    const fixtureSite = await setupFixture(fixtureRoot, "https://example.test/item");
    buildFixture(fixtureSite); // must not throw
    const html = await readFile(
      join(fixtureSite, "dist", "issues", "2026-06-01", "index.html"),
      "utf8",
    );
    assert.ok(html.includes('href="https://example.test/item"'));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
