import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cp, mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { seedEditions } from "./seed-editions.ts";

// Edition navigation (#421): every edition page links back to the umbrella
// home, and every issue page carries a no-JS archive sidebar (months as
// <details> groups, days as links) built from the edition's own issues.

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function issueMarkdown(date: string) {
  return [
    "---",
    `date: "${date}"`,
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

test("edition pages link home and issue pages render the archive sidebar", async () => {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-archive-nav-"));
  const fixtureSite = join(fixtureRoot, "site");

  try {
    await mkdir(fixtureSite, { recursive: true });
    await symlink(join(siteRoot, "node_modules"), join(fixtureSite, "node_modules"), "dir");
    await writeFile(join(fixtureSite, "package.json"), await readFile(join(siteRoot, "package.json")));
    // Isolated Astro + Vite caches per fixture; see base-layout.test.ts.
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
    await seedEditions(fixtureRoot);

    const dir = join(fixtureSite, "src", "content", "issues", "agents");
    await mkdir(dir, { recursive: true });
    for (const date of ["2026-01-15", "2026-02-03", "2026-02-25"]) {
      await writeFile(join(dir, `${date}.md`), issueMarkdown(date));
    }

    execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
      cwd: fixtureSite,
      env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test" },
      stdio: "pipe",
    });

    const read = (...p: string[]) => readFile(join(fixtureSite, "dist", ...p), "utf8");

    // Home link on edition pages (front page and issue page), not on the umbrella page.
    const homeLink = '<a class="site-header__home" href="/newsletter">';
    assert.ok((await read("agents", "index.html")).includes(homeLink));
    const issue = await read("agents", "issues", "2026-01-15", "index.html");
    assert.ok(issue.includes(homeLink));
    assert.ok(!(await read("index.html")).includes("site-header__home"));

    // Sidebar: both months, every day linked, no script needed.
    assert.ok(issue.includes('aria-label="Issue archive"'));
    for (const date of ["2026-01-15", "2026-02-03", "2026-02-25"]) {
      assert.ok(issue.includes(`href="/newsletter/agents/issues/${date}/"`), `link to ${date}`);
    }
    // Current page highlighted; its month open, the other closed.
    assert.equal(issue.match(/aria-current="page"/g)?.length, 1);
    assert.ok(
      /<a href="\/newsletter\/agents\/issues\/2026-01-15\/"[^>]*aria-current="page"|<a[^>]*aria-current="page"[^>]*href="\/newsletter\/agents\/issues\/2026-01-15\/"/.test(issue),
    );
    const details = issue.match(/<details[^>]*>/g) ?? [];
    assert.equal(details.length, 2);
    assert.equal(details.filter((d) => /\bopen\b/.test(d)).length, 1);
    assert.ok(issue.indexOf("Feb 2026") < issue.indexOf("Jan 2026"), "newest month first");
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
