#!/usr/bin/env node
// Audit-trail report: which recent commits were made by automated contributors,
// and do they carry the DCO sign-off AGENTS.md requires?  Read-only; uses git only.
//
//   node scripts/ai-audit-report.mjs [--since "30 days ago"] [--ref HEAD] [--check]
//
// --check exits 1 if any commit lacks a Signed-off-by trailer.
import { execFileSync } from "node:child_process";

const SEP = "\x1e";
const FIELD = "\x1f";
const BOT = /\[bot\]|\bbot\b|co-authored-by:.*(claude|copilot|codex|bob)/i;

export function parseLog(raw) {
  return raw
    .split(SEP)
    .map((r) => r.trim())
    .filter(Boolean)
    .map((r) => {
      const [sha, author, subject, body = ""] = r.split(FIELD);
      return {
        sha,
        author,
        subject,
        signedOff: /^Signed-off-by:\s+.+<.+>/im.test(body),
        automated: BOT.test(`${author}\n${body}`),
      };
    });
}

export function report(commits) {
  const unsigned = commits.filter((c) => !c.signedOff);
  const automated = commits.filter((c) => c.automated);
  const lines = [
    "# AI audit report",
    "",
    `- Commits: ${commits.length}`,
    `- Automated-contributor commits: ${automated.length}`,
    `- Missing DCO sign-off: ${unsigned.length}`,
  ];
  if (unsigned.length) {
    lines.push("", "## Missing sign-off", "");
    for (const c of unsigned) lines.push(`- ${c.sha.slice(0, 9)} ${c.author}: ${c.subject}`);
  }
  return lines.join("\n");
}

export function main(argv) {
  const opt = (name, dflt) => {
    const i = argv.indexOf(name);
    return i >= 0 && argv[i + 1] ? argv[i + 1] : dflt;
  };
  const raw = execFileSync(
    "git",
    ["log", opt("--ref", "HEAD"), `--since=${opt("--since", "30 days ago")}`,
      `--format=${SEP}%H${FIELD}%an${FIELD}%s${FIELD}%b`],
    { encoding: "utf8" },
  );
  const commits = parseLog(raw);
  console.log(report(commits));
  return argv.includes("--check") && commits.some((c) => !c.signedOff) ? 1 : 0;
}

if (import.meta.url === `file://${process.argv[1]}`) process.exitCode = main(process.argv.slice(2));
