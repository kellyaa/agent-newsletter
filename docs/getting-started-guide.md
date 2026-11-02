# Getting Started: First-Time Contributors

New to the project? This guide walks you through understanding the codebase, setting up your environment, and making your first contribution. After this, see [README.md](README.md) to navigate to guides for specific topics.

---

## 1. Understand what this project does (5 minutes)

Read the first two sections of [SPEC.md](../SPEC.md):
- **Goals & Non-Goals**: What the newsletter does and doesn't do
- **Audience & Editorial Rubric**: Who reads it and what they care about

Then skim the README's [§ Daily pipeline](../README.md#daily-pipeline) diagram. The pipeline has five stages: fetch → prefilter → rank → write → publish. Each is independent and idempotent.

**Key insight:** The pipeline is not magic. It's a series of deterministic filters (fetch, prefilter) followed by LLM calls (rank, write) that follow rubrics defined in prompt files, not code.

---

## 2. Local setup (10 minutes)

Follow [CONTRIBUTING.md § Local setup](../CONTRIBUTING.md#local-setup), stopping after you install Python and Node dependencies. Skip the LLM credentials step for now.

```bash
git clone https://github.com/kellyaa/agent-newsletter.git
cd agent-newsletter
uv sync
pnpm --prefix site install
```

Verify the setup:
```bash
uv run --extra test pytest --co -q | head -20   # list some tests
pnpm --prefix site test 2>&1 | tail -5            # run site tests
```

---

## 3. Explore the code structure (10 minutes)

```
scripts/
  fetch.py          # Collectors (RSS, arXiv, HN, etc.) — no LLM
  prefilter.py      # Gates (recency, keywords, dedup) — no LLM
  rank.py           # Ranker: 3 LLM calls, one per section
  write.py          # Writer: 1 LLM call, produces the issue
  publish.py        # Mark items as published; commit to content branch

editions/
  agents/           # Example: AI Agents Daily newsletter
    edition.yaml    # Configuration (reader profile, thresholds, sources)
    sources.yaml    # Feed list
    prompts/        # Prompt fragments (merged with base templates)

prompts/
  rank.md           # Ranker base template with {{slots}} for edition wording
  write.md          # Writer base template

tests/
  test_fetch.py, test_prefilter.py, etc. — fixtures and unit tests
  e2e/              — end-to-end tests (full pipeline on throwaway DB)
```

**Read the pipeline in order:**
1. `scripts/fetch.py` — pick one collector (e.g., `fetch_rss()`)
2. `scripts/prefilter.py` — pick one gate (e.g., `recency_gate()`)
3. `scripts/rank.py` — entry point; calls LLM three times
4. `scripts/write.py` — entry point; calls LLM once; writes issue file

Each is ~150–300 lines. Reading them takes ~30 minutes and makes the architecture concrete.

---

## 4. Run the tests (5 minutes)

```bash
uv run --extra test pytest tests/test_models.py -v
```

This tests the data types and helpers. Pick one test and trace what it does.

---

## 5. Choose your first contribution (5 minutes)

Pick from one of these:

### Option A: Documentation (Low risk, high impact)

- Add a guide on a topic not yet covered (see [README.md](README.md) for what exists)
- Improve an existing guide's clarity or examples
- Tier 0: one glance from a human, no code review needed

**Example:** "Add a troubleshooting guide for 'no items featured today' "

### Option B: Tests (Low risk, medium impact)

- The coverage gate is 95% (currently 100%). Add a test for uncovered edge case.
- Tier 1: human review, tests must pass

**Example:** "Add test for item dedup across editions"

### Option C: Config or operations (Low risk)

- Add a new source to `editions/agents/sources.yaml` or tune thresholds
- Update `prompts/rank.md` prompt fragments
- Tier 0 or 1 depending on scope

**Example:** "Add Simon Willison's Weeknotes RSS feed to agents edition"

### Option D: Code (Medium risk)

- Fix a bug in prefilter or rank
- Refactor a function for clarity
- Tier 1+: human review, tests, coverage gate

**Example:** "Simplify the near-dup detection in prefilter.py"

---

## 6. Create a branch and make your change

```bash
git checkout -b docs/my-guide       # or fix/, feat/, quality/ depending on type
# Make your changes
git commit -s -m "docs: explain <topic>"   # -s adds DCO sign-off
git push origin docs/my-guide
```

See [CONTRIBUTING.md § PR workflow](../CONTRIBUTING.md#pr-workflow-and-naming) for branch naming conventions and commit style.

**Important:** Always run tests before pushing (if applicable).

```bash
# For docs-only changes, just verify links work:
grep -r "docs/" README.md CONTRIBUTING.md | head -5

# For code changes:
uv run --extra test pytest
```

---

## 7. Open a pull request

Push your branch, then open a PR against `main`:

```bash
gh pr create --title "<type>: <short description>" --body "..."
```

See [CONTRIBUTING.md](../CONTRIBUTING.md) for title conventions (`docs:`, `fix:`, etc.). The PR body should explain *why* not what (the diff says what).

**For hold-gated PRs:** If your change is infrastructure, security, or workflow-related, it may be labeled `hold` by the hive framework. Don't remove it — a human will review and remove it when ready.

---

## 8. Review feedback and iterate

The reviewer will comment with feedback. Reply inline or make new commits:

```bash
# Make improvements
git add <files>
git commit -s -m "docs: clarify <topic> per review"
git push origin docs/my-guide
```

The PR auto-updates. Do not force-push.

---

## What to read next

- [README.md](../README.md) — architecture, setup, scheduling, troubleshooting
- [CONTRIBUTING.md](../CONTRIBUTING.md) — complete workflow, code style, test structure
- [SPEC.md](../SPEC.md) — full design doc; canonical reference
- [README.md](README.md) — all guides, organized by task

---

## FAQ

**Q: Do I need LLM credentials to contribute?**

A: No, unless you're testing the ranker or writer stages. Documentation and test-only changes work without them. For a full pipeline run, set up `.env` (see [README.md § LLM configuration](../README.md#llm-configuration-env)).

**Q: What if my PR touches `.github/workflows/`?**

A: Don't. Agents can't push to workflow files. Open an issue with the exact replacement text instead (see [AGENTS.md](../AGENTS.md) § Workflow files).

**Q: How long until my PR is reviewed?**

A: Reviews happen within 1–2 business days. Check the issue tracker if you've been waiting longer.

**Q: Can I work on the content branch?**

A: No. Never commit to `content` by hand — the daily pipeline owns it. All human work goes on `main`.

**Q: How do I test my changes locally before pushing?**

A: Run `pytest` for code changes, check for broken links in docs. For the full pipeline, set up `.env` with dummy LLM credentials and run `./run.sh --edition agents --force`.

---

## Common patterns

### Adding a new feed source

1. Edit `editions/agents/sources.yaml` (add the URL)
2. Run `uv run scripts/check_edition.py agents` to verify it parses and fetches
3. Commit and open a PR
4. Read [adding-feed-sources-guide.md](adding-feed-sources-guide.md) for details

### Fixing a bug in prefilter

1. Write a failing test in `tests/test_prefilter.py`
2. Fix the bug in `scripts/prefilter.py`
3. Verify the test passes: `pytest tests/test_prefilter.py -v`
4. Run full suite: `pytest` (must stay ≥95% coverage)
5. Commit with DCO sign-off and open a PR

### Updating documentation

1. Edit the `.md` file
2. Verify no broken links: `python -m pytest tests/test_docs_paths.py -v`
3. Commit and open a PR
4. No code review needed if docs-only; one human glance

---

## Getting help

- **Issues:** [github.com/kellyaa/agent-newsletter/issues](https://github.com/kellyaa/agent-newsletter/issues) — search before opening; link to relevant issues in your PR
- **Project design:** [SPEC.md](../SPEC.md)
- **Code style:** [CONTRIBUTING.md § Code style](../CONTRIBUTING.md#code-style)
- **Tests as examples:** Look at existing tests in `tests/` to see patterns

---

**Ready?** Go back to [CONTRIBUTING.md](../CONTRIBUTING.md), pick an issue, or open an issue for what you want to work on. Welcome aboard! 🎉
