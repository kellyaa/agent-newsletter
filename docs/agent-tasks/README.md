# Agent task traceability

How work done by an AI agent is traced back to the task that asked for it, and forward to the change that resolved it. It adds no new rules; see [`AGENTS.md`](../../AGENTS.md) and [`agent-boundaries.md`](../agent-boundaries.md). (ACMM `aef:task-traceability`.)

## The chain

Every agent change should be reconstructable from GitHub alone:

| Step | Record | Link back |
|------|--------|-----------|
| Task | A GitHub issue (human-filed, or filed by an agent for a confirmed finding) | — |
| Work | A branch off `main` | Branch name says what it is for |
| Change | A pull request labelled `hold` | Body says `Closes #N`, or `Refs #N` with what is left open and why |
| Commits | Signed off (`git commit -s`) | Subject follows `<scope>: <description>` |
| Review | A human removes `hold` and merges | The merge commit names the PR |

## Rules

- No agent PR without an issue. If none exists, file one first so the reason for the change is recorded.
- A PR touching more than one issue says so explicitly in its body.
- Reasons go in the PR body, not in code comments; the diff already shows what changed.
- Anything an agent decided not to do (for example a change to `.github/workflows/**`) is recorded as an issue with the exact replacement text, not silently dropped.

## Where to look

- Open work: the repository's GitHub issues and PRs.
- What an agent may touch: [`policies/agent-policy.yaml`](../../policies/agent-policy.yaml).
- How much review a change needs: [`risk-tiers.md`](../risk-tiers.md).
