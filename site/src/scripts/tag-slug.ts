// Tags come from a closed vocabulary of lowercase, hyphenated words
// (frameworks, tool-use, cost-latency, ...). Only tags that are safe as a
// single URL path segment get a /tags/<tag>/ page and a clickable chip;
// anything else (stray punctuation, slashes, markup) is rendered inert.

const TAG_SLUG = /^[a-z0-9][a-z0-9-]*$/;

export function isTagSlug(tag: string): boolean {
  return TAG_SLUG.test(tag);
}
