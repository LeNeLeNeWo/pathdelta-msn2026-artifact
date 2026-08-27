import json
from pathlib import Path

from experiments.msn2026_v8_day2_agent.change_envelope_v2 import (
    BehaviorRecord,
    build_frr_dependency_graph,
    derive_change_envelope_v2,
    handwritten_special_case_audit,
)


def test_dependency_graph_fingerprint_detects_value_only_object_edit():
    before = build_frr_dependency_graph(
        {
            "r1": "route-map RM_SHARED permit 10\n set local-preference 100\n"
            "router bgp 65000\n neighbor 192.0.2.1 route-map RM_SHARED in\n"
        }
    )
    after = build_frr_dependency_graph(
        {
            "r1": "route-map RM_SHARED permit 10\n set local-preference 250\n"
            "router bgp 65000\n neighbor 192.0.2.1 route-map RM_SHARED in\n"
        }
    )
    node = "r1:route_map:RM_SHARED"
    assert before.nodes[node].line_count == after.nodes[node].line_count
    assert before.nodes[node].definition_sha256 != after.nodes[node].definition_sha256
from tools.build_msn2026_v8_day2_dev import BASELINE_TEMPLATE


def _behaviors():
    rows = []
    for neighbor in ("198.51.100.1", "198.51.100.2"):
        for prefix, local_pref in (("10.1.0.0/24", 150), ("203.0.113.0/24", 100)):
            rows.append(
                BehaviorRecord(
                    behavior_id=f"edge-1|{neighbor}|{prefix}",
                    device="edge-1",
                    subject=neighbor,
                    fec=prefix,
                    attributes={"decision": "permit", "local_pref": local_pref, "session": "established"},
                    source="unit-independent-prestate",
                )
            )
    return rows


def _intent():
    return {
        "intent_id": "prefer-one-partner",
        "raw_text": "On edge-1 set local_pref 250 for 203.0.113.0/24 learned from 198.51.100.1",
        "selector": {
            "devices": ["edge-1"],
            "subjects": ["198.51.100.1"],
            "fecs": ["203.0.113.0/24"],
        },
        "changes": [{"dimension": "local_pref", "relation": "replace", "desired": 250}],
    }


def _envelope():
    configs = {"edge-1": BASELINE_TEMPLATE}
    return derive_change_envelope_v2(
        _intent(),
        configs,
        _behaviors(),
        build_frr_dependency_graph(configs),
        behavior_universe_provenance={"backend": "unit-fixture", "complete": True},
    )


def test_target_is_selected_without_turning_other_target_attributes_mutable():
    envelope = _envelope()
    assert len(envelope.target_delta) == 1
    assert envelope.target_delta[0].behavior_id == "edge-1|198.51.100.1|203.0.113.0/24"
    assert envelope.target_delta[0].desired == 250
    framed = {(item.behavior_id, item.dimension) for item in envelope.semantic_frame}
    assert ("edge-1|198.51.100.1|203.0.113.0/24", "session") in framed
    assert ("edge-1|198.51.100.2|203.0.113.0/24", "local_pref") in framed


def test_shared_dependency_is_inferred_by_target_non_target_closure_intersection():
    envelope = _envelope()
    assert "edge-1:route_map:RM_EDGE_IN" in envelope.protected_dependencies
    assert "edge-1:prefix_list:PL_ALL" in envelope.protected_dependencies
    assert envelope.provenance["patch_strategy_emitted"] is False
    assert envelope.provenance["expected_patch_used"] is False


def test_conformance_is_soft_and_separate_from_footprint():
    envelope = _envelope()
    assert "route_map" in envelope.conformance_preferences.naming_families
    payload = envelope.to_dict()
    assert "conformance_preferences" not in payload["footprint_budget"]


def test_schema_validates_derived_envelope():
    import jsonschema

    root = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (root / "experiments/msn2026_v8_day2_agent/change_envelope.schema.json").read_text()
    )
    jsonschema.validate(_envelope().to_dict(), schema)


def test_no_intent_family_or_expected_patch_special_cases_are_disclosed():
    audit = handwritten_special_case_audit()
    assert audit["intent_family_branches"] == 0
    assert audit["expected_patch_templates"] == 0
    assert audit["strategy_labels_emitted"] == 0


def test_frr_graph_supports_call_continue_and_community_dependencies():
    config = BASELINE_TEMPLATE.replace(
        "route-map RM_EDGE_IN permit 20",
        "bgp community-list standard CL_BLUE permit 65001:10\n"
        "route-map RM_CHILD permit 10\n"
        " match community CL_BLUE\n"
        " set local-preference 175\n"
        "route-map RM_EDGE_IN permit 20\n"
        " call RM_CHILD\n"
        " on-match next",
    )
    graph = build_frr_dependency_graph({"edge-1": config})
    assert "edge-1:route_map:RM_CHILD" in graph.edges["edge-1:route_map:RM_EDGE_IN"]
    assert "edge-1:community_list:CL_BLUE" in graph.edges["edge-1:route_map:RM_CHILD"]


def test_target_exclusive_policy_is_not_hard_protected_by_same_subject_frame():
    config = BASELINE_TEMPLATE.replace(
        " neighbor 198.51.100.2 route-map RM_EDGE_IN in",
        " neighbor 198.51.100.2 route-map RM_EDGE_B_IN in",
    ).replace(
        "route-map RM_EDGE_IN permit 10",
        "route-map RM_EDGE_B_IN permit 10\n"
        " match ip address prefix-list PL_CUSTOMER\n"
        " set local-preference 150\n"
        "route-map RM_EDGE_B_IN permit 20\n"
        " match ip address prefix-list PL_ALL\n"
        " set local-preference 100\n"
        "route-map RM_EDGE_IN permit 10",
    )
    envelope = derive_change_envelope_v2(
        _intent(),
        {"edge-1": config},
        _behaviors(),
        build_frr_dependency_graph({"edge-1": config}),
        behavior_universe_provenance={"backend": "unit", "complete": True},
    )
    assert "edge-1:route_map:RM_EDGE_IN" not in envelope.protected_dependencies
    assert "edge-1:prefix_list:PL_CUSTOMER" in envelope.protected_dependencies
