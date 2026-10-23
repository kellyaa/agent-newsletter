// Edition registry for the multi-edition site (#291, §8).
//
// The pipeline's source of truth for an edition is editions/<slug>/edition.yaml
// on `main`. The site reads only the five fields it needs (slug, name, tagline,
// status, order) at build time. Validation of the rest of the file belongs to
// scripts/edition.py; here a bad file fails the build loudly instead of
// silently dropping an edition from the site.
//
// Kept free of Astro imports so it can be unit-tested with plain `node --test`.

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "yaml";

export const UMBRELLA_NAME = "The Daily Inference";
export const UMBRELLA_DESCRIPTION =
  "Daily, opinionated digests for engineers building with AI.";

export const EDITION_STATUSES = ["draft", "live", "paused"] as const;
export type EditionStatus = (typeof EDITION_STATUSES)[number];

export interface EditionInfo {
  slug: string;
  name: string;
  tagline: string;
  status: EditionStatus;
  order: number;
}

export interface Registry {
  /** Every real edition folder, drafts included (used to reject stray content). */
  all: EditionInfo[];
  /** Editions the site builds and lists, sorted by `order` then slug. */
  listed: EditionInfo[];
}

const SLUG_RE = /^[a-z0-9-]+$/;

/** Parse one edition.yaml. `folder` is the directory name the file came from. */
export function parseEdition(text: string, folder: string): EditionInfo {
  let raw: unknown;
  try {
    raw = parse(text);
  } catch (e) {
    throw new Error(`editions/${folder}/edition.yaml: invalid YAML (${(e as Error).message})`);
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error(`editions/${folder}/edition.yaml: expected a mapping at the top level`);
  }
  const d = raw as Record<string, unknown>;
  const where = `editions/${folder}/edition.yaml`;

  const str = (key: string): string => {
    const v = d[key];
    if (typeof v !== "string" || v.trim() === "") {
      throw new Error(`${where}: "${key}" must be a non-empty string`);
    }
    return v.trim();
  };

  const slug = str("slug");
  if (!SLUG_RE.test(slug)) {
    throw new Error(`${where}: slug "${slug}" must match [a-z0-9-]+`);
  }
  if (slug !== folder) {
    throw new Error(`${where}: slug "${slug}" does not match its folder name "${folder}"`);
  }
  const status = d.status;
  if (typeof status !== "string" || !(EDITION_STATUSES as readonly string[]).includes(status)) {
    throw new Error(`${where}: status must be one of ${EDITION_STATUSES.join(", ")}`);
  }
  const order = d.order;
  if (typeof order !== "number" || !Number.isFinite(order)) {
    throw new Error(`${where}: "order" must be a number`);
  }
  return {
    slug,
    name: str("name"),
    tagline: str("tagline"),
    status: status as EditionStatus,
    order,
  };
}

function byOrder(a: EditionInfo, b: EditionInfo): number {
  return a.order - b.order || a.slug.localeCompare(b.slug);
}

/** Read every editions/<slug>/edition.yaml. `_template` and dot folders are ignored. */
export function readEditions(dir: string): EditionInfo[] {
  if (!existsSync(dir)) return [];
  const found: EditionInfo[] = [];
  for (const name of readdirSync(dir).sort()) {
    if (name.startsWith("_") || name.startsWith(".")) continue;
    const folder = resolve(dir, name);
    if (!statSync(folder).isDirectory()) continue;
    const file = resolve(folder, "edition.yaml");
    if (!existsSync(file)) {
      throw new Error(`editions/${name}/ has no edition.yaml`);
    }
    found.push(parseEdition(readFileSync(file, "utf8"), name));
  }
  return found.sort(byOrder);
}

/** `draft` editions are hidden unless `showDrafts`. */
export function listEditions(all: EditionInfo[], showDrafts: boolean): EditionInfo[] {
  return all.filter((e) => showDrafts || e.status !== "draft").sort(byOrder);
}

/** Directory holding the edition folders: ../editions from the site root. */
export function editionsDir(): string {
  return process.env.EDITIONS_DIR ?? resolve(process.cwd(), "..", "editions");
}

export function loadRegistry(
  dir: string = editionsDir(),
  showDrafts: boolean = process.env.SHOW_DRAFTS === "1",
): Registry {
  const all = readEditions(dir);
  return { all, listed: listEditions(all, showDrafts) };
}

/**
 * Reject content that no edition owns. `entries` are the names found directly
 * under src/content/issues (folders, and any stray files). Returns the list of
 * problems; the caller decides how to fail.
 */
export function unknownContentEntries(entries: string[], all: EditionInfo[]): string[] {
  const known = new Set(all.map((e) => e.slug));
  return entries.filter((name) => !known.has(name));
}

/** The edition an issue entry belongs to: the first segment of its id. */
export function editionOf(entry: { id: string }): string {
  return entry.id.split("/")[0];
}

export function issuesFor<T extends { id: string }>(issues: T[], slug: string): T[] {
  return issues.filter((i) => editionOf(i) === slug);
}

/** Site-relative path (no BASE_URL) to something inside an edition. */
export function editionPath(slug: string, rest = ""): string {
  return `/${slug}/${rest}`;
}
