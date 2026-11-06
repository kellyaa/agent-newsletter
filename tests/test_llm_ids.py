"""llm_ids: short per-call aliases for the ids an LLM echoes back (#586)."""
from __future__ import annotations

import logging
from unittest.mock import patch

import llm_ids
from llm_ids import alias_items, resolve_ids

H1, H2, H3 = "a" * 64, "b" * 64, "c" * 64


def _items():
    return [{"id": H1, "title": "one"}, {"id": H2, "title": "two"}, {"id": H3, "title": "three"}]


def _entry(i, **kw):
    return {"id": i, "score": 5, **kw}


class TestAliasItems:
    def test_numbers_in_input_order_and_maps_back(self):
        aliased, mapping = alias_items(_items())
        assert [it["id"] for it in aliased] == ["i1", "i2", "i3"]
        assert mapping == {"i1": H1, "i2": H2, "i3": H3}

    def test_keeps_other_fields_and_does_not_mutate_input(self):
        items = _items()
        aliased, _ = alias_items(items)
        assert [it["title"] for it in aliased] == ["one", "two", "three"]
        assert items[0]["id"] == H1

    def test_empty(self):
        assert alias_items([]) == ([], {})

    def test_aliases_are_short(self):
        aliased, _ = alias_items(_items())
        assert all(len(it["id"]) < 8 for it in aliased)


class TestResolveIds:
    def setup_method(self):
        _, self.m = alias_items(_items())

    def test_maps_aliases_to_real_ids_keeping_fields_and_order(self):
        out, unknown = resolve_ids([_entry("i3", why="x"), _entry("i1")], self.m)
        assert [e["id"] for e in out] == [H3, H1]
        assert out[0]["why"] == "x" and out[0]["score"] == 5
        assert unknown == []

    def test_case_and_whitespace_are_ignored(self):
        out, unknown = resolve_ids([_entry(" I2 ")], self.m)
        assert out[0]["id"] == H2 and unknown == []

    def test_real_id_that_was_sent_still_resolves(self):
        out, unknown = resolve_ids([_entry(H2)], self.m)
        assert out[0]["id"] == H2 and unknown == []

    def test_unknown_ids_are_reported_and_left_out(self):
        out, unknown = resolve_ids([_entry("i1"), _entry("i9"), _entry("a" * 62 + "bb")], self.m)
        assert [e["id"] for e in out] == [H1]
        assert unknown == ["i9", "a" * 62 + "bb"]

    def test_dup_suffix_is_stripped_with_a_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="llm_ids"):
            out, unknown = resolve_ids([_entry("i1"), _entry("i2_dup")], self.m)
        assert [e["id"] for e in out] == [H1, H2] and unknown == []
        assert any("_dup" in r.message for r in caplog.records)

    def test_dup_suffix_on_a_real_id_is_stripped(self):
        out, unknown = resolve_ids([_entry(H1 + "_dup")], self.m)
        assert out[0]["id"] == H1 and unknown == []

    def test_dup_not_stripped_when_the_base_entry_is_present(self):
        out, unknown = resolve_ids([_entry("i1"), _entry("i1_dup")], self.m)
        assert [e["id"] for e in out] == [H1]
        assert unknown == ["i1_dup"]

    def test_dup_not_stripped_when_base_is_present_later_in_the_list(self):
        out, unknown = resolve_ids([_entry("i1_dup"), _entry("i1")], self.m)
        assert [e["id"] for e in out] == [H1]
        assert unknown == ["i1_dup"]

    def test_two_dups_of_one_base_resolve_only_the_first(self):
        out, unknown = resolve_ids([_entry("i1_dup"), _entry("i1_dup")], self.m)
        assert [e["id"] for e in out] == [H1]
        assert unknown == ["i1_dup"]

    def test_dup_of_an_unknown_base_is_unknown(self):
        out, unknown = resolve_ids([_entry("i7_dup")], self.m)
        assert out == [] and unknown == ["i7_dup"]

    def test_non_dict_and_non_string_ids_pass_through_untouched(self):
        junk = ["str", None, {"id": 3}, {"score": 1}]
        out, unknown = resolve_ids(junk, self.m)
        assert out == junk and unknown == []

    def test_empty(self):
        assert resolve_ids([], self.m) == ([], [])

    def test_duplicate_real_ids_in_the_input_map_to_the_first_alias_owner(self):
        # Two items sharing a real id (should not happen) must not crash.
        _, m = alias_items([{"id": H1}, {"id": H1}])
        out, _ = resolve_ids([_entry("i2")], m)
        assert out[0]["id"] == H1


# ---------- wiring in the two stages ----------

class TestRankSectionUsesAliases:
    def test_typo_in_an_alias_is_dropped_but_a_good_alias_scores(self):
        from rank import rank_section

        items = [{"id": H1, "title": "one"}, {"id": H2, "title": "two"}]
        got = [{"id": "i1", "score": 9, "tags": [], "why": "ok"},
               {"id": "i2_dup", "score": 8, "tags": [], "why": "ok"}]
        with patch("rank.invoke_ranker", return_value=got):
            out = rank_section("papers", items, "rubric")
        assert {e["id"]: e["score"] for e in out} == {H1: 9, H2: 8}

    def test_prompt_carries_aliases_not_real_ids(self):
        from rank import rank_section

        seen = {}

        def fake(prompt, label):
            seen["p"] = prompt
            return []

        with patch("rank.invoke_ranker", side_effect=fake):
            rank_section("papers", [{"id": H1, "title": "one"}], "rubric")
        assert '"id": "i1"' in seen["p"] and H1 not in seen["p"]

    def test_unknown_ranker_id_is_logged_and_the_item_falls_back(self, caplog):
        from rank import rank_section

        got = [{"id": "zzz", "score": 9, "tags": [], "why": "ok"}]
        with patch("rank.invoke_ranker", return_value=got):
            with caplog.at_level(logging.WARNING, logger="rank"):
                out = rank_section("papers", [{"id": H1, "title": "one"}], "rubric")
        assert out == []
        assert any("not sent" in r.message and "zzz" in r.message for r in caplog.records)


class TestWriterUsesAliases:
    def test_call_writer_aliases_in_and_resolves_out(self):
        import write

        featured = [
            {"id": H1, "section": "papers", "source": "s", "url": "https://x.test/1",
             "title": "one", "author": None, "raw_text": "r", "score": 8, "tags": [], "why": "w"},
            {"id": H2, "section": "news", "source": "s", "url": "https://x.test/2",
             "title": "two", "author": None, "raw_text": "r", "score": 7, "tags": [], "why": "w"},
        ]
        seen = {}

        def fake(prompt):
            seen["p"] = prompt
            return {"theme": "t", "items": [
                {"id": "i1", "summary": "S1"}, {"id": "i2_dup", "summary": "S2"},
                {"id": "i9", "summary": "stray"}]}

        with patch("write.invoke_writer", side_effect=fake):
            out = write.call_writer(
                "2026-06-01", featured, {"papers": [], "news": [], "blogs": []},
                {}, None, "rubric")
        assert '"id": "i1"' in seen["p"] and H1 not in seen["p"]
        assert [(e["id"], e["summary"]) for e in out["items"]] == [(H1, "S1"), (H2, "S2")]
        assert out["theme"] == "t"

    def test_resolve_writer_output_leaves_malformed_responses_alone(self):
        import write

        for bad in (None, "x", {"items": None}, {"theme": "t"}):
            assert write.resolve_writer_output(bad, {"i1": H1}) is bad
