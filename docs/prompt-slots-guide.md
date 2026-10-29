# Prompt Slots Guide

How an edition customizes the ranker and writer prompts without touching code.
The implementation is `scripts/prompts.py`; this guide is the practical
companion. For scoring rules see [ranking-rubric.md](ranking-rubric.md), and for
`edition.yaml` fields see [edition-yaml-reference.md](edition-yaml-reference.md).

## 1. How it works

Two shared base templates hold everything that is not subject-specific:

- `prompts/rank.md` (the ranker)
- `prompts/write.md` (the writer)

Each contains `{{slot}}` placeholders. When a stage runs, `prompts.render()`
fills every placeholder from one of two places:

1. **A fragment file** at `editions/<slug>/prompts/<stage>.<slot>.md`, where
   `<stage>` is `rank` or `write`.
2. **A scalar field of `edition.yaml`**: `name`, `tagline`, `reader_profile`,
   `downweighted` or `tags`.

The edition is selected by the `EDITION` environment variable (`run.sh` sets it
for you). The rendered result is written to `logs/<slug>/<stage>.rendered.md`,
which is the file to read when a prompt behaves oddly.

Slot syntax: `{{` + lowercase letter + lowercase letters, digits, `-` or `_` +
`}}`. For example `{{theme-examples}}`. Anything else is left as literal text.

### Rendering rules

Rendering raises an `EditionError` (and the stage fails) when:

| Problem | Why it fails |
|---|---|
| A placeholder has no value | No fragment and no `edition.yaml` field supplies it. The message names the file to create. |
| A placeholder has two values | A fragment and an `edition.yaml` scalar both define the same slot. |
| A fragment is unused | `<stage>.<name>.md` exists but the template has no `{{name}}`. This catches typos in fragment names. |
| Unknown stage | Only `rank` and `write` exist. |

Substitution is a **single pass**: a fragment that itself contains `{{x}}` is
not expanded again. Keep fragments free of `{{` anyway so they cannot be
mistaken for slots. A single trailing newline in a fragment is stripped.

Files in `editions/<slug>/prompts/` that do not match `<stage>.*.md` (such as
the template's `README.md`) are ignored.

## 2. Slot reference

### Ranker (`prompts/rank.md`)

| Slot | Source | Where it lands | What to put in it |
|---|---|---|---|
| `{{subject}}` | `rank.subject.md` | Title: "Ranker - Daily {{subject}} Newsletter" | Two or three words naming the subject, e.g. `AI Agents`. |
| `{{intro}}` | `rank.intro.md` | Opening paragraph | One sentence: who the ranker works for and about what. |
| `{{reader}}` | `rank.reader.md` | "Reader profile" section | Numbered list of what the reader cares about, plus an "Explicitly down-weighted" line. |
| `{{relevance}}` | `rank.relevance.md` | First bullet of the 4-axis rubric | The relevance axis (0-4) as one bullet: what scores high and what scores zero for this subject. |
| `{{sections}}` | `rank.sections.md` | "Section-specific axis emphasis" | Three bullets (Papers, News, Blogs): what counts as strong or weak in each. |
| `{{tags}}` | `edition.yaml` `tags` | "Closed-vocabulary tags" | Rendered as backticked, comma-separated values. Do not create a fragment. |

### Writer (`prompts/write.md`)

| Slot | Source | Where it lands | What to put in it |
|---|---|---|---|
| `{{subject}}` | `write.subject.md` | Title: "Writer - Daily {{subject}} Newsletter" | Same as the ranker's subject. |
| `{{intro}}` | `write.intro.md` | Opening paragraph | One sentence: who the writer works for and about what. |
| `{{voice}}` | `write.voice.md` | "Voice and style" | Do / Do not lists and the summary length cap. |
| `{{sections}}` | `write.sections.md` | "Section context" | How the voice tilts for papers, news and blogs. |
| `{{theme-examples}}` | `write.theme-examples.md` | End of the "Theme" section | One good theme and three bad ones (dense list, abstract-shaped, generic) for this subject. |

`{{sections}}` and `{{intro}}` exist in both stages but are separate files
(`rank.sections.md` vs `write.sections.md`).

### Scalar slots from `edition.yaml`

`name`, `tagline`, `reader_profile`, `downweighted` and `tags` can be used as
placeholders, but a base template only uses the ones it actually contains. At
the time of writing only `{{tags}}` appears in the base templates. Check with
`grep -n '{{' prompts/*.md` before relying on one. If you add a placeholder to a
base template, remember every edition then needs a value for it.

Lists render as plain comma-separated text, except `tags`, which are
backticked (`` `tag-a`, `tag-b` ``). A name that is a scalar must not also have a
fragment; delete the fragment or rename the slot.

## 3. Writing a fragment

1. Copy the reference set: `editions/agents/prompts/` is the model, and
   `editions/_template/prompts/` is the blank scaffold with TODOs.
   `scripts/new_edition.py` creates the scaffold for you.
2. Name the file exactly `<stage>.<slot>.md`, lowercase, matching the slot.
3. Write plain markdown. Fragments are pasted verbatim into the prompt at the
   placeholder's position, so match the shape the surrounding template expects
   (for example `rank.relevance.md` is a single bullet because it sits in a
   bullet list, and `rank.sections.md` is three bullets).
4. Do not repeat the template's own instructions; the fragment only supplies the
   subject-specific part.
5. Do not include `{{`.

Because feed content is untrusted, do not paste fetched text into fragments, and
leave the security notice that `prompts.py` prepends alone (see
[SECURITY-AI.md](../SECURITY-AI.md)).

### Example: a ranker tone change

`editions/agents/prompts/rank.relevance.md` scores relevance to building and
running agents. A hypothetical edition about data engineering would replace it
with its own single bullet:

```markdown
- **Relevance to the "running data pipelines" beat (0-4).** Does this item directly help a senior data engineer build, test, schedule or debug a production pipeline? Orchestrator internals, schema-evolution postmortems, lineage and quality tooling -> high. Generic "data is the new oil" commentary, vendor funding news -> zero.
```

### Example: a writer voice change

`write.voice.md` holds the Do / Do not lists. Tightening the summary cap or
banning a phrase is a one-file change; no Python is touched.

## 4. Testing a change

Work from cheapest to most expensive.

**1. Lint (no LLM, no network for prompts).** Checks that both prompts render and
that no placeholders remain unfilled:

```bash
uv run scripts/check_edition.py <slug>
```

It also lints `sources.yaml` and does a dry run of fetch, recency and keyword
gate, which needs network access.

**2. Render and read.** Prints the final prompt without calling a model:

```bash
cd scripts && EDITION=<slug> python3 -c "import prompts; print(prompts.render('rank'))"
```

(`render('write')` for the writer.) Read it top to bottom as the model would.

**3. Replay the writer against a real past day.** `scripts/replay_writer.py`
re-runs only the writer stage on a published day's featured items. It is
read-only: it does not change `state.db` or the archived issue file.

```bash
EDITION=<slug> uv run scripts/replay_writer.py YYYY-MM-DD
```

Output goes to `logs/<slug>/theme-replay-<date>.json`. This does call the
writer LLM, so it costs tokens. It needs `LLM_BASE_URL`, `LLM_API_KEY` and `WRITER_MODEL` set, the content worktree at `.worktrees/content` (it reads past issue files from there), and `state.db` containing that date's published items; see CONTRIBUTING.md.
There is no equivalent replay for the ranker, so ranker changes are best
checked by steps 1 and 2 plus a normal run.

**4. Unit tests** for the renderer live in `tests/test_prompts_render.py`; run `uv run pytest` if you
change `scripts/prompts.py` or a base template.

## 5. Common mistakes

- **Fragment typo** (`rank.releveance.md`): fails with "not used by rank.md".
- **Fragment for a scalar slot** (`rank.tags.md`): fails with "defined both by
  ... and by edition.yaml".
- **Missing fragment after a base template gains a new slot**: every edition
  fails until it adds the file. Run `check_edition.py` for each edition.
- **Wrong stage prefix**: `write.relevance.md` is unused because `write.md` has
  no `{{relevance}}`.
- **Editing the base template for one edition's taste**: base templates are
  shared by all editions. Put edition-specific wording in fragments.
