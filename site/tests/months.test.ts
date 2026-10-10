import assert from "node:assert/strict";
import test from "node:test";
import { buildMonthList, groupByMonth } from "../src/scripts/months.ts";

function issue(date: string, id = date) {
  return { id, data: { date } };
}

test("groupByMonth sorts months and issues newest first without mutating input", () => {
  const issues = [
    issue("2025-12-28", "dec-late"),
    issue("2026-02-03", "feb-early"),
    issue("2026-01-15", "jan"),
    issue("2025-12-02", "dec-early"),
    issue("2026-02-25", "feb-late"),
  ];
  const originalOrder = issues.map(({ id }) => id);

  const buckets = groupByMonth(issues);

  assert.deepEqual(
    buckets.map(({ key }) => key),
    ["2026-02", "2026-01", "2025-12"],
  );
  assert.deepEqual(
    buckets.map(({ issues: monthIssues }) => monthIssues.map(({ id }) => id)),
    [
      ["feb-late", "feb-early"],
      ["jan"],
      ["dec-late", "dec-early"],
    ],
  );
  assert.deepEqual(issues.map(({ id }) => id), originalOrder);
});

test("groupByMonth returns no buckets for an empty collection", () => {
  assert.deepEqual(groupByMonth([]), []);
});

test("buildMonthList labels and counts months and links latest to home", () => {
  const buckets = groupByMonth([
    issue("2026-01-15", "jan"),
    issue("2026-03-08", "mar-early"),
    issue("2026-03-21", "mar-late"),
  ]);

  assert.deepEqual(buildMonthList(buckets), [
    { key: "2026-03", label: "Mar 2026", count: 2, href: "/" },
    { key: "2026-01", label: "Jan 2026", count: 1, href: "/months/2026-01/" },
  ]);
});

test("buildMonthList handles no months", () => {
  assert.deepEqual(buildMonthList([]), []);
});
