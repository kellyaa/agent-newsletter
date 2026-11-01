# Testing Patterns Guide

How the test suite is built and how to add tests that fit it. [CONTRIBUTING.md](../CONTRIBUTING.md) says to run the tests and keep coverage up; this guide covers the conventions behind that. Every pattern below is taken from a test that exists in `tests/`; look there for the full version.

## 1. Running tests

```bash
uv sync --extra test
uv run --extra test pytest                          # whole suite, with coverage
uv run --extra test pytest tests/test_db.py -v      # one file
uv run --extra test pytest tests/test_db.py -k canonicalize   # by name
uv run --extra test pytest tests/e2e -v             # end-to-end only
uv run --extra test pytest --cov-report=html        # htmlcov/index.html
```

The coverage options are in `pyproject.toml` (`[tool.pytest.ini_options] addopts`): `--cov=scripts --cov-report=term-missing --cov-fail-under=95`, with `branch = true` under `[tool.coverage.run]`. So a plain `pytest` already measures line **and** branch coverage of `scripts/` and fails the run below 95%.

- The gate is a floor, not a target; the suite sits well above it. Never lower it. `.coverage-thresholds.json` mirrors the number and `tests/test_coverage_threshold_sync.py` fails if the two disagree, so change both together if it is ever raised.
- CI (`.github/workflows/tests.yml`) runs `uv run pytest --cov=scripts ...` and takes the threshold from `pyproject.toml`. Do not hard-code a threshold anywhere else.
- Lines under `if __name__ == "__main__":` and `pragma: no cover` are excluded; `main()` functions are not, so test them.
- Site tests are separate: `pnpm --prefix site test` (see `site/tests/`).

## 2. Test infrastructure (`tests/conftest.py`)

`conftest.py` does four things every test relies on:

1. Puts `scripts/` on `sys.path`, so tests write `import db`, `import rank`, not `from scripts import ...`.
2. Sets `os.environ.setdefault("EDITION", "agents")`. Stage modules read the edition at **import time** and there is deliberately no default edition in the code (`scripts/edition.py`), so tests run against the real `agents` edition.
3. Autouse `_no_inherited_run_date` removes `RUN_DATE` from the environment, so a value in your shell cannot leak in.
4. Autouse `_isolated_edition_logs` points `edition.LOGS_ROOT` at a temp directory, so rendered prompts and debug files never land in the repo's `logs/`.

It also provides `base_template(stage)`, which returns the real `prompts/<stage>.md` so tests that run a stage's `main()` against a temp prompt file use a template that fills every slot.

Consequences for new tests:

- Tests that need a different date set it themselves: `monkeypatch.setenv("RUN_DATE", "2026-10-17")`.
- Use `monkeypatch` for every environment or module-attribute change. It undoes the change after the test; a bare `os.environ[...] = ...` leaks into later tests.
- Never write to the real `editions/`, `logs/` or `.worktrees/`. Use `tmp_path`.

## 3. Database tests

Tests use a real SQLite database in `tmp_path`, never a mock. `db.init_db(path)` creates the full schema and `db.connect(path)` opens it:

```python
@pytest.fixture()
def db_path(tmp_path):
    import db as db_mod
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    return p
```

Insert rows with small `_insert_*` helpers local to the test file (`_insert_candidate`, `_insert_featured`, `_insert_item`...). They take the few columns the test cares about and default the rest, as in `tests/test_prefilter_main.py`:

```python
def _insert_item(conn, item_id, status="new", section=None, score=None, ...):
    conn.execute("INSERT OR REPLACE INTO items (...) VALUES (...)", (...))
    conn.commit()
```

Conventions:

- Seed with the helper, run the code under test, then **query the database** to assert the outcome (`SELECT status FROM items WHERE id = ?`). Asserting on state, not on call counts, is what makes these tests survive refactors.
- Use the `Status` and `Section` enums from `scripts/models.py` where the code does; the literal strings are their values.
- Close connections you open (`conn.close()`), or use a fixture that yields and closes.
- Stages normally open the module-level `DB_PATH`. Tests redirect them to the temp database by patching the stage's `connect` / `init_db` or path constant, for example `tests/test_write_main.py`:

```python
monkeypatch.setattr(write_mod, "ISSUES_DIR", issues_dir)
monkeypatch.setattr(write_mod, "connect", lambda: db_mod.connect(db_path))
monkeypatch.setattr(write_mod, "init_db", lambda: db_mod.init_db(db_path))
```

`reset_day` is simpler: its tests patch one attribute (`reset_day.CONTENT_ROOT`) and seed the database under `edition.runtime_paths(tmp_path, "agents")` (`TestResetDay` in `tests/test_multi_edition_layout.py`). Prefer the narrowest patch that redirects the paths.

### Import-time configuration and `importlib.reload`

Because paths and edition config are fixed when a module is imported, a test that needs a different `CONTENT_ROOT` sets the env var and reloads, `db` first:

```python
monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
import importlib, db
importlib.reload(db)
assert db.DB_PATH == tmp_path / "editions" / "agents" / "state.db"
```

If a test reloads `db`, reload dependents (`write`, `publish`, `rank`) after it; the wrong order leaves them pointing at the old paths and the test can pass against the wrong edition. Reload is used where the import-time binding is itself the behaviour under test (`tests/test_content_root.py`). For anything else, patch the attribute with `monkeypatch.setattr`.

## 4. Mocking the LLM

All LLM calls go through `scripts/llm.py` (a repo rule), so tests mock at that boundary and never touch the network. There are two levels:

**Mock the stage's imported `call_llm`** to test a stage's handling of the response (`tests/test_rank_invoke.py`):

```python
with patch("rank.call_llm", return_value={"rankings": rankings}):
    result = invoke_ranker("prompt text", label="papers")
assert result == rankings
```

For the writer, patch `write.invoke_writer` and return the dict shape the real one returns; `assert_called_once()` / `assert_not_called()` pin whether the model was reached at all (the appendix-only path must not call it).

**Mock `llm._one_shot`** to test `call_llm`'s own retry and error logic (`tests/test_llm_helpers.py`). `side_effect` with a list gives a sequence of outcomes, exceptions included:

```python
with patch("llm._one_shot", side_effect=[RuntimeError("parse fail"), {"answer": "ok"}]) as shot:
    assert call_llm("p", SCHEMA, "s", model="m", max_attempts=2) == {"answer": "ok"}
assert shot.call_count == 2
```

**Fake the client** to test response parsing in `_one_shot`: a `MagicMock` whose `chat.completions.create.return_value` has `.choices[0].message.content`, `.finish_reason` and `.usage`. See `_make_fake_client` in `tests/test_llm_helpers.py`.

Pure parsing helpers such as `_last_json_object` need no mocks at all; feed them strings (`tests/test_llm_parse.py`), including the malformed outputs seen in production.

Environment: `call_llm` raises without an API key, so tests that reach it set one with `monkeypatch.setenv("LLM_API_KEY", "test-key")` (the `_mock_env` helper) and clear `LLM_EXTRA_HEADERS` and `LLM_BASE_URL`.

## 5. Network sources

Fetch adapters are tested with `httpx` patched and feeds built in memory (`_rss_feed_content` in `tests/test_fetch_adapters.py` builds minimal RSS bytes). `subprocess` is patched for the `gh` adapter. No test makes a real request. When you add an adapter, cover: a normal response, a missing optional field, an entry that must be skipped, and an exception (the main loop isolates one source's failure from the others).

## 6. File and Markdown output

Write into `tmp_path`, then read the file back and assert on its content: parse the YAML front matter rather than comparing the whole string, unless the exact bytes are the contract.

`tests/test_prompts_render.py` is the byte-for-byte case: rendering the `agents` edition must equal `tests/golden/rank.md` and `tests/golden/write.md`. If you change a base prompt on purpose, update the golden files in the same PR and say why.

Several tests keep documentation honest by parsing it: `test_docs_paths.py` (README and CONTRIBUTING mention every script and reference only paths that exist), `test_spec_constants_sync.py` (constants quoted in `SPEC.md`), `test_tag_vocabulary_sync.py`, `test_ranking_rubric_sync.py`, `test_metrics_docs.py`. If you add a script, update `README.md`, or `test_docs_paths.py` fails.

## 7. Structural and policy tests

Some tests enforce architecture rather than behaviour:

- `test_stage_import_boundary.py` parses imports with `ast` and fails if `fetch`, `prefilter`, `rank`, `write`, `publish` or `run_attempt` import each other. Shared code belongs in `db.py`, `edition.py`, `llm.py`, `models.py` or `candidates.py`.
- `test_github_workflows.py` validates the workflow files (timeouts, single coverage gate, pinned toolchain).
- `test_agents_edition_unchanged.py` pins the `agents` edition's keywords and rules; update it in the same PR as an intentional tuning change.
- `test_section_names_single_source.py` and `test_sql_domain_constants.py` keep literals out of code that should use the enums.

If one of these fails on your change, fix the change, not the test, unless the rule itself is being changed on purpose.

## 8. Shell scripts and end-to-end tests

`tests/test_run_script.py` tests `run.sh` by copying it into `tmp_path` and running it with `subprocess.run(["bash", ...], cwd=tmp_path, timeout=10)`, asserting on the exit code and stderr. The lock test holds `.watchdog.lock` with `fcntl.flock` from Python and checks that `run.sh` exits without doing work. Always pass a `timeout`.

`tests/e2e/test_check_edition_e2e.py` runs `scripts/check_edition.py` as a real subprocess against a copy of `scripts/`, `prompts/` and a throwaway edition, with a local `http.server` thread serving an RSS feed (no internet, no LLM). Its fixtures show the pattern:

- a `feed_url` fixture that starts the server, `yield`s the URL, then shuts it down;
- a `sandbox` fixture that copies the repo pieces into `tmp_path` and writes a custom `edition.yaml` and `sources.yaml`;
- `EDITION` and proxy variables removed from the child's environment.

Use e2e only for what unit tests cannot show (argument parsing, process exit codes, modules wired together). They are slower; keep each one small.

## 9. Organisation and naming

- One file per module or concern: `test_<module>.py`, with suffixes for focus (`test_prefilter_main.py`, `test_prefilter_internals.py`). Structural tests are named for the rule.
- Group related tests in a `Test<Thing>` class when they share setup; plain functions are fine otherwise.
- Test names say the behaviour: `test_returns_2_when_prompt_missing`, `test_content_filter_not_retried`.
- A docstring on a test says why it exists when the reason is not obvious, especially for a regression (cite the issue number).
- Use `pytest.mark.parametrize` for tables of inputs (see `test_db.py` for URL canonicalisation).
- Check logged warnings with `caplog.at_level(logging.WARNING, logger="fetch")`.
- Use `pytest.raises(..., match="...")` with a message fragment, so the test fails if the wrong error is raised.

## 10. Branch coverage

Because `branch = true`, an `if` whose false path no test reaches shows as a partial branch even when every line ran. Run with `--cov-report=term-missing` (the default here) and read the `Missing` column: entries like `129->132` mean "the jump from line 129 to line 132 never happened".

`tests/test_branch_coverage.py` is the file of last resort for those: each test names the partial branch it closes (for example `fetch.py:152->154` a Hacker News hit with no `created_at`). When you add a branch, add the test that takes the other side in the same PR. Line numbers drift, so describe the case in words.

## 11. Anti-patterns

- **Vacuous assertions.** `assert x is not None`, `assert "a" in result`, or an `or` in an assertion pass for many wrong results. Assert the exact value.
- **Asserting only on mocks.** `mock.assert_called()` proves a call happened, not that the outcome is right. Pair it with a check of the database or file.
- **Relying on the wall clock.** Set `RUN_DATE` for run dates. Where a function takes `now=`, pass a fixed datetime (see the injectable-clock tests in `tests/test_fetch_db.py`).
- **Leaking state.** Module attributes patched without `monkeypatch`, or modules reloaded and not restored, change later tests' results depending on order.
- **Real I/O.** No network, no `logs/`, no repo `editions/`, no `.worktrees/`.
- **Copy-pasting 30 lines of INSERT.** Add or reuse a helper with defaults.
- **Fixing a failing structural test by editing it.** See section 7.

## 12. Performance

The suite is mostly pure Python and SQLite on `tmp_path`, so it runs quickly; keep it that way. Avoid `sleep`; patch `time.sleep` where code under test sleeps between requests (the arXiv and Reddit adapters do). Keep subprocess tests few and give them a `timeout`.

## 13. Debugging a failing test

```bash
uv run --extra test pytest tests/test_x.py::TestThing::test_case -x -vv   # stop at first failure, full diff
uv run --extra test pytest tests/test_x.py --pdb                          # drop into pdb at the failure
uv run --extra test pytest tests/test_x.py -s                             # show print() and logging output
uv run --extra test pytest tests/test_x.py --no-cov                       # skip coverage while iterating
uv run --extra test pytest --lf                                           # re-run only last failures
```

Put `breakpoint()` in the test or code for an interactive stop. Remember `--no-cov` is for iterating only; run the full suite before pushing, because the 95% gate applies to the whole run.

If a test passes alone but fails in the full run (or the reverse), suspect leaked state (section 11) or reload order (section 3). Run the pair with `pytest a.py b.py` to confirm.

## 14. Checklist for new code

1. Write the test first or alongside it; it must fail without your change.
2. Use the existing helpers and fixtures for that area before writing new ones.
3. Mock `call_llm`/`invoke_*` and `httpx`; use real SQLite in `tmp_path`.
4. Assert exact outcomes in the database or output file.
5. Cover both sides of each new branch, and error paths.
6. Run the full suite: `uv run --extra test pytest`. It must pass and meet the gate.
7. If you added a script or documented path, update `README.md` and `CONTRIBUTING.md`.
8. Sign off the commit (`git commit -s`).
