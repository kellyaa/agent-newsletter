// Defense-in-depth guard for item URLs rendered as href attributes.
//
// Item URLs originate from scraped/fed content (RSS, arXiv, HN, scraped
// HTML) and are supposed to be rejected at ingest (scripts/fetch.py's
// upsert_items) and at the content-collection schema boundary
// (site/src/content.config.ts) if they aren't http(s). This helper is the
// last line of defense at the render sink itself: if a non-http(s) URL
// somehow reaches a template (e.g. hand-edited content, a future loader
// that bypasses the schema), rendering it as `javascript:`/`data:`/`file:`
// would be a click-to-execute vector. `rel="noopener noreferrer"` does NOT
// mitigate this — it only affects window.opener / referrer leakage, not
// the scheme the browser executes when the link is clicked.
//
// See issue #248.
const ALLOWED_SCHEMES = new Set(["http:", "https:"]);

/**
 * Return `url` if it parses as an absolute http(s) URL, otherwise `"#"`
 * (a safe, inert fallback that keeps the link visually present but
 * unclickable-to-harm).
 */
export function safeHref(url: string): string {
  try {
    const parsed = new URL(url);
    if (ALLOWED_SCHEMES.has(parsed.protocol)) {
      return url;
    }
  } catch {
    // Not a parseable absolute URL — fall through to the safe default.
  }
  return "#";
}
