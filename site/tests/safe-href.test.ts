import assert from "node:assert/strict";
import test from "node:test";
import { safeHref } from "../src/scripts/safe-href.ts";

// Defense-in-depth guard for item URLs rendered as href attributes.
// See issue #248: scripts/fetch.py and content.config.ts are expected to
// reject non-http(s) URLs before they reach a template, but safeHref() is
// the last line of defense at the render sink itself.

test("safeHref passes through well-formed http and https URLs unchanged", () => {
  assert.equal(safeHref("https://example.com/article"), "https://example.com/article");
  assert.equal(safeHref("http://example.com/article?x=1#y"), "http://example.com/article?x=1#y");
});

test("safeHref rejects javascript:, data:, file:, and vbscript: schemes", () => {
  assert.equal(safeHref("javascript:alert(1)"), "#");
  assert.equal(safeHref("JaVaScRiPt:alert(1)"), "#");
  assert.equal(safeHref("data:text/html,<script>alert(1)</script>"), "#");
  assert.equal(safeHref("file:///etc/passwd"), "#");
  assert.equal(safeHref("vbscript:msgbox(1)"), "#");
});

test("safeHref rejects unparseable/relative strings rather than throwing", () => {
  assert.equal(safeHref("not a url"), "#");
  assert.equal(safeHref(""), "#");
  // A protocol-relative or path-relative string has no absolute scheme for
  // `new URL()` to parse without a base, so it also falls back to "#" —
  // item URLs from feeds are always expected to be absolute.
  assert.equal(safeHref("/relative/path"), "#");
});
