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

// Covers src/components/Search.astro end to end via a real Astro build.
// Search.astro had zero direct test coverage: no existing test asserts on
// the #search mount node, the pagefind stylesheet link, or the inline
// loader script — base-layout.test.ts builds src/pages/search.astro only
// as BaseLayout's cheapest wrapper and never looks past the header/footer.
// This closes that gap, including the one conditional in the component:
// `const pagefindBase = base.endsWith("/") ? base : base + "/";` — exercised
// here on all three bases the site actually builds with (trailing slash,
// no trailing slash, and the root-default "/" which already ends in "/").

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

async function buildFixtureSite(prefix: string) {
  const fixtureRoot = await mkdtemp(join(tmpdir(), prefix));
  const fixtureSite = join(fixtureRoot, "site");

  await mkdir(fixtureSite, { recursive: true });
  await symlink(join(siteRoot, "node_modules"), join(fixtureSite, "node_modules"), "dir");
  await writeFile(
    join(fixtureSite, "package.json"),
    await readFile(join(siteRoot, "package.json")),
  );
  // See base-layout.test.ts / issue-page-sections.test.ts: each fixture
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
  // The content collection loader needs the directory to exist, but the
  // search page needs no issue files in it.
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

test("Search renders the #search mount node, a base-aware pagefind stylesheet link, and the loader script, for a trailing-slash base", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite("newsletter-search-");

  try {
    runBuild(fixtureSite, { SITE_BASE: "/newsletter/", SITE_URL: "https://example.test" });

    const html = await readFile(join(fixtureSite, "dist", "search", "index.html"), "utf8");

    // The mount node pagefind's loader script attaches its UI to.
    assert.ok(
      html.includes('<div id="search" class="pagefind-search"'),
      "the pagefind mount node should render with id=search",
    );

    // base already ends with "/" -> pagefindBase must be used as-is, not
    // doubled, in both the stylesheet href and the inline loader script.
    assert.ok(
      html.includes('<link rel="stylesheet" href="/newsletter/pagefind/pagefind-ui.css">'),
      "pagefind stylesheet link should not double a trailing slash when base already ends with one",
    );
    assert.ok(
      html.includes('pagefindBase = "/newsletter/"'),
      "the inline loader script should receive the base as-is when it already ends with a slash",
    );
    assert.ok(
      html.includes("script.src = `${pagefindBase}pagefind/pagefind-ui.js`"),
      "the loader script should build the pagefind-ui.js URL from pagefindBase",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Search's pagefind stylesheet and loader script append a single slash when base has none", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite("newsletter-search-noslash-");

  try {
    // SITE_BASE has NO trailing slash here, exercising the other branch of
    // Search.astro's `base.endsWith("/") ? base : base + "/"` ternary: a
    // slash must be appended, not doubled or omitted.
    runBuild(fixtureSite, { SITE_BASE: "/newsletter", SITE_URL: "https://example.test" });

    const html = await readFile(join(fixtureSite, "dist", "search", "index.html"), "utf8");

    assert.ok(
      html.includes('<link rel="stylesheet" href="/newsletter/pagefind/pagefind-ui.css">'),
      "pagefind stylesheet link should append a single slash when base has none",
    );
    assert.ok(
      html.includes('pagefindBase = "/newsletter/"'),
      "the inline loader script should receive a single trailing slash appended to the base",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Search falls back to the root pagefind path when no SITE_BASE/SITE_URL env vars are set", async () => {
  const { fixtureRoot, fixtureSite } = await buildFixtureSite("newsletter-search-root-");

  try {
    // No SITE_BASE/SITE_URL at all: astro.config.mjs falls back to its own
    // defaults ("/" and "https://example.github.io"). base === "/" already
    // ends with "/", so this is the degenerate, root case of the
    // trailing-slash branch — also exercises Search.astro's own
    // `import.meta.env.BASE_URL || "/"` fallback.
    runBuild(fixtureSite, { SITE_BASE: undefined, SITE_URL: undefined });

    const html = await readFile(join(fixtureSite, "dist", "search", "index.html"), "utf8");

    assert.ok(
      html.includes('<link rel="stylesheet" href="/pagefind/pagefind-ui.css">'),
      "pagefind stylesheet link should fall back to the root path when no base is configured",
    );
    assert.ok(
      html.includes('pagefindBase = "/"'),
      "the inline loader script should receive the root path when no base is configured",
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
