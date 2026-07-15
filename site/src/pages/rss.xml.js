import rss from "@astrojs/rss";
import { getCollection } from "astro:content";

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Scheme-guard for hrefs: HTML-encoding does not neutralize a javascript:
// URL in an <a href>. `db.py::canonicalize_url` already rejects non-http(s)
// at ingestion, but z.string().url() in content.config.ts admits them, so
// belt-and-suspenders guard here too.
function safeHref(url) {
  return /^https?:\/\//i.test(url) ? url : "#";
}

function renderItemContent(issue) {
  const parts = [];
  if (issue.data.theme) {
    parts.push(`<p><em>${escapeHtml(issue.data.theme)}</em></p>`);
  }
  const featured = [...issue.data.featured].sort((a, b) => b.score - a.score);
  if (featured.length > 0) {
    parts.push("<h2>Featured</h2>");
    for (const f of featured) {
      parts.push(
        `<h3><a href="${escapeHtml(safeHref(f.url))}">${escapeHtml(f.title)}</a></h3>`,
      );
      const meta = [f.section, f.author].filter(Boolean).map(escapeHtml).join(" · ");
      if (meta) parts.push(`<p><small>${meta}</small></p>`);
      if (f.summary) parts.push(`<p>${escapeHtml(f.summary)}</p>`);
      if (f.takeaway) parts.push(`<p><strong>Takeaway:</strong> ${escapeHtml(f.takeaway)}</p>`);
      if (f.open_question) parts.push(`<p><strong>Open question:</strong> ${escapeHtml(f.open_question)}</p>`);
    }
  }
  return parts.join("\n");
}

export async function GET(context) {
  const issues = await getCollection("issues");
  issues.sort((a, b) => (a.data.date < b.data.date ? 1 : -1));

  const base = import.meta.env.BASE_URL.replace(/\/$/, "");

  return rss({
    title: "AI Agents Daily",
    description:
      "A daily, opinionated digest on building and running AI agents.",
    site: context.site,
    items: issues.map((issue) => ({
      title: issue.data.theme
        ? `${issue.data.date} — ${issue.data.theme.split(/[.!?]/)[0]}`
        : `Issue ${issue.data.date}`,
      description: issue.data.theme || `AI Agents Daily — ${issue.data.date}`,
      pubDate: new Date(`${issue.data.date}T12:00:00Z`),
      link: `${base}/issues/${issue.id}/`,
      content: renderItemContent(issue),
    })),
    customData: `<language>en-us</language>`,
  });
}
