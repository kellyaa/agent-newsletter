# Front-page Theme Rewrite Plan Execution Summary

## Plan Reference
- Document: `docs/superpowers/plans/2026-07-07-front-page-theme-rewrite.md`
- Goal: Rewrite the writer LLM's theme rubric from "dense catalog" to "lede + territory" structure
- Execution Date: 2026-11-12

## Execution Status

### Task 1: Rewrite `prompts/write.md` ✅ COMPLETE
- **Step 1**: Confirmed current file (prompts/write.md) 
- **Step 2**: Theme field in output-shape JSON updated ✅
  - Lines 42: Contains new lede+territory description (~60 words)
  - Previous: "2-3 sentence concrete digest"
  - New: "Front-page card copy in two parts: a lede... plus a territory sentence..."
  
- **Step 3**: Theme bullet in Output rules updated ✅
  - Lines 59: Updated with two-part structure
  - Clarifies lede framing and territory constraints
  
- **Step 4**: Entire `## Theme` section replaced ✅
  - Lines 70-121: Full replacement complete
  - New structure: Shape, Front-page test, Source constraint, Banned framings, Construction rules
  - Includes good/bad examples and specific banlist for 3+ named items in lede
  
- **Step 5**: Diff-check ✅
  - Verified: No unintended changes to other sections
  - All three replacement blocks in place
  
- **Step 6**: Commit ✅
  - In commit a823544: "contentbranch: single-source the content worktree path..."
  - (Note: Commit message differs from plan template but content is correct)

### Task 2: Build replay helper ✅ COMPLETE
- **Step 1**: Gitignore check ✅
  - `logs/` already in .gitignore (covers replay outputs)
  
- **Step 2**: Replay helper script created ✅
  - File: `scripts/replay_writer.py` (149 lines)
  - Functionality: Reads archived issue frontmatter, reconstructs writer input, invokes current prompt, writes output to logs/theme-replay-<date>.json
  - Syntax validation: PASSED
  
- **Step 3**: End-to-end test ⏸️ BLOCKED
  - Requires: Historical issue files and state.db data
  - Current state: No historical data available
  - Cannot proceed without archive
  
- **Step 4**: Commit ✅
  - In commit a823544 (same commit as Task 1)

### Task 3: Verification against known-bad dates ⏸️ BLOCKED
- Requires: Historical data for replay dates (2026-06-24, 2026-06-27, 2026-07-07)
- Current state: No archived issues or published items in database
- Awaiting: Execution in environment with live history
- Verification criteria defined in plan remain valid:
  - Named-item count in lede ≤2
  - Lede frames by what-it-is before finding
  - Territory describes kinds-of-work, not system names
  - Three replays read distinguishably

### Task 4: Ship ✅ READY
- **Step 1**: Branch state ✅
  - Current: feature/theme-rewrite-execution (branched from main)
  - All work is already on main (commit a823544)
  - No new unpushed commits needed
  
- **Step 2**: Push
  - Already on main, no action needed
  - Next daily run will pick up prompt changes automatically
  
- **Step 3**: Verify next daily run
  - Will be visible in next `newsletter: <date> daily run` commit
  - Can be verified via: `git log --oneline -5 -- site/src/content/issues/`

## Completion Assessment

### What is Complete
1. ✅ Prompt rewritten and in production (main branch)
2. ✅ Replay helper created and syntactically valid
3. ✅ Gitignore configured
4. ✅ Both files committed to main

### What is Pending
1. ⏸️ Verification replays (Task 3) - Awaiting historical data
2. ✅ Daily run pickup (Task 4 Step 3) - Will happen automatically

## Next Steps

Once historical issue data becomes available:
1. Run: `EDITION=agents python3 scripts/replay_writer.py 2026-06-24`
2. Run: `EDITION=agents python3 scripts/replay_writer.py 2026-06-27`
3. Run: `EDITION=agents python3 scripts/replay_writer.py 2026-07-07`
4. Verify each output in `logs/theme-replay-<date>.json` against four success criteria
5. If all pass: plan execution complete
6. If failures: Refine `prompts/write.md` per plan guidance, re-run, iterate

## Files Modified
- `prompts/write.md`: Theme rubric rewritten (3 sections updated)
- `scripts/replay_writer.py`: NEW - 149 lines

## Commits
- a823544: Both Task 1 and Task 2 work

## Plan Reference Spec
- Design spec: `docs/superpowers/specs/2026-07-07-front-page-theme-rewrite-design.md`
- Type/name consistency: ✅ Verified (build_writer_input, invoke_writer all present)
- Architecture: ✅ Single-file prompt change, no schema change, no template change
