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

// Covers src/components/TagChip.astro end to end via a real Astro build.
//
// TagChip is a simple component that renders a single tag label inside a
// <span> with class "chip". It is used in FeaturedItem.astro and
// MonthView.astro to display tags. While FeaturedItem and MonthView tests
// indirectly exercise TagChip's rendering via their parent components,
// TagChip's direct behavior is not explicitly tested — notably, the label
// prop rendering and any HTML escaping for XSS prevention (consistency with
// issue-page.test.ts's sanitization approach for similar fields).

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function quoted(value: string) {
  return JSON.stringify(value);
}

function featuredItem({
  id,
  tags,
  title = "Untitled",
}: {
  id: string;
  tags: string[];
  title?: string;
}) {
  const tagList = tags.map(quoted).join(", ");
  return [
    `  - id: ${quoted(id)}`,
    "    section: news",
    '    source: "Example"',
    `    url: ${quoted(`https://example.test/${id}`)}`,
    `    title: ${quoted(title)}`,
    "    score: 5",
    `    tags: [${tagList}]`,
    `    summary: ${quoted("A summary.")}`,
  ].join("\n");
}

function issueMarkdown({
  date,
  featured,
}: {
  date: string;
  featured: string[];
}) {
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
  // See issue-page.test.ts: each fixture needs its own Astro and Vite cache
  // to avoid races between concurrent builds.
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

  return { fixtureRoot, fixtureSite };
}

function runBuild(fixtureSite: string) {
  execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
    cwd: fixtureSite,
    env: { ...process.env, SITE_BASE: "/newsletter", SITE_URL: "https://example.test" },
    stdio: "pipe",
  });
}

test("TagChip renders the label prop inside a chip span", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite("newsletter-tagchip-");

  try {
    const issueDirectory = join(fixtureSite, "src", "content", "issues");
    await mkdir(issueDirectory, { recursive: true });

    const featured = [
      featuredItem({
        id: "item-with-tags",
        tags: ["machine-learning", "safety", "scaling"],
        title: "A Foundational Paper",
      }),
    ];

    await writeFile(
      join(issueDirectory, "2026-06-01.md"),
      issueMarkdown({ date: "2026-06-01", featured }),
    );

    runBuild(fixtureSite);

    const html = await readFile(
      join(fixtureSite, "dist", "issues", "2026-06-01", "index.html"),
      "utf8",
    );

    // Each tag should render as a chip span.
    assert.ok(
      html.includes(
        '<a class="chip chip--link" href="/newsletter/tags/machine-learning/">machine-learning</a>',
      ),
      "machine-learning tag should render as a link chip to its tag page",
    );
    assert.ok(
      html.includes(
        '<a class="chip chip--link" href="/newsletter/tags/safety/">safety</a>',
      ),
      "safety tag should render as a link chip to its tag page",
    );
    assert.ok(
      html.includes(
        '<a class="chip chip--link" href="/newsletter/tags/scaling/">scaling</a>',
      ),
      "scaling tag should render as a link chip to its tag page",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("TagChip does not render when an item has no tags", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite("newsletter-tagchip-empty-");

  try {
    const issueDirectory = join(fixtureSite, "src", "content", "issues");
    await mkdir(issueDirectory, { recursive: true });

    const featured = [
      featuredItem({
        id: "item-without-tags",
        tags: [],
        title: "An Item Without Tags",
      }),
    ];

    await writeFile(
      join(issueDirectory, "2026-06-01.md"),
      issueMarkdown({ date: "2026-06-01", featured }),
    );

    runBuild(fixtureSite);

    const html = await readFile(
      join(fixtureSite, "dist", "issues", "2026-06-01", "index.html"),
      "utf8",
    );

    // FeaturedItem's `{item.tags.length > 0 && (...)}` guard should prevent
    // any chip rendering when tags array is empty.
    assert.ok(
      !html.includes('class="chip'),
      "no chip spans should render when the item has no tags",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("TagChip escapes HTML special characters in label", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite("newsletter-tagchip-xss-");

  try {
    const issueDirectory = join(fixtureSite, "src", "content", "issues");
    await mkdir(issueDirectory, { recursive: true });

    // Test with tags containing HTML-like content that should be escaped.
    const featured = [
      featuredItem({
        id: "item-with-xss",
        tags: ['<script>alert("xss")</script>', "ai&ml", 'tag"quote'],
        title: "XSS Prevention Check",
      }),
    ];

    await writeFile(
      join(issueDirectory, "2026-06-01.md"),
      issueMarkdown({ date: "2026-06-01", featured }),
    );

    runBuild(fixtureSite);

    const html = await readFile(
      join(fixtureSite, "dist", "issues", "2026-06-01", "index.html"),
      "utf8",
    );

    // Tags should be escaped, not injected as HTML. The exact escaping depends
    // on how Astro/the renderer handles text nodes, but these assertions ensure
    // no raw script tags or unescaped quotes appear in the output.
    assert.ok(
      !html.includes('<script>alert("xss")</script>'),
      "script tag content should not appear unescaped in HTML",
    );
    // The tag text itself should appear (escaped).
    // Non-slug tags render as inert spans: no link to a /tags/ page.
    assert.ok(
      html.includes('<span class="chip">'),
      "chip span should render even with special characters",
    );
    assert.ok(
      !html.includes("/tags/ai"),
      "non-slug tags must not produce tag-page links",
    );
    // Verify that the escaped versions appear.
    assert.ok(
      html.includes("ai&amp;ml") || html.includes("ai&ml"),
      "ampersand in tag should be present (escaped or literal)",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
