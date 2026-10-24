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

// Covers src/components/Callout.astro end to end via a real Astro build.
//
// Every existing test that touches `takeaway`/`open_question` only asserts
// on the *sanitized inner content* of these fields (issue-page.test.ts,
// XSS/DOMPurify coverage) — none of them assert on Callout.astro's own
// markup: which `callout--takeaway` / `callout--question` class is applied,
// what the visible `callout__label` text is ("Takeaway" vs. "Open
// question"), or that the whole `<aside class="callout ...">` wrapper is
// omitted entirely when the corresponding field is absent (the
// `{item.takeaway && <Callout ...>}` / `{item.open_question && <Callout
// ...>}` guards in FeaturedItem.astro). This file closes that gap.

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
    "  items_featured_total: 1",
    "  items_featured_papers: 0",
    "  items_featured_news: 1",
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
  await writeFile(
    join(fixtureSite, "package.json"),
    await readFile(join(siteRoot, "package.json")),
  );
  // See issue-page.test.ts / issue-page-sections.test.ts: each fixture
  // needs its own Astro content-layer cache dir AND its own Vite dependency
  // cache dir so concurrent `astro build` runs in this suite (and sibling
  // suites) don't race on a shared cache (see PR #238's thread for the
  // two-cache explanation and reproduction).
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

  return { fixtureRoot, fixtureSite };
}

function runBuild(fixtureSite: string) {
  execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
    cwd: fixtureSite,
    env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test" },
    stdio: "pipe",
  });
}

test("Callout renders the takeaway variant's class and label, and the question variant's, on the same item", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite("newsletter-callout-");

  try {
    const issueDirectory = join(fixtureSite, "src", "content", "issues", "agents");
    await mkdir(issueDirectory, { recursive: true });

    const featured = [
      "  - id: both-callouts",
      "    section: news",
      '    source: "Example"',
      `    url: ${quoted("https://example.test/item")}`,
      `    title: ${quoted("Item With Both Callouts")}`,
      "    score: 5",
      `    summary: ${quoted("Summary text.")}`,
      `    takeaway: ${quoted("The key insight.")}`,
      `    open_question: ${quoted("What happens at scale?")}`,
    ].join("\n");

    await writeFile(
      join(issueDirectory, "2026-06-01.md"),
      issueMarkdown({ date: "2026-06-01", featured }),
    );

    runBuild(fixtureSite);

    const html = await readFile(
      join(fixtureSite, "dist", "agents", "issues", "2026-06-01", "index.html"),
      "utf8",
    );

    // Both callouts render with their own class.
    assert.ok(
      html.includes('<aside class="callout callout--takeaway">'),
      "takeaway should render inside a callout--takeaway aside",
    );
    assert.ok(
      html.includes('<aside class="callout callout--question">'),
      "open_question should render inside a callout--question aside",
    );

    // Scope the label and body assertions to *within* each callout's own
    // <aside>...</aside> block (not just "appears somewhere on the page")
    // so a mutation that swaps which label goes with which type, or leaks
    // one callout's text into the other, is actually caught.
    const takeawayStart = html.indexOf('<aside class="callout callout--takeaway">');
    const takeawayEnd = html.indexOf("</aside>", takeawayStart);
    const takeawayBlock = html.slice(takeawayStart, takeawayEnd);
    assert.ok(
      takeawayBlock.includes('<span class="callout__label">Takeaway</span>'),
      "the takeaway callout's visible label should read 'Takeaway'",
    );
    assert.ok(
      takeawayBlock.includes("The key insight."),
      "the takeaway callout should contain the takeaway field's own text",
    );
    assert.ok(
      !takeawayBlock.includes("What happens at scale?"),
      "the takeaway callout should not contain the open_question field's text",
    );

    const questionStart = html.indexOf('<aside class="callout callout--question">');
    const questionEnd = html.indexOf("</aside>", questionStart);
    const questionBlock = html.slice(questionStart, questionEnd);
    assert.ok(
      questionBlock.includes('<span class="callout__label">Open question</span>'),
      "the open_question callout's visible label should read 'Open question'",
    );
    assert.ok(
      questionBlock.includes("What happens at scale?"),
      "the open_question callout should contain the open_question field's own text",
    );
    assert.ok(
      !questionBlock.includes("The key insight."),
      "the open_question callout should not contain the takeaway field's text",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Callout does not render at all for an item with neither takeaway nor open_question", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite("newsletter-callout-none-");

  try {
    const issueDirectory = join(fixtureSite, "src", "content", "issues", "agents");
    await mkdir(issueDirectory, { recursive: true });

    const featured = [
      "  - id: no-callouts",
      "    section: news",
      '    source: "Example"',
      `    url: ${quoted("https://example.test/item")}`,
      `    title: ${quoted("Item Without Callouts")}`,
      "    score: 5",
      `    summary: ${quoted("Summary text.")}`,
    ].join("\n");

    await writeFile(
      join(issueDirectory, "2026-06-01.md"),
      issueMarkdown({ date: "2026-06-01", featured }),
    );

    runBuild(fixtureSite);

    const html = await readFile(
      join(fixtureSite, "dist", "agents", "issues", "2026-06-01", "index.html"),
      "utf8",
    );

    // Neither field is present on the item, so FeaturedItem's
    // `{item.takeaway && <Callout .../>}` / `{item.open_question && <Callout
    // .../>}` guards must both suppress rendering — no callout markup of
    // any kind should appear on the page.
    assert.ok(
      !html.includes('class="callout'),
      "no callout should render when the item has neither takeaway nor open_question",
    );
    assert.ok(!html.includes("callout__label"));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
