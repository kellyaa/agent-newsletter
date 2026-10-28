# Reflections

A reflection log: short, dated notes on what a run, incident or review taught us, so the next human or agent does not relearn it. This satisfies the ACMM `acmm:reflection-log` criterion (`docs/reflections/`).

Use it for lessons about *how we work* (a pipeline stage that failed in an unexpected way, a review that kept asking for the same change, a source that turned out unreliable). Not for task tracking (use issues) and not for per-edition editorial config (that lives in `editions/<slug>/`).

## Adding an entry

Create `docs/reflections/YYYY-MM-DD-<short-slug>.md`:

```markdown
# <One-line title>

- **Context:** what was being done, with issue/PR links
- **What happened:** the outcome, good or bad
- **Lesson:** the rule or habit to carry forward
- **Follow-up:** issue/PR that encodes the lesson (test, lint, doc), or "none"
```

Prefer turning a lesson into a test, lint or `AGENTS.md` rule; the reflection records why it exists. Do not put secrets or personal data in entries.
