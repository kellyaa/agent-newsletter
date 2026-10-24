import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cp, mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { isTagSlug } from "../src/scripts/tag-slug.ts";
import { seedEditions } from "./seed-editions.ts";

// Covers /tags/ and /tags/<tag>/ (issue #7) via a real Astro build.

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function quoted(value: string) {
  return JSON.stringify(value);
}

function item(id: string, tags: string[], score: number, title: string, summary = "A summary.") {
  return [
    `  - id: ${quoted(id)}`,
    "    section: news",
    '    source: "Example"',
    `    url: ${quoted(`https://example.test/${id}`)}`,
    `    title: ${quoted(title)}`,
    `    score: ${score}`,
    `    tags: [${tags.map(quoted).join(", ")}]`,
    `    summary: ${quoted(summary)}`,
  ].join("\n");
}

function issue(date: string, featured: string[]) {
  return [
    "---",
    `date: ${quoted(date)}`,
    "featured:",
    ...featured,
    "appendix:",
    "  papers: []",
    "  news: []",
    "  blogs: []",
    "metadata:",
    "  items_considered: 0",
    "  items_featured_total: 0",
    "  items_featured_papers: 0",
    "  items_featured_news: 2",
    "  items_featured_blogs: 0",
    "  items_appendix: 0",
    "---",
    "",
  ].join("\n");
}

async function buildFixtureSite(prefix: string) {
  const fixtureRoot = await mkdtemp(join(tmpdir(), prefix));
  const fixtureSite = join(fixtureRoot, "site");
  await mkdir(fixtureSite, { recursive: true });
  await symlink(join(siteRoot, "node_modules"), join(fixtureSite, "node_modules"), "dir");
  await writeFile(join(fixtureSite, "package.json"), await readFile(join(siteRoot, "package.json")));
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
  return { fixtureRoot, fixtureSite };
}

function runBuild(fixtureSite: string) {
  execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
    cwd: fixtureSite,
    env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test" },
    stdio: "pipe",
  });
}

test("isTagSlug accepts vocabulary tags and rejects unsafe ones", () => {
  for (const ok of ["evals", "tool-use", "cost-latency", "multi-agent"]) {
    assert.equal(isTagSlug(ok), true, ok);
  }
  for (const bad of ["", "a/b", "A", "x y", "<b>", "-lead", "../x", "a?b"]) {
    assert.equal(isTagSlug(bad), false, bad);
  }
});

test("tag pages: index counts, per-tag listing order, chip links", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite("newsletter-tagpages-");
  try {
    const dir = join(fixtureSite, "src", "content", "issues", "agents");
    await mkdir(dir, { recursive: true });
    await writeFile(
      join(dir, "2026-06-01.md"),
      issue("2026-06-01", [
        item("a1", ["evals", "tool-use"], 5, "Older eval"),
        item("a2", ["memory", "bad/tag"], 9, "Memory item"),
      ]),
    );
    await writeFile(
      join(dir, "2026-06-02.md"),
      issue("2026-06-02", [
        item("b1", ["evals"], 3, "Newer low eval"),
        item("b2", ["evals"], 8, "Newer high eval", "**Bold** [link](https://x.test) text."),
      ]),
    );

    runBuild(fixtureSite);
    const dist = join(fixtureSite, "dist");

    const index = await readFile(join(dist, "agents", "tags", "index.html"), "utf8");
    assert.match(index, /href="\/newsletter\/agents\/tags\/evals\/"[^>]*>\s*<span class="tag-index__tag"[^>]*>evals<\/span>\s*<span class="tag-index__count"[^>]*>3<\/span>/);
    assert.ok(index.includes("/newsletter/agents/tags/tool-use/"));
    assert.ok(index.includes("/newsletter/agents/tags/memory/"));
    assert.ok(!index.includes("bad/tag") && !index.includes("bad%2Ftag"));

    const evals = await readFile(join(dist, "agents", "tags", "evals", "index.html"), "utf8");
    const order = ["Newer high eval", "Newer low eval", "Older eval"].map((t) => evals.indexOf(t));
    assert.ok(order.every((i) => i >= 0), "all evals items listed");
    assert.deepEqual([...order].sort((a, b) => a - b), order, "date desc, then score desc");
    assert.ok(!evals.includes("Memory item"), "other tags excluded");
    assert.ok(evals.includes('href="/newsletter/agents/issues/2026-06-02/"'));
    assert.ok(evals.includes("Bold link text."), "summary rendered as plain excerpt");
    assert.ok(!evals.includes("**Bold**"));

    assert.ok(existsSync(join(dist, "agents", "tags", "tool-use", "index.html")));
    assert.ok(existsSync(join(dist, "agents", "tags", "memory", "index.html")));

    const issuePage = await readFile(join(dist, "agents", "issues", "2026-06-01", "index.html"), "utf8");
    assert.ok(issuePage.includes('<a class="chip chip--link" href="/newsletter/agents/tags/tool-use/">tool-use</a>'));
    assert.ok(issuePage.includes('<span class="chip">bad/tag</span>'));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
