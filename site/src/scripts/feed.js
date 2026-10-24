import rss from "@astrojs/rss";
import { safeHref } from "./safe-href";

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
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

/**
 * Build the RSS response for one edition. `issues` must already be filtered to
 * that edition; `edition` is { slug, name, tagline }.
 */
export function buildFeed(context, edition, issues) {
  const sorted = [...issues].sort((a, b) => (a.data.date < b.data.date ? 1 : -1));

  const base = import.meta.env.BASE_URL.replace(/\/$/, "");

  return rss({
    title: edition.name,
    description: edition.tagline,
    site: context.site,
    items: sorted.map((issue) => ({
      title: issue.data.theme
        ? `${issue.data.date} — ${issue.data.theme.split(/[.!?]/)[0]}`
        : `Issue ${issue.data.date}`,
      description: issue.data.theme || `${edition.name} — ${issue.data.date}`,
      pubDate: new Date(`${issue.data.date}T12:00:00Z`),
      link: `${base}/${edition.slug}/issues/${issue.data.date}/`,
      content: renderItemContent(issue),
    })),
    customData: `<language>en-us</language>`,
  });
}
