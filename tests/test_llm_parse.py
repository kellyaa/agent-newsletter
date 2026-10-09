from llm import _last_json_object

R = ["rankings"]


def test_self_correction_takes_last_object():
    # 2026-10-08 blogs ranker: object, prose, corrected fenced object.
    raw = (
        '{"rankings": [{"id": "a", "tags": ["blogging"]}]}\n\n'
        "I notice one tag is not in the closed vocabulary. Correcting:\n\n"
        '```json\n{"rankings": [{"id": "a", "tags": ["writing"]}]}\n```'
    )
    assert _last_json_object(raw, R) == {"rankings": [{"id": "a", "tags": ["writing"]}]}


def test_truncated_second_object_falls_back_to_first():
    raw = '{"rankings": []}\nCorrecting:\n{"rankings": [{"id": "a", "tags": []}'
    assert _last_json_object(raw, R) == {"rankings": []}


def test_nested_entry_is_not_mistaken_for_the_answer():
    assert _last_json_object('{"rankings": [{"id": "a"}', R) is None


def test_no_json():
    assert _last_json_object("no json here", R) is None
