"""scripts/check_edition.py: edition lint and the no-LLM fetch/prefilter dry run."""
import io
import shutil
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import yaml

import check_edition
import edition

REAL = edition.EDITIONS_DIR


@pytest.fixture()
def editions_dir(tmp_path, monkeypatch):
    """A copy of the real editions/ tree that tests may freely edit."""
    d = tmp_path / "editions"
    shutil.copytree(REAL, d)
    monkeypatch.setattr(edition, "EDITIONS_DIR", d)
    return d


def _clone_agents(editions_dir, slug="demo", status="draft"):
    dest = editions_dir / slug
    shutil.copytree(editions_dir / "agents", dest)
    data = yaml.safe_load((dest / "edition.yaml").read_text())
    data["slug"], data["status"] = slug, status
    (dest / "edition.yaml").write_text(yaml.safe_dump(data))
    return dest


def test_the_real_agents_edition_lints_clean():
    result = check_edition.lint("agents")
    assert result.ok and not result.warnings, (result.errors, result.warnings)


def test_unknown_edition_is_an_error():
    result = check_edition.lint("no-such-edition")
    assert not result.ok and "unknown edition" in result.errors[0]


def test_template_folder_is_not_itself_a_valid_edition():
    # Its slug is a placeholder on purpose; new_edition.py rewrites it.
    assert not check_edition.lint("_template").ok


def test_invalid_edition_config_is_reported(editions_dir):
    dest = _clone_agents(editions_dir)
    (dest / "edition.yaml").write_text("slug: demo\n")
    result = check_edition.lint("demo")
    assert any("missing keys" in e for e in result.errors)


def test_missing_prompt_fragment_is_reported(editions_dir):
    dest = _clone_agents(editions_dir)
    (dest / "prompts" / "rank.relevance.md").unlink()
    result = check_edition.lint("demo")
    assert any("no value for slot {{relevance}}" in e for e in result.errors)


def test_unfilled_placeholder_in_rendered_prompt_is_reported(editions_dir):
    dest = _clone_agents(editions_dir)
    # A fragment value is inserted verbatim, so a stray slot name survives rendering.
    (dest / "prompts" / "rank.subject.md").write_text("{{oops}}\n")
    result = check_edition.lint("demo")
    assert any("unfilled placeholders ['{{oops}}']" in e for e in result.errors)


def test_sources_file_missing_or_malformed(editions_dir):
    dest = _clone_agents(editions_dir)
    (dest / "sources.yaml").unlink()
    assert any("missing" in e for e in check_edition.lint("demo").errors)
    (dest / "sources.yaml").write_text("rss: [unclosed")
    assert any("invalid YAML" in e for e in check_edition.lint("demo").errors)
    (dest / "sources.yaml").write_text("- a\n- b\n")
    assert any("must be a mapping" in e for e in check_edition.lint("demo").errors)


@pytest.mark.parametrize(
    "sources, fragment",
    [
        ({"rss": [{"id": "a"}]}, "missing ['url']"),
        ({"rss": [{"id": "a", "url": "u"}, {"id": "a", "url": "v"}]}, "duplicate id 'a'"),
        ({"rss": "nope"}, "'rss' must be a list"),
        ({"rss": ["nope"]}, "must be a mapping"),
        ({"bogus": []}, "unknown source families"),
        ({"rss": [{"id": "a", "url": "u", "section": "videos"}]}, "section 'videos'"),
        ({"rss": [{"id": "a", "url": "u", "keyword_gate_bypass": "yes"}]}, "keyword_gate_bypass"),
        ({"rss": [{"id": "a", "url": "u", "recency_days": 0}]}, "recency_days"),
        ({"rss": [{"id": "a", "url": "u", "recency_days": True}]}, "recency_days"),
    ],
)
def test_source_entry_validation(editions_dir, sources, fragment):
    dest = _clone_agents(editions_dir)
    (dest / "sources.yaml").write_text(yaml.safe_dump(sources))
    errors = check_edition.lint("demo").errors
    assert any(fragment in e for e in errors), errors


def test_empty_sources_file_warns_for_draft_and_fails_for_live(editions_dir):
    dest = _clone_agents(editions_dir, status="draft")
    (dest / "sources.yaml").write_text("# nothing yet\n")
    draft = check_edition.lint("demo")
    assert draft.ok and any("no active sources" in w for w in draft.warnings)

    live = _clone_agents(editions_dir, slug="demo-live", status="live")
    (live / "sources.yaml").write_text("rss: []\n")
    assert any("no active sources" in e for e in check_edition.lint("demo-live").errors)


def test_todo_placeholders_warn_for_draft_and_fail_for_live(editions_dir):
    dest = _clone_agents(editions_dir, status="draft")
    (dest / "prompts" / "write.voice.md").write_text("line one\nTODO: finish\n")
    draft = check_edition.lint("demo")
    assert draft.ok and any("write.voice.md:2" in w for w in draft.warnings)

    live = _clone_agents(editions_dir, slug="demo-live", status="live")
    (live / "prompts" / "write.voice.md").write_text("TODO: finish\n")
    assert any("TODO placeholders" in e for e in check_edition.lint("demo-live").errors)


def test_todo_in_a_yaml_comment_is_ignored(editions_dir):
    dest = _clone_agents(editions_dir)
    (dest / "sources.yaml").write_text(
        "# TODO nothing here\nrss:\n  - id: a\n    url: https://x.test/f\n"
    )
    assert check_edition.lint("demo").warnings == []


# ------------------------------------------------------------------ dry run

def _atom(*entries):
    items = "".join(
        f"<entry><title>{t}</title><link href='{u}'/><id>{u}</id>"
        f"<published>{p}</published><summary>{s}</summary></entry>"
        for t, u, p, s in entries
    )
    return f"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>{items}</feed>"


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def fake_network(monkeypatch):
    """Route fetch's HTTP client to canned responses keyed by URL substring."""
    import fetch

    routes = {}

    def handler(request: httpx.Request) -> httpx.Response:
        for needle, resp in routes.items():
            if needle in str(request.url):
                if isinstance(resp, Exception):
                    raise resp
                return httpx.Response(200, content=resp)
        return httpx.Response(404)

    monkeypatch.setattr(
        fetch, "_make_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    return routes


def test_dry_run_counts_each_gate(editions_dir, fake_network):
    dest = _clone_agents(editions_dir)
    data = yaml.safe_load((dest / "edition.yaml").read_text())
    data["keywords"] = [r"\bkubernetes\b"]
    (dest / "edition.yaml").write_text(yaml.safe_dump(data))
    (dest / "sources.yaml").write_text(yaml.safe_dump({
        "rss": [
            {"id": "gated", "url": "https://gated.test/feed"},
            {"id": "bypass", "url": "https://bypass.test/feed", "keyword_gate_bypass": True},
            {"id": "dead", "url": "https://dead.test/feed"},
            {"id": "boom", "url": "https://boom.test/feed"},
        ],
        "arxiv": [
            {"id": "a1", "query": "cat:cs.SE"},
            {"id": "a2", "query": "cat:cs.AI"},
        ],
    }))
    fake_network["gated.test"] = _atom(
        ("Kubernetes operators", "https://gated.test/1", _iso(1), "x"),
        ("Cooking tips", "https://gated.test/2", _iso(1), "nothing relevant"),
        ("Kubernetes, old", "https://gated.test/3", _iso(400), "stale"),
    )
    fake_network["bypass.test"] = _atom(("Cooking tips", "https://bypass.test/1", _iso(1), "x"))
    fake_network["boom.test"] = httpx.ConnectError("no route")
    fake_network["export.arxiv.org"] = _atom(
        ("A kubernetes paper", "https://arxiv.org/abs/1", _iso(1), "x")
    )

    result = check_edition.lint("demo")
    counts = {c.label: c for c in check_edition.dry_run(result.edition, result.sources)}

    assert (counts["rss/gated"].fetched, counts["rss/gated"].after_recency,
            counts["rss/gated"].after_keyword) == (3, 2, 1)
    assert counts["rss/gated"].sections == {"blogs": 1}
    assert counts["rss/bypass"].after_keyword == 1  # keyword gate bypassed
    assert counts["rss/dead"].error and "404" in counts["rss/dead"].error
    assert counts["rss/boom"].error and "ConnectError" in counts["rss/boom"].error
    assert counts["arxiv/a1"].sections == {"papers": 1}
    assert counts["arxiv/a2"].sections == {"papers": 1}


def test_dry_run_is_isolated_from_real_state_and_environment(editions_dir, fake_network, monkeypatch):
    import os

    import db

    dest = _clone_agents(editions_dir)
    (dest / "sources.yaml").write_text("rss:\n  - id: s\n    url: https://s.test/feed\n")
    fake_network["s.test"] = _atom(("An agent", "https://s.test/1", _iso(1), "x"))
    result = check_edition.lint("demo")
    before_db = db.DB_PATH.exists()
    monkeypatch.setenv("EDITION", "agents")
    check_edition.dry_run(result.edition, result.sources)
    assert os.environ["EDITION"] == "agents"  # restored
    assert db.DB_PATH.exists() == before_db  # real state.db untouched


def test_dry_run_unsets_edition_it_set_itself(editions_dir, fake_network, monkeypatch):
    import os

    dest = _clone_agents(editions_dir)
    (dest / "sources.yaml").write_text("rss: []\n")
    monkeypatch.delenv("EDITION", raising=False)
    result = check_edition.lint("demo")
    assert check_edition.dry_run(result.edition, result.sources) == []
    assert "EDITION" not in os.environ


def test_format_table_shapes():
    ok = check_edition.SourceCount("rss/a", 5, 4, 3)
    ok.sections.update({"blogs": 3})
    bad = check_edition.SourceCount("rss/b", error="HTTPStatusError: 404")
    text = check_edition.format_table([ok, bad])
    assert "rss/a" in text and "blogs:3" in text
    assert "ERROR: HTTPStatusError: 404" in text
    assert "1 source(s) failed to fetch: rss/b" in text
    total = [l for l in text.splitlines() if l.startswith("total")][0]
    assert total.split()[1:4] == ["5", "4", "3"]
    assert "-" in check_edition.format_table([check_edition.SourceCount("rss/c")]).splitlines()[2]


# ---------------------------------------------------------------------- CLI

def test_check_stops_after_a_failed_lint(editions_dir):
    out = io.StringIO()
    assert check_edition.check("no-such-edition", out) == 1
    assert "lint FAILED" in out.getvalue()


def test_check_reports_when_there_is_nothing_to_fetch(editions_dir, fake_network):
    dest = _clone_agents(editions_dir)
    (dest / "sources.yaml").write_text("rss: []\n")
    out = io.StringIO()
    assert check_edition.check("demo", out) == 0
    assert "no sources configured" in out.getvalue()


def test_check_prints_the_table(editions_dir, fake_network):
    dest = _clone_agents(editions_dir)
    (dest / "sources.yaml").write_text("rss:\n  - id: s\n    url: https://s.test/feed\n")
    fake_network["s.test"] = _atom(("An agent", "https://s.test/1", _iso(1), "x"))
    out = io.StringIO()
    assert check_edition.check("demo", out) == 0
    assert "rss/s" in out.getvalue() and "total" in out.getvalue()


def test_check_warnings_are_printed(editions_dir, fake_network):
    dest = _clone_agents(editions_dir)
    (dest / "sources.yaml").write_text("rss: []\n")
    (dest / "prompts" / "write.voice.md").write_text("TODO\n")
    out = io.StringIO()
    check_edition.check("demo", out)
    assert "WARN" in out.getvalue() and "lint ok (2 warning(s))" in out.getvalue()


def test_check_defaults_to_stdout(editions_dir, capsys):
    assert check_edition.check("no-such-edition") == 1
    assert "lint FAILED" in capsys.readouterr().out


def test_main_parses_the_slug(editions_dir, capsys):
    assert check_edition.main(["no-such-edition"]) == 1
    assert "no-such-edition" in capsys.readouterr().out
