from experiments.msn2026_v8_day2_agent.change_envelope_v2 import BehaviorRecord
from experiments.msn2026_v84_agent_repair.run_agent128_repair_v2 import (
    detailed_feedback,
    policy_context,
)
from experiments.msn2026_v84_agent_repair.run_union_repair import (
    counterexample,
    route_values,
)


BASELINE = """set system host-name r1
set protocols bgp group ISP import IMPORT
set policy-options policy-statement IMPORT term BASELINE then local-preference 200
set policy-options policy-statement IMPORT term BASELINE then accept
"""


def test_policy_context_preserves_prechange_term_order():
    context = policy_context({"r1": BASELINE}, "r1", "ISP@import")
    assert context["applicable"] is True
    assert context["bound_policy"] == "IMPORT"
    assert [term["term"] for term in context["ordered_terms"]] == ["BASELINE"]
    assert context["ordered_terms"][0]["set_local_pref"] == 200


def test_detailed_feedback_carries_values_but_no_patch():
    candidate = BASELINE.replace("local-preference 200", "local-preference 250")
    target = BehaviorRecord("r1|ISP@import|198.18.1.0/24", "r1", "ISP@import", "198.18.1.0/24",
                            {"decision": "permit", "local_pref": 200, "communities": [], "session": "established"}, "pre")
    collateral = BehaviorRecord("r1|ISP@import|203.0.113.0/24", "r1", "ISP@import", "203.0.113.0/24",
                                {"decision": "permit", "local_pref": 200, "communities": [], "session": "established"}, "pre")
    evaluation = {
        "candidate_configs": {"r1": candidate},
        "syntax": {"status": "PASS"},
        "reports": {"full": {
            "compliance": {"goal_success": True, "semantic_frame_preserved": False,
                           "frame_failures": ["frame-1"], "dependency_frame_preserved": True,
                           "hard_footprint_preserved": True},
            "semantic": {"non_target_attribute_changes": ["r1|ISP@import|203.0.113.0/24::local_pref"]},
        }},
        "target_observations": [{"behavior_id": target.behavior_id, "dimension": "local_pref",
                                 "observed": 250, "required": 250}],
    }
    feedback = detailed_feedback("full_envelope", evaluation, [target, collateral], {"r1": BASELINE}, "ISP@import")
    example = feedback["counterexamples"][0]["examples"][0]
    assert example["pre_change"] == 200
    assert example["candidate_observed"] == 250
    serialized = str(feedback).lower()
    assert "old_text" not in serialized
    assert "new_text" not in serialized
    assert feedback["patch_disclosed"] is False
    assert feedback["strategy_disclosed"] is False


def test_union_counterexample_reports_impacts_without_commands():
    reference = "BgpRoute(localPreference=200, metric=200, originType='igp', communities=['65535:1'])"
    candidate = "BgpRoute(localPreference=250, metric=0, originType='egp', communities=['65535:1'])"
    audit = {
        "target_prefix": "198.18.7.0/24",
        "symbolic_frame_verdict": "FAIL",
        "unauthorized_target_fields": ["metric", "originType"],
        "symbolic_difference_networks": ["198.18.7.0/24"],
        "symbolic_difference": {"rows": [{
            "Input_Route": "BgpRoute(network='198.18.7.0/24')",
            "Reference_Output_Route": reference,
            "Snapshot_Output_Route": candidate,
        }]},
    }
    values = route_values(reference)
    assert values["metric"] == 200
    assert values["originType"] == "igp"
    feedback = counterexample(audit)
    assert feedback["target_examples"][0]["candidate_output"]["metric"] == 0
    assert feedback["recommended_object"] is None
    assert feedback["recommended_commands"] is None
    assert feedback["patch_disclosed"] is False
