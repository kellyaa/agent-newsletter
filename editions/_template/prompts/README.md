# Prompt fragments

Not part of the rendered prompt. `scripts/prompts.py` fills the `{{slots}}` of
`prompts/rank.md` and `prompts/write.md` from the files named
`<stage>.<slot>.md` in this folder (anything else here is ignored). Every slot
in the base template needs a value, and a fragment no slot uses is an error.

Slots filled from `edition.yaml` instead of a fragment: `name`, `tagline`,
`reader_profile`, `downweighted`, `tags`. Do not also create a fragment for
those.

| File | What it holds |
|---|---|
| `rank.subject.md` | Two or three words naming the subject; goes in the title ("Daily {{subject}} Newsletter"). |
| `rank.intro.md` | One sentence: who the ranker works for and about what. |
| `rank.reader.md` | The reader profile as a numbered list of what they care about, plus an "Explicitly down-weighted" line. |
| `rank.relevance.md` | The relevance axis (0-4), one bullet. Say what scores high and what scores zero for THIS subject. |
| `rank.sections.md` | Three bullets (Papers, News, Blogs): what counts as strong or weak in each. |
| `write.subject.md` | Same as `rank.subject.md`. |
| `write.intro.md` | One sentence: who the writer works for and about what. |
| `write.voice.md` | The voice: Do / Do not lists, summary length cap. |
| `write.sections.md` | How the voice tilts for papers, news and blogs. |
| `write.theme-examples.md` | A good theme and three bad ones (dense list, abstract-shaped, generic) for this subject. |

`editions/agents/prompts/` is the reference. Keep fragments free of `{{` so
they cannot be mistaken for slots.
