import assert from "node:assert/strict";
import test from "node:test";
import {
  SECTIONS,
  SECTION_DISPLAY,
  featuredCountKey,
  sectionCount,
} from "../src/scripts/sections.ts";

test("SECTION_DISPLAY has an entry for every section, in SECTIONS order", () => {
  assert.deepEqual(Object.keys(SECTION_DISPLAY), [...SECTIONS]);
});

test("featuredCountKey names the issue metadata count for a section", () => {
  assert.deepEqual(SECTIONS.map(featuredCountKey), [
    "items_featured_papers",
    "items_featured_news",
    "items_featured_blogs",
  ]);
});

test("sectionCount picks singular or plural", () => {
  assert.equal(sectionCount("papers", 1), "1 paper");
  assert.equal(sectionCount("papers", 2), "2 papers");
  assert.equal(sectionCount("blogs", 1), "1 blog");
  assert.equal(sectionCount("blogs", 3), "3 blogs");
  assert.equal(sectionCount("news", 1), "1 news");
  assert.equal(sectionCount("news", 4), "4 news");
});
