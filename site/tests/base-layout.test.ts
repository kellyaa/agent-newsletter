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

// Covers src/layouts/BaseLayout.astro end to end via a real Astro build.
// BaseLayout had zero direct test coverage: every other site test exercises
// it only incidentally (as the wrapper every page renders inside), and all
// of them set SITE_BASE to a value *without* a trailing slash
// ("/newsletter"), so BaseLayout's `baseUrl.endsWith("/") ? ... : ...`
// branches for both searchUrl and rssUrl have only ever been exercised on
// one side — the trailing-slash branch (and the all-defaults-no-env-vars
// case) were never built. This also covers the CSP meta tag content
// (sec-check issue #167) and the header brand link / footer RSS link, none
// of which any existing test asserts on.
//
// The fixture builds src/pages/search.astro, the cheapest page that wraps
// BaseLayout: it needs no issues content collection data at all (Astro logs
// a benign "no files found" warning for the empty glob loader and still
// builds every content-independent page normally).

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

async function buildFixtureSite() {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "newsletter-baselayout-"));
  const fixtureSite = join(fixtureRoot, "site");

  await mkdir(fixtureSite, { recursive: true });
  await symlink(join(siteRoot, "node_modules"), join(fixtureSite, "node_modules"), "dir");
  await writeFile(
    join(fixtureSite, "package.json"),
    await readFile(join(siteRoot, "package.json")),
  );
  // See issue-page.test.ts / appendix-section.test.ts: each fixture needs
  // its own Astro content-layer cache dir AND its own Vite dependency cache
  // dir so concurrent `astro build` runs in this suite (and sibling suites)
  // don't race on a shared cache (see PR #238's thread for the two-cache
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
  // The content collection loader needs the directory to exist, but search
  // page rendering needs no issue files in it.
  await mkdir(join(fixtureSite, "src", "content", "issues", "agents"), { recursive: true });

  return { fixtureRoot, fixtureSite };
}

function runBuild(fixtureSite: string, env: Record<string, string | undefined>) {
  execFileSync(join(siteRoot, "node_modules", ".bin", "astro"), ["build"], {
    cwd: fixtureSite,
    env: { ...process.env, ...env },
    stdio: "pipe",
  });
}

test("BaseLayout renders CSP, title/description, and base-aware search/RSS/brand links for a trailing-slash base", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite();

  try {
    runBuild(fixtureSite, { SITE_BASE: "/newsletter/", SITE_URL: "https://example.test" });

    const html = await readFile(join(fixtureSite, "dist", "agents", "search", "index.html"), "utf8");

    // Title and description (both passed explicitly by search.astro).
    assert.ok(html.includes("<title>Search — AI Agents Daily</title>"));
    assert.ok(
      html.includes('content="Search past issues of AI Agents Daily."'),
      "the page's explicit description prop should render in the meta description tag",
    );

    // CSP meta tag: a locked-down policy limiting XSS blast radius
    // (sec-check #167). Assert on the policy actually present, not just
    // that *some* CSP exists — this is a security-relevant header and a
    // regression here (e.g. accidentally widening script-src) should fail
    // loudly.
    assert.ok(
      html.includes(
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'self\' \'unsafe-inline\' \'wasm-unsafe-eval\'; style-src \'self\' \'unsafe-inline\' https://rsms.me https://fonts.googleapis.com; font-src \'self\' https://rsms.me https://fonts.gstatic.com; img-src \'self\' data: https:; connect-src \'self\'; worker-src \'self\'; base-uri \'self\'; form-action \'none\'; frame-ancestors \'none\'">',
      ),
      "the CSP meta tag's exact policy string should render unchanged",
    );

    // base ends with "/" -> searchUrl/rssUrl must NOT get an extra slash
    // inserted (the `: baseUrl + "/search/"` branch is for the no-trailing-
    // slash case; this is the other branch).
    assert.ok(
      html.includes('<a class="site-header__search" href="/newsletter/agents/search/">Search</a>'),
      "search link should not double a trailing slash when base already ends with one",
    );
    assert.ok(
      html.includes('<link rel="alternate" type="application/rss+xml" title="AI Agents Daily" href="/newsletter/agents/rss.xml">'),
      "RSS alternate link should not double a trailing slash when base already ends with one",
    );
    assert.ok(
      html.includes('<a href="/newsletter/agents/rss.xml">RSS</a>'),
      "footer RSS link should use the same rssUrl construction",
    );

    // Brand link uses baseUrl directly (no searchUrl/rssUrl-style branching).
    assert.ok(
      html.includes('<a class="site-header__brand" href="/newsletter/agents/">AI Agents Daily</a>'),
      "brand link should point at the edition front page",
    );

    // Static footer copy renders verbatim.
    assert.ok(
      html.includes("AI Agents Daily — automated digest, opinions sharpened by Claude"),
    );
    assert.ok(html.includes("updated daily ~07:00 PT"));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("BaseLayout's search/RSS links append a single slash when base has none, and the brand link uses the base as-is", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite();

  try {
    // SITE_BASE has NO trailing slash here (unlike every other site test,
    // which always uses "/newsletter/"-style values or this exact
    // no-slash value incidentally) — this exercises the *other* branch of
    // BaseLayout's `baseUrl.endsWith("/") ? baseUrl + "x/" : baseUrl + "/x/"`
    // ternaries for searchUrl/rssUrl: a slash must be appended, not doubled
    // or omitted.
    runBuild(fixtureSite, { SITE_BASE: "/newsletter", SITE_URL: "https://example.test" });

    const html = await readFile(join(fixtureSite, "dist", "agents", "search", "index.html"), "utf8");

    // Brand link uses baseUrl directly with no slash-handling at all, so a
    // base with no trailing slash renders with none added.
    assert.ok(
      html.includes('<a class="site-header__brand" href="/newsletter/agents/">AI Agents Daily</a>'),
      "brand link should join the base and the edition slug with a single slash",
    );
    // searchUrl/rssUrl must append exactly one slash (not zero, not two).
    assert.ok(
      html.includes('<a class="site-header__search" href="/newsletter/agents/search/">Search</a>'),
      "search link should append a single slash when base has none",
    );
    assert.ok(
      html.includes('<link rel="alternate" type="application/rss+xml" title="AI Agents Daily" href="/newsletter/agents/rss.xml">'),
      "RSS alternate link should append a single slash when base has none",
    );
    assert.ok(html.includes('<a href="/newsletter/agents/rss.xml">RSS</a>'));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("BaseLayout falls back to root-relative links when no SITE_BASE/SITE_URL env vars are set", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite();

  try {
    // No SITE_BASE/SITE_URL at all: astro.config.mjs falls back to its own
    // defaults ("/" and "https://example.github.io"), exercising the
    // `baseUrl || "/"` fallback in BaseLayout itself (base === "/" already
    // ends with "/", so this is a degenerate case of the trailing-slash
    // branch, at the root rather than a sub-path).
    runBuild(fixtureSite, { SITE_BASE: undefined, SITE_URL: undefined });

    const html = await readFile(join(fixtureSite, "dist", "agents", "search", "index.html"), "utf8");

    assert.ok(
      html.includes('<a class="site-header__brand" href="/agents/">AI Agents Daily</a>'),
      "brand link should fall back to /<edition>/ when no base is configured",
    );
    assert.ok(
      html.includes('<a class="site-header__search" href="/agents/search/">Search</a>'),
    );
    assert.ok(
      html.includes('<link rel="alternate" type="application/rss+xml" title="AI Agents Daily" href="/agents/rss.xml">'),
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
