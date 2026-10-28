import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  editionOf,
  editionPath,
  editionsDir,
  issuesFor,
  listEditions,
  loadRegistry,
  parseEdition,
  readEditions,
  unknownContentEntries,
} from "../src/scripts/editions.ts";
import { editionYaml } from "./seed-editions.ts";

test("parseEdition reads the five fields the site needs and ignores the rest", () => {
  const yaml = [
    editionYaml({ slug: "agents", name: "AI Agents Daily", tagline: "T.", status: "live", order: 10 }),
    "reader_profile: ignored by the site",
    "keywords: ['\\bagent\\b']",
  ].join("\n");
  assert.deepEqual(parseEdition(yaml, "agents"), {
    slug: "agents",
    name: "AI Agents Daily",
    tagline: "T.",
    status: "live",
    order: 10,
  });
});

test("parseEdition rejects bad config loudly", () => {
  const ok = { slug: "agents" };
  assert.throws(() => parseEdition(editionYaml(ok), "other"), /does not match its folder/);
  assert.throws(() => parseEdition(editionYaml({ ...ok, status: "beta" }), "agents"), /status must be one of/);
  assert.throws(() => parseEdition(editionYaml({ slug: "Bad_Slug" }), "Bad_Slug"), /must match/);
  assert.throws(() => parseEdition("- a\n- b\n", "agents"), /mapping/);
  assert.throws(() => parseEdition("slug: agents\nname: x\nstatus: live\norder: 1\n", "agents"), /"tagline"/);
  assert.throws(
    () => parseEdition(editionYaml(ok).replace("order: 10", "order: soon"), "agents"),
    /"order" must be a number/,
  );
});

test("listEditions hides drafts unless asked, keeps paused, and sorts by order then slug", () => {
  const all = [
    { slug: "z", name: "Z", tagline: "t", status: "live", order: 20 },
    { slug: "d", name: "D", tagline: "t", status: "draft", order: 5 },
    { slug: "p", name: "P", tagline: "t", status: "paused", order: 20 },
    { slug: "a", name: "A", tagline: "t", status: "live", order: 10 },
  ] as const;
  assert.deepEqual(listEditions([...all], false).map((e) => e.slug), ["a", "p", "z"]);
  assert.deepEqual(listEditions([...all], true).map((e) => e.slug), ["d", "a", "p", "z"]);
});

test("readEditions ignores _template and dot folders and stray files", async () => {
  const dir = await mkdtemp(join(tmpdir(), "editions-"));
  try {
    for (const [slug, o] of [
      ["agents", { slug: "agents", order: 10 }],
      ["sdlc", { slug: "sdlc", order: 20, status: "draft" }],
      ["_template", { slug: "_template" }],
    ] as const) {
      await mkdir(join(dir, slug));
      await writeFile(join(dir, slug, "edition.yaml"), editionYaml(o));
    }
    await mkdir(join(dir, ".hidden"));
    await writeFile(join(dir, "README.md"), "not an edition");

    assert.deepEqual(readEditions(dir).map((e) => e.slug), ["agents", "sdlc"]);
    assert.deepEqual(readEditions(join(dir, "missing")), []);

    await mkdir(join(dir, "broken"));
    assert.throws(() => readEditions(dir), /editions\/broken\/ has no edition\.yaml/);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("content for an edition the registry does not know is reported", () => {
  const all = [{ slug: "agents", name: "A", tagline: "t", status: "live", order: 1 }] as const;
  assert.deepEqual(unknownContentEntries(["agents", "ghost", "2026-06-01.md"], [...all]), [
    "ghost",
    "2026-06-01.md",
  ]);
  assert.deepEqual(unknownContentEntries(["agents"], [...all]), []);
});

test("issuesFor filters entries by the first segment of their id", () => {
  const entries = [{ id: "agents/2026-06-01" }, { id: "sdlc/2026-06-01" }, { id: "agents/2026-06-02" }];
  assert.equal(editionOf(entries[1]), "sdlc");
  assert.deepEqual(issuesFor(entries, "agents").map((e) => e.id), ["agents/2026-06-01", "agents/2026-06-02"]);
});

test("parseEdition names the offending file when the YAML does not parse", () => {
  assert.throws(
    () => parseEdition("slug: [unclosed\n", "agents"),
    /editions\/agents\/edition\.yaml: invalid YAML/,
  );
});

test("parseEdition rejects a non-finite order and blank strings", () => {
  const base = editionYaml({ slug: "agents" });
  assert.throws(() => parseEdition(base.replace("order: 10", "order: .inf"), "agents"), /"order" must be a number/);
  assert.throws(() => parseEdition(base.replace(/^name: .*$/m, 'name: "   "'), "agents"), /"name" must be a non-empty string/);
});

test("editionPath builds site-relative paths inside an edition", () => {
  assert.equal(editionPath("agents"), "/agents/");
  assert.equal(editionPath("agents", "issues/2026-06-01/"), "/agents/issues/2026-06-01/");
});

test("editionsDir honours EDITIONS_DIR and otherwise points at ../editions", () => {
  const saved = process.env.EDITIONS_DIR;
  try {
    process.env.EDITIONS_DIR = "/somewhere/else";
    assert.equal(editionsDir(), "/somewhere/else");
    delete process.env.EDITIONS_DIR;
    assert.equal(editionsDir(), join(process.cwd(), "..", "editions"));
  } finally {
    if (saved === undefined) delete process.env.EDITIONS_DIR;
    else process.env.EDITIONS_DIR = saved;
  }
});

test("loadRegistry lists every edition in `all` but hides drafts from `listed`", async () => {
  const dir = await mkdtemp(join(tmpdir(), "registry-"));
  try {
    for (const [slug, o] of [
      ["agents", { slug: "agents", order: 10 }],
      ["sdlc", { slug: "sdlc", order: 20, status: "draft" }],
    ] as const) {
      await mkdir(join(dir, slug));
      await writeFile(join(dir, slug, "edition.yaml"), editionYaml(o));
    }
    const hidden = loadRegistry(dir, false);
    assert.deepEqual(hidden.all.map((e) => e.slug), ["agents", "sdlc"]);
    assert.deepEqual(hidden.listed.map((e) => e.slug), ["agents"]);
    assert.deepEqual(loadRegistry(dir, true).listed.map((e) => e.slug), ["agents", "sdlc"]);

    const saved = process.env.SHOW_DRAFTS;
    try {
      process.env.SHOW_DRAFTS = "1";
      assert.deepEqual(loadRegistry(dir).listed.map((e) => e.slug), ["agents", "sdlc"]);
      process.env.SHOW_DRAFTS = "0";
      assert.deepEqual(loadRegistry(dir).listed.map((e) => e.slug), ["agents"]);
    } finally {
      if (saved === undefined) delete process.env.SHOW_DRAFTS;
      else process.env.SHOW_DRAFTS = saved;
    }
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
