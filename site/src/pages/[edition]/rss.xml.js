import { getCollection } from "astro:content";
import { loadRegistry, issuesFor } from "../../scripts/editions";
import { buildFeed } from "../../scripts/feed.js";

export function getStaticPaths() {
  return loadRegistry().listed.map((edition) => ({
    params: { edition: edition.slug },
    props: { edition },
  }));
}

export async function GET(context) {
  const { edition } = context.props;
  const issues = issuesFor(await getCollection("issues"), edition.slug);
  return buildFeed(context, edition, issues);
}
