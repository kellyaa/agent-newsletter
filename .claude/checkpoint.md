# Checkpoint

Mid-task state for an agent session that may be interrupted. Overwrite it as work progresses; clear the "In progress" section when the task is done. Longer hand-off notes for the next session go in `.claude/session-summary.md`. (ACMM `acmm:session-continuity`.)

## In progress

One unpushed commit ready for merge:
- Commit: 39506f4 (docs: verify dedupe task completion #630)
- File: DEDUPE_VERIFICATION.md (verification report, 104 lines)
- Status: Ready to push to origin/main
- Note: Supervisor role is READ-ONLY; push requires elevated credentials

## How to resume

### Current task: Push verification documentation
1. Branch: main (local ahead by 1 commit: 39506f4)
2. File modified: DEDUPE_VERIFICATION.md (104 lines, added)
3. Next command: `git push origin main` (requires merge-capable credentials)
4. After pushing: Task complete, update .claude/checkpoint.md to clear "In progress"

### For new work:
1. Read `AGENTS.md`, `.claude/session-summary.md` and `.memory/corrections.jsonl`.
2. `git status` and `git log -5` on your branch; branch from a fresh `origin/main` if starting new work.
3. Record here: the issue number, the branch, files already changed, and the next command to run.
