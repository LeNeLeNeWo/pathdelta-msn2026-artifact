import json

from experiments.msn2026_v8_day2_agent.agent_benchmark import METHODS, parse_edit_response


def test_all_required_llm_editing_methods_exist():
    assert set(METHODS) == set("ABCDEFG")
    assert all(spec.envelope_source in {"none", "inferred", "oracle"} for spec in METHODS.values())


def test_edit_response_contains_model_owned_exact_edit():
    edits, payload = parse_edit_response(
        json.dumps({"edits": [{"device": "edge", "old_text": "old", "new_text": "new"}], "summary": "x"})
    )
    assert edits[0].old_text == "old"
    assert edits[0].new_text == "new"

