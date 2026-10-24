import { getCollection } from "astro:content";
import { loadRegistry, issuesFor } from "../scripts/editions";
import { buildFeed } from "../scripts/feed.js";

// /rss.xml stays valid as a copy of /agents/rss.xml so existing subscribers
// keep working after the move to per-edition feeds (#291, answer A4).
export async function GET(context) {
  const agents = loadRegistry().listed.find((e) => e.slug === "agents");
  if (!agents) return new Response("Not found", { status: 404 });
  const issues = issuesFor(await getCollection("issues"), agents.slug);
  return buildFeed(context, agents, issues);
}
