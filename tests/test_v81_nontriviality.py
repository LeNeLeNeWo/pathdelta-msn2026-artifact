from tools.build_msn2026_v81_nontriviality import cases
from experiments.msn2026_v8_day2_agent.change_envelope_v2 import build_frr_dependency_graph


def test_value_only_shared_edit_changes_definition_fingerprint():
    scenario = next(item for item in cases() if item["scenario_id"] == "latent_shared_route_map")
    unsafe = next(item for item in scenario["candidates"] if item["candidate_id"].startswith("unsafe"))
    before = build_frr_dependency_graph({scenario["device"]: scenario["baseline"]})
    after = build_frr_dependency_graph({scenario["device"]: unsafe["config"]})
    node = "edge-latent-rm:route_map:RM_SHARED"
    assert before.nodes[node].definition_sha256 != after.nodes[node].definition_sha256


def test_dataset_declares_visible_and_heldout_changes_separately():
    scenario = next(item for item in cases() if item["scenario_id"] == "latent_shared_prefix_list")
    unsafe = next(item for item in scenario["candidates"] if item["candidate_id"].startswith("unsafe"))
    visible_other = unsafe["visible"][2]
    heldout = unsafe["heldout"][0]
    assert visible_other["attributes"]["decision"] == "implicit-deny"
    assert heldout["attributes"]["decision"] == "permit"
