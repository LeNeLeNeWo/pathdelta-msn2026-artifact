import json
from pathlib import Path

import jsonschema
import pytest

from experiments.msn2026_v8_day2_agent.counterexample_feedback import (
    assert_feedback_is_patch_free,
    build_counterexample_feedback,
)


def _evaluation():
    return {
        "transaction_error": None,
        "syntax": {"status": "PASS", "files": []},
        "contract_pass": {"write_scope": True},
        "report": {
            "compliance": {
                "goal_success": True,
                "target_failures": [],
                "semantic_frame_preserved": False,
                "frame_failures": ["frame-1"],
                "dependency_frame_preserved": False,
                "hard_footprint_preserved": True,
                "footprint_failures": [],
            },
            "semantic": {
                "non_target_attribute_changes": ["edge|peer2|pfx::path"],
                "missing_post_behaviors": [],
                "protected_dependency_violations": ["edge:route_map:SHARED"],
            },
            "structural": {},
            "textual": {},
        },
    }


def test_feedback_contains_counterexamples_but_no_patch_or_strategy():
    feedback = build_counterexample_feedback("full_envelope", _evaluation())
    assert [row["type"] for row in feedback["counterexamples"]] == ["path_relation", "protected_dependency"]
    serialized = json.dumps(feedback).upper()
    assert "NEW_TEXT" not in serialized
    assert "REBIND" not in serialized
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "experiments/msn2026_v8_day2_agent/counterexample_feedback.schema.json").read_text()
    )
    jsonschema.validate(feedback, schema)


def test_guard_rejects_hidden_renderer_or_strategy():
    with pytest.raises(ValueError):
        assert_feedback_is_patch_free({"correct_patch": "x"})
    with pytest.raises(ValueError):
        assert_feedback_is_patch_free({"observed": "please rebind the peer"})

