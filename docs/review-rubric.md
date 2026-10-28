# Pull request review rubric

What a reviewer (human or agent) checks before approving a PR. Satisfies the ACMM `acmm:pr-review-rubric` criterion. The author-side checklist is `.github/pull_request_template.md`.

| Area | Question | Pass when |
| --- | --- | --- |
| Scope | Does the PR do one thing and name the issue it closes (`Closes #N`) or partly addresses (`Refs #N`, with what is left)? | No unrelated refactors; files touched are listed in the PR body |
| Tests | Is every behaviour change covered by a test? | `uv run --extra test pytest` passes; the 95% coverage gate is not lowered |
| Edition config | Does edition behaviour come from `editions/<slug>/`? | No hard-coded slugs in `scripts/`; `uv run scripts/check_edition.py <slug>` is clean for touched editions |
| Architecture | Do stage scripts share state only through `state.db` and `db.py`, `edition.py`, `models.py`, `llm.py`? | No stage-to-stage imports; LLM calls only through `scripts/llm.py` |
| Docs | Are new scripts or documented paths reflected in `README.md` / `CONTRIBUTING.md`? | `tests/test_docs_paths.py` passes |
| Site | If `site/` changed, does it build and pass its tests? | `pnpm --prefix site build` succeeds |
| Safety | Any secrets, `.env` content, or edits to `.github/workflows/**` by an agent? | None; workflow changes come via an issue with replacement text |
| Process | DCO sign-off on every commit; `hold` label untouched? | `git commit -s` used; `hold` removed only by a human |

Outcome: approve when every row passes; otherwise request changes and cite the row. Record a repeated correction in `.memory/corrections.jsonl`.
