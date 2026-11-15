# Dedupe: Centralize run_attempts Outcome Vocabulary

## ✅ TASK COMPLETE & VERIFIED

**Date:** 2026-11-08 (merged)  
**Verification Date:** 2026-11-14  
**Status:** Production-ready, actively deployed on main branch

---

## Summary

The run_attempts outcome vocabulary (`started`, `ok`, `failed`, `none`) has been successfully centralized in `scripts/models.py` as a single source of truth. This prevents the silent-failure risk of one-sided renames between Python (db.py, run_attempt.py) and shell (run.sh, run_edition.sh) that would cause the production orchestrator to loop or skip editions.

---

## What Was Done

### 1. Centralized Vocabulary
- Created `AttemptOutcome` StrEnum in `scripts/models.py` with three values
- Added `NO_ATTEMPT = "none"` sentinel for "no attempts yet"
- Both are imported by db.py and run_attempt.py

### 2. Eliminated Hardcoded Literals
- db.py: Now imports `AttemptOutcome` instead of `'started'` string literal
- run_attempt.py: Now imports `AttemptOutcome` and `NO_ATTEMPT` instead of inline strings
- Result: One-sided renames now cause test failure, not silent production chaos

### 3. Test Coverage
- Created `tests/test_attempt_outcome_single_source.py` with 7 test functions:
  - Validates exact enum values never drift
  - Ensures db.py and run_attempt.py don't hardcode
  - Verifies run.sh and run_edition.sh use only enumerated values (via regex)
  - Full roundtrip test: start → fail → retry → succeed

### 4. Documentation
- Added governance rule to AGENTS.md
- Rule specifies where contract lives and how it's enforced
- Test referenced in enforcement note

---

## Verification Results

| Component | Check | Status |
|-----------|-------|--------|
| **models.py** | Enum defined correctly | ✅ |
| **db.py** | Imports from models | ✅ |
| **db.py** | Uses enum values only | ✅ |
| **run_attempt.py** | Imports from models | ✅ |
| **run_attempt.py** | Uses enum values only | ✅ |
| **run_edition.sh** | Uses enumerated finish values | ✅ |
| **run.sh** | Uses enumerated read values | ✅ |
| **Test coverage** | 7 functions, comprehensive | ✅ |
| **Shell syntax** | Both scripts validate | ✅ |
| **Documentation** | AGENTS.md updated | ✅ |

**Conclusion:** All checks pass. Implementation is production-ready.

---

## Commit Information

- **Main commit:** `1b23e63e1f9ded8e8d6e054e1cf158615195d435`
- **Branch merged:** `arch/backfill-writer-dedupe` → `main`
- **PR:** #630
- **Files changed:** 5
  - `scripts/models.py` (+16 lines)
  - `scripts/db.py` (modified to import)
  - `scripts/run_attempt.py` (modified to import)
  - `tests/test_attempt_outcome_single_source.py` (+129 new test lines)
  - `AGENTS.md` (added governance rule)

---

## Deployment Status

- ✅ On main branch
- ✅ Part of the 95% coverage gate (enforced by CI)
- ✅ No configuration changes required
- ✅ Backward compatible (stored values unchanged)
- ✅ Active in all daily newsletter runs since 2026-11-08

---

## Risk Mitigation

**Before:** 
- Outcome values scattered across 5 files
- Silent failure if one side renamed the value
- No detection until production orchestration failed

**After:**
- Single source of truth
- Test enforces consistency across Python/shell boundary
- Immediate test failure on any one-sided rename
- Governance rule prevents regression

---

## Next Steps

None. Verification complete. The dedupe task is deployed and monitored by CI/CD.

