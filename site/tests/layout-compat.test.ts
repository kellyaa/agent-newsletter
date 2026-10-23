import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cp, mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// Multi-edition layout (#301): issues are staged at issues/<edition>/DATE.md,
// but the flat issues/DATE.md form must keep building too (the deploy workflow
// has a one-release fallback for a content branch that is not migrated yet).
// Either way URLs come from the issue's `date`, never from the entry id, so the
// published URLs do not change.

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function issueMarkdown(date: string) {
  return [
    "---",
    `date: "${date}"`,
    'theme: "Layout compat."',
    "featured: []",
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

test("nested and flat issue layouts both build to /issues/<date>/ and link there", async () => {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-layout-"));
  const fixtureSite = join(fixtureRoot, "site");
  try {
    await mkdir(fixtureSite, { recursive: true });
    await symlink(join(siteRoot, "node_modules"), join(fixtureSite, "node_modules"), "dir");
    await writeFile(join(fixtureSite, "package.json"), await readFile(join(siteRoot, "package.json")));
    // Isolate Astro's and Vite's caches per fixture (see issue-page.test.ts).
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
    await writeFile(join(fixtureSite, "tsconfig.json"), await readFile(join(siteRoot, "tsconfig.json")));
    await cp(join(siteRoot, "src"), join(fixtureSite, "src"), { recursive: true });

    const issues = join(fixtureSite, "src", "content", "issues");
    await mkdir(join(issues, "agents"), { recursive: true });
    await writeFile(join(issues, "agents", "2026-06-02.md"), issueMarkdown("2026-06-02"));
    await writeFile(join(issues, "2026-06-01.md"), issueMarkdown("2026-06-01"));

    execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
      cwd: fixtureSite,
      env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test" },
      stdio: "pipe",
    });

    for (const date of ["2026-06-01", "2026-06-02"]) {
      const page = await readFile(join(fixtureSite, "dist", "issues", date, "index.html"), "utf8");
      assert.ok(page.includes(date), `issue page for ${date} should render`);
    }
    const feed = await readFile(join(fixtureSite, "dist", "rss.xml"), "utf8");
    assert.ok(feed.includes("https://example.test/newsletter/issues/2026-06-02/"));
    assert.ok(feed.includes("https://example.test/newsletter/issues/2026-06-01/"));
    assert.ok(!feed.includes("/issues/agents/"), "feed links must not leak the edition folder");
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
