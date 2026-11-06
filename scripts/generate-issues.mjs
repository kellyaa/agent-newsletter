#!/usr/bin/env node
// Auto issue generation (ACMM acmm:auto-issue-gen): turn a markdown backlog
// into GitHub issues. Each `## ` heading in the file is one issue; the text
// under it becomes the body. Issues whose title already matches an open issue
// are skipped, so re-running over the same backlog is safe. Read-only unless
// --create is passed, which files the issues through `gh` (agent-policy.yaml
// still applies: run it as a human, or from a role allowed to open issues).
//
//   node scripts/generate-issues.mjs <backlog.md> [--repo owner/name]
//                                    [--label <label>]… [--create]
//
// --create files the planned issues; without it the script only prints the plan.
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

export function parseBacklog(raw) {
  const issues = [];
  let cur = null;
  for (const line of raw.split("\n")) {
    const m = /^## (.+?)\s*$/.exec(line);
    if (m) {
      if (cur) issues.push(cur);
      cur = { title: m[1], body: "" };
    } else if (cur) {
      cur.body += `${line}\n`;
    }
  }
  if (cur) issues.push(cur);
  return issues.map((i) => ({ title: i.title, body: i.body.trim() }));
}

export function planIssues(issues, existingTitles) {
  const open = new Set(existingTitles.map((t) => t.trim().toLowerCase()));
  const toCreate = [];
  const skipped = [];
  for (const i of issues) {
    (open.has(i.title.toLowerCase()) ? skipped : toCreate).push(i);
  }
  return { toCreate, skipped };
}

function openTitles(repo) {
  const args = ["issue", "list", "--state", "open", "--limit", "500", "--json", "title"];
  if (repo) args.push("--repo", repo);
  return JSON.parse(execFileSync("gh", args, { encoding: "utf8" })).map((i) => i.title);
}

export function main(argv) {
  const opt = (name, dflt) => {
    const i = argv.indexOf(name);
    return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : dflt;
  };
  const labels = argv.flatMap((a, i) => (a === "--label" && argv[i + 1] ? [argv[i + 1]] : []));
  // Skip option values so `--repo o/n backlog.md` finds the file, not `o/n`.
  const valued = new Set(["--repo", "--label"]);
  const file = argv.find((a, i) => !a.startsWith("--") && !valued.has(argv[i - 1]));
  if (!file) {
    console.error(
      "usage: node scripts/generate-issues.mjs <backlog.md> [--repo owner/name] [--label <label>]… [--create]",
    );
    return 2;
  }
  const backlog = parseBacklog(readFileSync(file, "utf8"));
  if (!backlog.length) {
    console.error(`no '## ' issue headings found in ${file}`);
    return 2;
  }
  const { toCreate, skipped } = planIssues(backlog, openTitles(opt("--repo")));
  for (const s of skipped) console.log(`skip (already open): ${s.title}`);
  for (const i of toCreate) {
    if (!argv.includes("--create")) {
      console.log(`would create: ${i.title}`);
      continue;
    }
    const args = ["issue", "create", "--title", i.title, "--body-file", "-"];
    for (const l of labels) args.push("--label", l);
    if (opt("--repo")) args.push("--repo", opt("--repo"));
    const url = execFileSync("gh", args, { input: i.body, encoding: "utf8" }).trim();
    console.log(`created: ${i.title} -> ${url}`);
  }
  if (!argv.includes("--create")) console.log("(dry run; pass --create to file them)");
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) process.exitCode = main(process.argv.slice(2));
