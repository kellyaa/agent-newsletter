# Checkpoint

Mid-task state for an agent session that may be interrupted. Overwrite it as work progresses; clear the "In progress" section when the task is done. Longer hand-off notes for the next session go in `.claude/session-summary.md`. (ACMM `acmm:session-continuity`.)

## In progress

Nothing in progress.

## How to resume

1. Read `AGENTS.md`, `.claude/session-summary.md` and `.memory/corrections.jsonl`.
2. `git status` and `git log -5` on your branch; branch from a fresh `origin/main` if starting new work.
3. Record here: the issue number, the branch, files already changed, and the next command to run.
