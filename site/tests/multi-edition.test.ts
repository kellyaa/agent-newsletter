import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { cp, mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { editionYaml, seedEditions } from "./seed-editions.ts";

// Multi-edition site (#291 §8, #302): homepage cards, per-edition routes, per-
// edition RSS, the /rss.xml alias, the Pagefind filter attribute, draft
// filtering and rejection of content no edition owns. Real Astro builds.

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function issueMarkdown(date: string, theme?: string) {
  return [
    "---",
    `date: "${date}"`,
    ...(theme === undefined ? [] : [`theme: ${JSON.stringify(theme)}`]),
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

async function fixture(
  editions: Record<string, string>,
  issues: Record<string, Record<string, string>>,
) {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-multi-"));
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
  await seedEditions(fixtureRoot, editions);
  await mkdir(join(fixtureSite, "src", "content", "issues"), { recursive: true });
  for (const [edition, files] of Object.entries(issues)) {
    const dir = join(fixtureSite, "src", "content", "issues", edition);
    await mkdir(dir, { recursive: true });
    for (const [name, body] of Object.entries(files)) await writeFile(join(dir, name), body);
  }
  return { fixtureRoot, fixtureSite };
}

function build(fixtureSite: string, env: Record<string, string> = {}) {
  execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
    cwd: fixtureSite,
    env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test", ...env },
    stdio: "pipe",
  });
}

const read = (site: string, ...p: string[]) => readFile(join(site, "dist", ...p), "utf8");

const EDITIONS = {
  agents: editionYaml({ slug: "agents", name: "AI Agents Daily", tagline: "Agents tagline.", order: 10 }),
  sdlc: editionYaml({ slug: "sdlc", name: "Breaking Changes", tagline: "SDLC tagline.", order: 20 }),
  paused: editionYaml({ slug: "paused", name: "Paused One", tagline: "Quiet.", status: "paused", order: 30 }),
  fresh: editionYaml({ slug: "fresh", name: "Fresh One", tagline: "New.", order: 40 }),
  wip: editionYaml({ slug: "wip", name: "Draft One", tagline: "Not yet.", status: "draft", order: 50 }),
};

const ISSUES = {
  agents: {
    "2026-06-01.md": issueMarkdown("2026-06-01", "Older agents theme."),
    "2026-06-02.md": issueMarkdown("2026-06-02", "Newest agents theme."),
  },
  sdlc: { "2026-06-02.md": issueMarkdown("2026-06-02", "SDLC only theme.") },
  paused: { "2026-05-01.md": issueMarkdown("2026-05-01", "Paused theme.") },
  wip: { "2026-06-02.md": issueMarkdown("2026-06-02", "Draft-only theme.") },
};

test("homepage lists one card per listed edition with latest issue, theme, count, links and states", async () => {
  const { fixtureRoot, fixtureSite } = await fixture(EDITIONS, ISSUES);
  try {
    build(fixtureSite);
    const home = await read(fixtureSite, "index.html");

    assert.ok(home.includes("<title>The Daily Inference</title>"));
    assert.ok(home.includes("data-pagefind-ignore"), "homepage is excluded from the search index");
    assert.ok(!home.includes("site-header__search"), "no edition-scoped search link on the umbrella page");

    const order = ["agents", "sdlc", "paused", "fresh"].map((s) => home.indexOf(`data-edition="${s}"`));
    assert.ok(order.every((i) => i >= 0));
    assert.deepEqual([...order].sort((a, b) => a - b), order, "cards follow `order`");
    assert.ok(!home.includes('data-edition="wip"') && !home.includes("Draft One"), "drafts are hidden");

    const card = (slug: string, next: string | null) => {
      const start = home.indexOf(`data-edition="${slug}"`);
      const end = next ? home.indexOf(`data-edition="${next}"`) : home.indexOf("</ul>", start);
      return home.slice(start, end);
    };
    const agents = card("agents", "sdlc");
    assert.ok(agents.includes("AI Agents Daily") && agents.includes("Agents tagline."));
    assert.ok(agents.includes('href="/newsletter/agents/issues/2026-06-02/"'), "latest issue linked");
    assert.ok(agents.includes("Newest agents theme.") && !agents.includes("Older agents theme."));
    assert.ok(agents.includes("2 issues"));
    assert.ok(agents.includes('href="/newsletter/agents/rss.xml"'));
    assert.ok(agents.includes('href="/newsletter/agents/"'), "archive link");
    assert.ok(!agents.includes("paused") && !agents.includes("First issue coming soon"));

    const sdlc = card("sdlc", "paused");
    assert.ok(sdlc.includes("1 issue<") && sdlc.includes("SDLC only theme."));

    const paused = card("paused", "fresh");
    assert.match(paused, /class="edition-card__status"[^>]*>paused</);
    assert.ok(paused.includes("Paused theme."), "paused editions still serve their archive");

    const fresh = card("fresh", null);
    assert.ok(fresh.includes("First issue coming soon"));
    assert.ok(fresh.includes("0 issues"));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("routes are generated per listed edition and nothing is left at the legacy paths", async () => {
  const { fixtureRoot, fixtureSite } = await fixture(EDITIONS, ISSUES);
  try {
    build(fixtureSite);
    const dist = join(fixtureSite, "dist");
    for (const p of [
      "agents/index.html",
      "agents/issues/2026-06-01/index.html",
      "agents/issues/2026-06-02/index.html",
      "agents/rss.xml",
      "agents/search/index.html",
      "sdlc/index.html",
      "sdlc/issues/2026-06-02/index.html",
      "sdlc/rss.xml",
      "sdlc/search/index.html",
      "paused/issues/2026-05-01/index.html",
      "fresh/index.html",
      "fresh/rss.xml",
    ]) {
      assert.ok(existsSync(join(dist, p)), p);
    }
    for (const p of ["issues", "months", "search", "tags", "wip"]) {
      assert.ok(!existsSync(join(dist, p)), `${p}/ must not exist at the site root`);
    }
    // sdlc's issue is its own, not agents'
    const sdlcIssue = await read(fixtureSite, "sdlc", "issues", "2026-06-02", "index.html");
    assert.ok(sdlcIssue.includes("SDLC only theme.") && !sdlcIssue.includes("Newest agents theme."));
    assert.ok(sdlcIssue.includes("<title>2026-06-02 — Breaking Changes</title>"));
    assert.ok(sdlcIssue.includes('href="/newsletter/sdlc/rss.xml"'));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("each edition RSS contains only its own issues, and /rss.xml is a copy of /agents/rss.xml", async () => {
  const { fixtureRoot, fixtureSite } = await fixture(EDITIONS, ISSUES);
  try {
    build(fixtureSite);
    const agents = await read(fixtureSite, "agents", "rss.xml");
    const sdlc = await read(fixtureSite, "sdlc", "rss.xml");

    assert.ok(agents.includes("<title>AI Agents Daily</title>"));
    assert.ok(agents.includes("https://example.test/newsletter/agents/issues/2026-06-02/"));
    assert.ok(agents.includes("https://example.test/newsletter/agents/issues/2026-06-01/"));
    assert.ok(!agents.includes("SDLC only theme.") && !agents.includes("/sdlc/"));

    assert.ok(sdlc.includes("<title>Breaking Changes</title>"));
    assert.ok(sdlc.includes("https://example.test/newsletter/sdlc/issues/2026-06-02/"));
    assert.ok(!sdlc.includes("agents theme") && !sdlc.includes("/agents/"));

    assert.equal(await read(fixtureSite, "rss.xml"), agents, "/rss.xml aliases /agents/rss.xml");
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("issue pages carry the Pagefind edition filter; the edition search page pre-applies it", async () => {
  const { fixtureRoot, fixtureSite } = await fixture(EDITIONS, ISSUES);
  try {
    build(fixtureSite);
    const issue = await read(fixtureSite, "sdlc", "issues", "2026-06-02", "index.html");
    assert.ok(issue.includes('data-pagefind-filter="edition:sdlc"'));
    const search = await read(fixtureSite, "sdlc", "search", "index.html");
    assert.ok(search.includes('edition = "sdlc"'), "loader receives the edition");
    assert.ok(search.includes("triggerFilters({ edition: [edition] })"));
    assert.ok(search.includes("<title>Search — Breaking Changes</title>"));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("SHOW_DRAFTS=1 builds draft editions and lists them as drafts", async () => {
  const { fixtureRoot, fixtureSite } = await fixture(EDITIONS, ISSUES);
  try {
    build(fixtureSite, { SHOW_DRAFTS: "1" });
    assert.ok(existsSync(join(fixtureSite, "dist", "wip", "issues", "2026-06-02", "index.html")));
    const home = await read(fixtureSite, "index.html");
    assert.ok(home.includes('data-edition="wip"'));
    assert.match(home, /class="edition-card__status"[^>]*>draft</);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("a draft edition's staged content does not break a normal build", async () => {
  const { fixtureRoot, fixtureSite } = await fixture(EDITIONS, ISSUES);
  try {
    build(fixtureSite);
    assert.ok(!existsSync(join(fixtureSite, "dist", "wip")));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("content for an edition not in the registry, or a flat legacy issue, fails the build", async () => {
  const cases: [string, (site: string) => Promise<void>][] = [
    [
      "unknown edition folder",
      async (site) => {
        await mkdir(join(site, "src", "content", "issues", "ghost"), { recursive: true });
        await writeFile(join(site, "src", "content", "issues", "ghost", "2026-06-01.md"), issueMarkdown("2026-06-01"));
      },
    ],
    [
      "flat legacy issue",
      async (site) => {
        await writeFile(join(site, "src", "content", "issues", "2026-06-01.md"), issueMarkdown("2026-06-01"));
      },
    ],
  ];
  for (const [label, plant] of cases) {
    const { fixtureRoot, fixtureSite } = await fixture({ agents: EDITIONS.agents }, { agents: {} });
    try {
      await plant(fixtureSite);
      assert.throws(
        () => build(fixtureSite),
        (e: any) => /not an edition in editions\/\*\/edition\.yaml/.test(String(e.stderr) + String(e.stdout)),
        label,
      );
    } finally {
      await rm(fixtureRoot, { recursive: true, force: true });
    }
  }
});

test("a site with no editions builds an empty homepage", async () => {
  const { fixtureRoot, fixtureSite } = await fixture({}, {});
  try {
    await mkdir(join(fixtureRoot, "editions"), { recursive: true });
    build(fixtureSite);
    assert.ok((await read(fixtureSite, "index.html")).includes("No editions yet."));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
