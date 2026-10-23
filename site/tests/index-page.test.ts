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

// Covers src/pages/index.astro end to end via a real Astro build.
// index.astro has its own branch — `latest === undefined ? <empty-state> :
// <MonthView ...>` — that no other test exercises: every other site test
// either builds a *different* page (search, a /months/<key>/ page, an
// /issues/<date>/ page, rss.xml) or never reads dist/index.html at all.
// MonthView itself is covered against a /months/ page in month-view.test.ts,
// but never against the home page, so the "latest bucket renders at the
// root, marked as the current month" wiring in index.astro is also new
// coverage here.

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function quoted(value: string) {
  return JSON.stringify(value);
}

function issueMarkdown({ date, theme }: { date: string; theme: string }) {
  return [
    "---",
    `date: ${quoted(date)}`,
    `theme: ${quoted(theme)}`,
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

async function buildFixtureSite() {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-index-"));
  const fixtureSite = join(fixtureRoot, "site");

  await mkdir(fixtureSite, { recursive: true });
  await symlink(join(siteRoot, "node_modules"), join(fixtureSite, "node_modules"), "dir");
  await writeFile(
    join(fixtureSite, "package.json"),
    await readFile(join(siteRoot, "package.json")),
  );
  // See issue-page.test.ts / month-view.test.ts: each fixture needs its own
  // Astro content-layer cache dir AND its own Vite dependency cache dir so
  // concurrent `astro build` runs in this suite (and sibling suites) don't
  // race on a shared cache (see PR #238's thread for the two-cache
  // explanation and reproduction).
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

test("home page renders the empty state when the issues collection has no entries", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite();

  try {
    // Content-layer glob loader needs the directory to exist, but the
    // empty-state branch requires zero issue files in it.
    await mkdir(join(fixtureSite, "src", "content", "issues", "agents"), { recursive: true });

    runBuild(fixtureSite);

    const html = await readFile(join(fixtureSite, "dist", "agents", "index.html"), "utf8");

    assert.ok(
      html.includes('<p class="empty-state">No issues yet. The first run lands tomorrow.</p>'),
      "home page should render the empty-state message when there are no issues",
    );
    // The MonthView branch must not render anything in this case.
    assert.ok(!html.includes("month-view"), "MonthView must not render when there are no issues");

    // index.astro is BaseLayout's only caller that omits the `description`
    // prop (search.astro and issues/[...slug].astro both pass one
    // explicitly — see base-layout.test.ts and issue-page.test.ts), so this
    // is the only place BaseLayout.astro's own default value for
    // `description` ever actually renders. No existing test asserted on it:
    // replacing the default string entirely still left every other site
    // test green.
    assert.ok(
      html.includes(
        '<meta name="description" content="A daily, opinionated digest on building and running AI agents.">',
      ),
      "BaseLayout's default description should render in the meta tag when no page passes its own",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("home page renders the latest month's MonthView, marked as the current month, when issues exist", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite();

  try {
    const issueDirectory = join(fixtureSite, "src", "content", "issues", "agents");
    await mkdir(issueDirectory, { recursive: true });

    // Two issues in different months: groupByMonth's newest bucket (July)
    // must be the one rendered at the root, with June available only via
    // the archive nav (not rendered on this page's main timeline).
    await writeFile(
      join(issueDirectory, "2026-06-05.md"),
      issueMarkdown({ date: "2026-06-05", theme: "June theme" }),
    );
    await writeFile(
      join(issueDirectory, "2026-07-20.md"),
      issueMarkdown({ date: "2026-07-20", theme: "July theme" }),
    );

    runBuild(fixtureSite);

    const html = await readFile(join(fixtureSite, "dist", "agents", "index.html"), "utf8");

    assert.ok(
      !html.includes("empty-state"),
      "home page must not render the empty state when issues exist",
    );

    // The latest issue (July) is the one rendered in the home page's
    // timeline; the older (June) issue is reachable only via the nav.
    assert.ok(html.includes("July theme"), "home page should render the latest issue's theme");
    assert.ok(
      !html.includes("June theme"),
      "home page's timeline should only include the latest month's issues",
    );

    // index.astro passes `latest.key` as currentMonth to MonthView, so the
    // archive nav must mark July (the latest month) as current — on the
    // home page itself, not just on a dedicated /months/<key>/ page (that
    // case is already covered by month-view.test.ts).
    assert.ok(
      html.includes("month-nav__item--current"),
      "the archive nav on the home page should highlight the latest month as current",
    );
    assert.ok(
      html.includes('aria-current="page"'),
      "the archive nav on the home page should mark the latest month with aria-current",
    );

    // The latest month's nav entry links to the site root (per
    // buildMonthList), not to a /months/2026-07/ page.
    assert.ok(
      html.includes('href="/newsletter/agents/"') ,
      "the latest month's nav link should point at the edition front page",
    );
    // The older month's nav entry should link to its dedicated page.
    assert.ok(
      html.includes('href="/newsletter/agents/months/2026-06/"'),
      "an older month's nav link should point at its dedicated /months/ page",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
