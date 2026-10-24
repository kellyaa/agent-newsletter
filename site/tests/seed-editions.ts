import { cp, mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const siteRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * Give a fixture site its edition registry. The site reads `../editions` from
 * its working directory, so a fixture at <root>/site needs <root>/editions.
 * With no `editions` argument the real `agents` edition.yaml is copied, which
 * keeps the fixtures honest about the file the site actually reads.
 */
export async function seedEditions(
  fixtureRoot: string,
  editions?: Record<string, string>,
): Promise<void> {
  const target = join(fixtureRoot, "editions");
  if (editions === undefined) {
    await mkdir(join(target, "agents"), { recursive: true });
    await cp(
      join(siteRoot, "..", "editions", "agents", "edition.yaml"),
      join(target, "agents", "edition.yaml"),
    );
    return;
  }
  for (const [slug, yaml] of Object.entries(editions)) {
    await mkdir(join(target, slug), { recursive: true });
    await writeFile(join(target, slug, "edition.yaml"), yaml);
  }
}

export function editionYaml(o: {
  slug: string;
  name?: string;
  tagline?: string;
  status?: string;
  order?: number;
}): string {
  return [
    `slug: ${o.slug}`,
    `name: ${JSON.stringify(o.name ?? o.slug)}`,
    `tagline: ${JSON.stringify(o.tagline ?? `Tagline of ${o.slug}`)}`,
    `status: ${o.status ?? "live"}`,
    `order: ${o.order ?? 10}`,
    "",
  ].join("\n");
}
