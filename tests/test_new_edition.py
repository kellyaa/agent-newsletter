"""scripts/new_edition.py: scaffolding, LLM-draft validation, REVIEW.md."""
import copy
import shutil

import feedparser
import httpx
import pytest
import yaml

import check_edition
import edition
import new_edition
from new_edition import NewEditionError

REAL = edition.EDITIONS_DIR


@pytest.fixture()
def editions_dir(tmp_path, monkeypatch):
    d = tmp_path / "editions"
    shutil.copytree(REAL, d, ignore=shutil.ignore_patterns("engineering-with-ai"))
    monkeypatch.setattr(edition, "EDITIONS_DIR", d)
    return d


def _atom(n=1):
    entries = "".join(
        f"<entry><title>T{i}</title><link href='https://x.test/{i}'/><id>{i}</id>"
        f"<summary>s</summary></entry>" for i in range(n)
    )
    return f"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>{entries}</feed>"


@pytest.fixture()
def net():
    """A mock internet: {url substring: bytes | int status | Exception}."""
    routes = {}

    def handler(request):
        for needle, resp in routes.items():
            if needle in str(request.url):
                if isinstance(resp, Exception):
                    raise resp
                if isinstance(resp, int):
                    return httpx.Response(resp)
                return httpx.Response(200, content=resp)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    client.routes = routes
    yield client
    client.close()


# ----------------------------------------------------------- the LLM stub

def _proposal():
    return {
        "tagline": "A daily digest for people who ship.",
        "reader_profile": "Senior engineers shipping software with AI tools.",
        "downweighted": ["hype", "funding news"],
        "keywords": [r"\bagent(s)?\b", r"\bcode review\b", "(unclosed", r"\bagent(s)?\b", " "],
        # (blank above: an empty regex would match every item)
        "tags": ["Code Review", "code-review", "Testing", "  ", "Dev Tools"],
        "prompts": {k: f"fragment for {k}\n" for k in new_edition.FRAGMENTS},
        "sources": {
            "rss": [
                {"id": "good", "name": "Good Blog", "url": "https://good.test/feed",
                 "keyword_gate_bypass": True, "recency_days": 90},
                {"id": "dead", "name": "Dead Blog", "url": "https://dead.test/feed",
                 "keyword_gate_bypass": False, "recency_days": None},
                {"id": "good", "name": "Dup Id", "url": "https://dup.test/feed",
                 "keyword_gate_bypass": False, "recency_days": None},
            ],
            "arxiv": [
                {"id": "se", "name": "arXiv SE", "query": "cat:cs.SE", "max_results": 30},
                {"id": "empty", "name": "arXiv none", "query": "cat:zz.ZZ", "max_results": 500},
                {"id": "blank", "name": "blank", "query": "  ", "max_results": 30},
            ],
            "hn": [
                {"id": "hn-ai", "name": "HN: ai", "query": "ai coding", "min_points": 40},
                {"id": "hn-bad", "name": "x", "query": " ", "min_points": 0},
            ],
            "reddit": [
                {"id": "r-ok", "name": "r/programming", "subreddit": "r/programming", "limit": 5},
                {"id": "r-dead", "name": "r/gone", "subreddit": "gone", "limit": 10},
                {"id": "r-bad", "name": "bad", "subreddit": "not a sub!", "limit": 10},
            ],
        },
    }


@pytest.fixture()
def llm_stub(monkeypatch):
    calls = []
    state = {"response": _proposal()}

    def fake(prompt, schema, schema_name, **kw):
        calls.append({"prompt": prompt, "schema": schema, "name": schema_name, **kw})
        return copy.deepcopy(state["response"])

    monkeypatch.setattr(new_edition.llm, "call_llm", fake)
    fake.calls, fake.state = calls, state
    return fake


def _routes(net):
    r = net.routes
    r["good.test"] = _atom(3)
    r["dead.test"] = 404
    r["dup.test"] = _atom(1)
    r["zz.ZZ"] = _atom(0)  # before the generic arXiv route: first match wins
    r["export.arxiv.org"] = _atom(1)
    r["reddit.com/r/programming"] = _atom(1)
    r["reddit.com/r/gone"] = 404


@pytest.fixture()
def run_llm(editions_dir, net, llm_stub, monkeypatch):
    _routes(net)
    monkeypatch.setattr(new_edition.time, "sleep", lambda s: None)
    return lambda **kw: new_edition.scaffold(
        "breaking", "Engineering Sample", "About shipping.", kw.pop("seed", []),
        use_llm=True, client=net, sleep=lambda s: None, **kw,
    )


# ------------------------------------------------------- the LLM draft path

def test_llm_draft_is_validated_and_the_result_passes_the_checker(run_llm, editions_dir, llm_stub):
    dest = run_llm()
    ed = edition.load("breaking", editions_dir)
    assert ed.status == "draft" and ed.name == "Engineering Sample"
    assert ed.order == 20  # after agents (10)
    assert ed.tagline.startswith("A daily digest")
    # bad regex and exact duplicate dropped; good ones kept in order
    assert ed.keywords == (r"\bagent(s)?\b", r"\bcode review\b")
    # lower-cased, kebab-cased, de-duplicated, blanks dropped
    assert ed.tags == ("code-review", "testing", "dev-tools")
    template = yaml.safe_load((editions_dir / "_template" / "edition.yaml").read_text())
    assert ed.section_rules() == template["sections"]
    assert check_edition.lint("breaking").ok
    for key, fname in new_edition.FRAGMENTS.items():
        assert (dest / "prompts" / fname).read_text() == f"fragment for {key}\n"
    assert not (dest / "prompts" / "README.md").exists()
    assert (dest / "REVIEW.md").is_file()


def test_llm_receives_the_description_seeds_and_the_agents_edition_as_example(run_llm, llm_stub):
    run_llm(seed=["https://good.test/feed"])
    call = llm_stub.calls[0]
    p = call["prompt"]
    assert call["name"] == "edition_draft" and call["schema"] is new_edition.DRAFT_SCHEMA
    assert "About shipping." in p and "Engineering Sample" in p
    assert "- https://good.test/feed" in p
    assert "AI Agents Daily" in p  # edition.yaml of the worked example
    assert "editions/agents/prompts/write.voice.md" in p
    assert "{{slug}}" not in p and "{{example_edition}}" not in p


def test_dead_feeds_queries_and_subreddits_are_commented_out_not_silently_dropped(run_llm, editions_dir):
    dest = run_llm()
    text = (dest / "sources.yaml").read_text()
    live = yaml.safe_load(text)
    assert [e["id"] for e in live["rss"]] == ["good", "good-2"]  # dead.test dropped
    assert live["rss"][0] == {"id": "good", "name": "Good Blog", "url": "https://good.test/feed",
                              "keyword_gate_bypass": True, "recency_days": 90}
    assert [e["id"] for e in live["arxiv"]] == ["se"]
    assert [e["id"] for e in live["hn"]] == ["hn-ai"]
    assert [e["id"] for e in live["reddit"]] == ["r-ok"]
    assert live["reddit"][0]["url"] == "https://www.reddit.com/r/programming/top.json?t=day"
    assert live["reddit"][0]["limit"] == 5 and "min_score" not in live["reddit"][0]
    # the failures are still visible, annotated with why
    assert "# DROPPED (fetch failed: HTTPStatusError" in text and "dead.test/feed" in text
    assert "# DROPPED (query returned no results)" in text
    assert "gone/top.json" in text
    # the file with its comments still parses and lints
    assert check_edition.lint("breaking").ok


def test_review_md_lists_everything_that_was_dropped(run_llm):
    dest = run_llm()
    review = (dest / "REVIEW.md").read_text()
    assert "Dropped during validation" in review
    assert "**keyword** `(unclosed`: does not compile" in review
    assert "**keyword** `\\bagent(s)?\\b`: duplicate" in review
    assert "**keyword** ` `: empty pattern" in review
    assert "**tag** `code-review`: duplicate of 'code-review'" in review
    assert "**tag** `  `: empty after normalisation" in review
    assert "**rss** `https://dead.test/feed`" in review
    assert "**arxiv** `cat:zz.ZZ`" in review
    assert "**reddit** `r/gone`" in review
    assert "**reddit** `not a sub!`: not a valid subreddit name" in review
    assert "**hn** `(empty)`" in review
    assert "SHOW_DRAFTS=1" in review


def test_a_clean_draft_says_nothing_was_dropped(editions_dir, net, llm_stub):
    clean = _proposal()
    clean["keywords"] = [r"\bship\b"]
    clean["tags"] = ["shipping"]
    clean["sources"] = {"rss": [], "arxiv": [], "hn": [], "reddit": []}
    llm_stub.state["response"] = clean
    dest = new_edition.scaffold("clean", "Clean", "d", [], use_llm=True, client=net, sleep=lambda s: None)
    assert "Nothing was dropped." in (dest / "REVIEW.md").read_text()
    sources = yaml.safe_load((dest / "sources.yaml").read_text())
    assert sources["rss"] == [] and sources["reddit"] == []


def test_seed_sources_the_llm_ignored_are_still_tried(editions_dir, net, llm_stub):
    p = _proposal()
    p["sources"] = {"rss": [], "arxiv": [], "hn": [], "reddit": []}
    llm_stub.state["response"] = p
    net.routes["deadseed.example"] = 500  # first: "seed.example" is a substring of it
    net.routes["seed.example"] = _atom(2)
    dest = new_edition.scaffold(
        "seeded", "Seeded", "d", ["https://seed.example/rss", "https://deadseed.example/rss"],
        use_llm=True, client=net, sleep=lambda s: None,
    )
    sources = yaml.safe_load((dest / "sources.yaml").read_text())
    assert [e["url"] for e in sources["rss"]] == ["https://seed.example/rss"]
    text = (dest / "sources.yaml").read_text()
    assert "deadseed.example" in text and "DROPPED" in text
    assert "https://seed.example/rss" in (dest / "REVIEW.md").read_text()


def test_a_seed_the_llm_already_used_is_not_added_twice(editions_dir, net, llm_stub):
    p = _proposal()
    p["sources"] = {"rss": [{"id": "g", "name": "G", "url": "https://good.test/feed/",
                             "keyword_gate_bypass": False, "recency_days": None}],
                    "arxiv": [], "hn": [], "reddit": []}
    llm_stub.state["response"] = p
    net.routes["good.test"] = _atom(1)
    dest = new_edition.scaffold("s2", "S2", "d", ["https://good.test/feed"], use_llm=True,
                                client=net, sleep=lambda s: None)
    assert len(yaml.safe_load((dest / "sources.yaml").read_text())["rss"]) == 1


def test_arxiv_requests_are_spaced(editions_dir, net, llm_stub):
    _routes(net)
    sleeps = []
    new_edition.scaffold("sp", "Sp", "d", [], use_llm=True, client=net, sleep=sleeps.append)
    assert sleeps == [new_edition.ARXIV_DELAY_S, new_edition.ARXIV_DELAY_S]  # 3 queries -> 2 gaps


def test_duplicate_ids_are_made_unique(editions_dir, net, llm_stub):
    _routes(net)
    dest = new_edition.scaffold("dd", "Dd", "d", [], use_llm=True, client=net, sleep=lambda s: None)
    text = (dest / "sources.yaml").read_text()
    assert "id: good-2" in text  # second 'good' (its feed was alive: dup.test)
    ids = [e["id"] for e in yaml.safe_load(text)["rss"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda p: p.pop("tagline"), "'tagline'"),
        (lambda p: p.update(reader_profile="  "), "'reader_profile'"),
        (lambda p: p.update(keywords="nope"), "'keywords'"),
        (lambda p: p.update(tags=[1]), "'tags'"),
        (lambda p: p.update(keywords=["  "]), "none of the LLM's keyword regexes compile"),
        (lambda p: p.update(prompts=[]), "'prompts' must be an object"),
        (lambda p: p["prompts"].pop("write_voice"), "'write_voice'"),
        (lambda p: p["prompts"].update(write_voice="has {{slot}} in it"), "contains '{{'"),
        (lambda p: p.update(sources=[]), "'sources' must be an object"),
        (lambda p: p["sources"].update(rss="x"), "sources.rss"),
        (lambda p: p.update(keywords=["(a", "(b"]), "none of the LLM's keyword regexes compile"),
        (lambda p: p.update(tags=["!!!"]), "no usable tags"),
    ],
)
def test_unusable_llm_output_is_rejected_before_anything_is_written(
    editions_dir, net, llm_stub, mutate, message
):
    bad = _proposal()
    mutate(bad)
    llm_stub.state["response"] = bad
    with pytest.raises(NewEditionError, match=message.replace("{{", r"\{\{")):
        new_edition.scaffold("nope", "Nope", "d", [], use_llm=True, client=net, sleep=lambda s: None)
    assert not (editions_dir / "nope").exists()


def test_non_object_llm_output_is_rejected(editions_dir, net, llm_stub):
    llm_stub.state["response"] = ["not", "an", "object"]
    with pytest.raises(NewEditionError, match="did not return a JSON object"):
        new_edition.scaffold("nope", "Nope", "d", [], use_llm=True, client=net)
    assert not (editions_dir / "nope").exists()


def test_a_failure_while_writing_leaves_no_half_written_edition(editions_dir, net, llm_stub, monkeypatch):
    _routes(net)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(new_edition, "render_review", boom)
    with pytest.raises(OSError):
        new_edition.scaffold("half", "Half", "d", [], use_llm=True, client=net, sleep=lambda s: None)
    assert not (editions_dir / "half").exists()


def test_model_and_limits_come_from_the_writer_env(editions_dir, net, llm_stub, monkeypatch):
    _routes(net)
    monkeypatch.setenv("WRITER_MODEL", "my-model")
    monkeypatch.setenv("WRITER_TIMEOUT_S", "99")
    monkeypatch.setenv("WRITER_MAX_TOKENS", "1234")
    new_edition.scaffold("env", "Env", "d", [], use_llm=True, client=net, sleep=lambda s: None)
    call = llm_stub.calls[0]
    assert (call["model"], call["timeout_s"], call["max_tokens"]) == ("my-model", 99, 1234)


def test_draft_schema_requires_every_fragment_and_source_family():
    props = new_edition.DRAFT_SCHEMA["properties"]
    assert set(props["prompts"]["required"]) == set(new_edition.FRAGMENTS)
    assert set(props["sources"]["required"]) == {"rss", "arxiv", "hn", "reddit"}
    assert set(new_edition.DRAFT_SCHEMA["required"]) == set(props)


def test_fragment_map_matches_the_template_folder():
    template = {p.name for p in (REAL / "_template" / "prompts").glob("*.md")} - {"README.md"}
    assert set(new_edition.FRAGMENTS.values()) == template


def test_meta_prompt_placeholders_are_all_supplied():
    import prompts as prompts_mod

    text = (prompts_mod.PROMPTS_DIR / "draft_edition.md").read_text()
    used = set(new_edition._PLACEHOLDER.findall(text))
    out = new_edition.build_prompt("s", "N", "D", [], edition.REPO_ROOT)
    assert used == {"slug", "name", "describe", "seed_sources", "example_edition",
                    "example_sources", "example_fragments"}
    assert not new_edition._PLACEHOLDER.search(out.replace("{{" "slot" "}}", ""))
    assert "(none)" in out


# ---------------------------------------------------------- source checks

def test_check_rss_reasons(net):
    net.routes.update({"ok.test": _atom(1), "empty.test": _atom(0), "html.test": b"<html>hi</html>",
                       "err.test": httpx.ConnectError("boom")})
    assert new_edition.check_rss(net, "https://ok.test/f") is None
    assert "no feed entries" in new_edition.check_rss(net, "https://empty.test/f")
    assert "no feed entries" in new_edition.check_rss(net, "https://html.test/f")
    assert "ConnectError" in new_edition.check_rss(net, "https://err.test/f")
    assert "404" in new_edition.check_rss(net, "https://missing.test/f")
    assert new_edition.check_rss(net, "file:///etc/passwd") == "not an http(s) URL"


def test_check_arxiv_reasons(net):
    net.routes["export.arxiv.org"] = _atom(1)
    assert new_edition.check_arxiv(net, "cat:cs.SE") is None
    net.routes["export.arxiv.org"] = _atom(0)
    assert new_edition.check_arxiv(net, "cat:zz") == "query returned no results"
    net.routes["export.arxiv.org"] = 503
    assert "query failed" in new_edition.check_arxiv(net, "cat:cs.SE")


def test_check_subreddit_reasons(net):
    net.routes["r/ok/"] = _atom(1)
    net.routes["r/quiet/"] = _atom(0)
    net.routes["r/html/"] = b"<html>"
    net.routes["r/err/"] = 403
    assert new_edition.check_subreddit(net, "https://x/r/ok/top.json?t=day") is None
    assert new_edition.check_subreddit(net, "https://x/r/ok/top/.rss") is None
    assert new_edition.check_subreddit(net, "https://x/r/quiet/top.json") is None
    assert new_edition.check_subreddit(net, "https://x/r/html/top.json") == "response was not a feed"
    assert "fetch failed" in new_edition.check_subreddit(net, "https://x/r/err/top.json")


def test_clean_helpers_directly():
    assert new_edition.clean_tags(["A b", "a-b", "C"])[0] == ["a-b", "c"]
    assert new_edition.slugify("Hello, World!") == "hello-world"
    kws, dropped = new_edition.clean_keywords(["ok", "(", "ok"])
    assert kws == ["ok"] and [d.reason.split(":")[0] for d in dropped] == ["does not compile", "duplicate"]


def test_source_defaults_are_sanitised(net):
    net.routes.update({"a.test": _atom(1), "export.arxiv.org": _atom(1),
                       "reddit.com": _atom(1)})
    out, dropped = new_edition.validate_sources(
        {"rss": [{"url": "https://a.test/f", "keyword_gate_bypass": "yes", "recency_days": -5}],
         "arxiv": [{"query": "cat:cs.SE", "max_results": True}],
         "hn": [{"query": "x", "min_points": "lots"}],
         "reddit": [{"subreddit": "python", "limit": None}]},
        [], net, sleep=lambda s: None,
    )
    assert dropped == []
    assert out["rss"] == [{"id": "a-test", "name": "https://a.test/f", "url": "https://a.test/f"}]
    assert out["arxiv"][0]["max_results"] == 30
    assert out["hn"][0]["min_points"] == 40 and out["hn"][0]["hours_back"] == 48
    assert out["reddit"][0]["limit"] == 10 and out["reddit"][0]["id"] == "reddit-python"


# ------------------------------------------------------------- --no-llm

def test_no_llm_scaffold_passes_the_checker(editions_dir):
    dest = new_edition.scaffold("fresh", "Fresh: Ideas", "", [], use_llm=False)
    ed = edition.load("fresh", editions_dir)
    assert (ed.slug, ed.name, ed.status, ed.order) == ("fresh", "Fresh: Ideas", "draft", 20)
    result = check_edition.lint("fresh")
    assert result.ok, result.errors
    assert any("TODO" in w for w in result.warnings)  # still a template: warned, not failed
    assert (dest / "REVIEW.md").is_file()
    assert "--no-llm" in (dest / "REVIEW.md").read_text()
    assert not (dest / "prompts" / "README.md").exists()
    assert sorted(p.name for p in (dest / "prompts").iterdir()) == sorted(new_edition.FRAGMENTS.values())


def test_no_llm_never_calls_the_llm(editions_dir, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("LLM called")

    monkeypatch.setattr(new_edition.llm, "call_llm", boom)
    new_edition.scaffold("nollm", "No LLM", "", [], use_llm=False)


def test_no_llm_seed_sources_are_validated(editions_dir, net):
    net.routes["alive.test"] = _atom(1)
    dest = new_edition.scaffold(
        "seeds", "Seeds", "", ["https://alive.test/feed", "https://dead.test/feed"],
        use_llm=False, client=net, sleep=lambda s: None,
    )
    text = (dest / "sources.yaml").read_text()
    assert yaml.safe_load(text)["rss"][0]["url"] == "https://alive.test/feed"
    assert "DROPPED" in text and "dead.test" in text
    assert check_edition.lint("seeds").ok


def test_order_follows_the_highest_existing_edition(editions_dir):
    new_edition.scaffold("second", "Second", "", [], use_llm=False)
    new_edition.scaffold("third", "Third", "", [], use_llm=False)
    assert edition.load("second", editions_dir).order == 20
    assert edition.load("third", editions_dir).order == 30


def test_order_ignores_broken_and_template_folders(editions_dir):
    (editions_dir / "broken").mkdir()
    (editions_dir / "broken" / "edition.yaml").write_text("slug: broken\n")
    (editions_dir / "stray").mkdir()
    assert new_edition._next_order(editions_dir) == 20
    empty = editions_dir.parent / "empty"
    empty.mkdir()
    assert new_edition._next_order(empty) == 10


# ----------------------------------------------------------- bad usage

@pytest.mark.parametrize(
    "slug, name, describe, seeds, llm, message",
    [
        ("Bad Slug", "n", "d", [], False, "invalid slug"),
        ("_hidden", "n", "d", [], False, "invalid slug"),
        ("ok", "  ", "d", [], False, "--name"),
        ("ok", "n", " ", [], True, "--describe is required"),
        ("ok", "n", "d", ["ftp://x"], False, "http"),
        ("agents", "n", "d", [], False, "already exists"),
    ],
)
def test_bad_arguments_are_rejected(editions_dir, slug, name, describe, seeds, llm, message):
    before = sorted(p.name for p in editions_dir.iterdir())
    with pytest.raises(NewEditionError, match=message):
        new_edition.scaffold(slug, name, describe, seeds, use_llm=llm)
    assert sorted(p.name for p in editions_dir.iterdir()) == before


def test_missing_template_is_reported(editions_dir):
    shutil.rmtree(editions_dir / "_template")
    with pytest.raises(NewEditionError, match="does not exist"):
        new_edition.scaffold("x", "X", "", [], use_llm=False)


# ------------------------------------------------------------------ CLI

def test_main_scaffolds_and_runs_the_checker(editions_dir, capsys):
    assert new_edition.main(["cli-ed", "--name", "Cli Ed", "--no-llm"]) == 0
    out = capsys.readouterr().out
    assert "created" in out and "lint ok" in out and "REVIEW.md" in out


def test_main_reports_usage_errors_without_a_traceback(editions_dir, capsys):
    assert new_edition.main(["agents", "--name", "Dup", "--no-llm"]) == 2
    assert "already exists" in capsys.readouterr().err


def test_main_requires_describe_unless_no_llm(editions_dir, capsys):
    assert new_edition.main(["cli-ed", "--name", "Cli Ed"]) == 2
    assert "--describe" in capsys.readouterr().err
    assert not (editions_dir / "cli-ed").exists()


def test_main_returns_the_checker_status(editions_dir, monkeypatch, capsys):
    monkeypatch.setattr(new_edition.check_edition, "check", lambda slug: 1)
    assert new_edition.main(["cli-ed", "--name", "Cli Ed", "--no-llm"]) == 1


def test_scaffold_creates_its_own_http_client_when_none_given(editions_dir, monkeypatch):
    made = []
    real = httpx.Client

    class Spy(real):
        def __init__(self, *a, **k):
            made.append(self)
            super().__init__(*a, **k)

    monkeypatch.setattr(new_edition.httpx, "Client", Spy)
    new_edition.scaffold("own", "Own", "", [], use_llm=False)
    assert len(made) == 1 and made[0].is_closed
