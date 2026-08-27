from experiments.msn2026_v8_day2_agent.change_envelope_v2 import (
    BehaviorRecord,
    augment_behavior_universe_with_frr_probes,
    discover_frr_fec_probes,
)


CONFIG = """ip prefix-list PL_CUSTOMER seq 10 permit 10.0.0.0/8 le 24
ip prefix-list PL_TARGET seq 10 permit 203.0.113.0/24
route-map RM_IN permit 10
 match ip address prefix-list PL_CUSTOMER
 set local-preference 100
router bgp 65000
 neighbor 192.0.2.1 route-map RM_IN in
 neighbor 192.0.2.2 route-map RM_IN in
"""


def test_discovers_prefix_boundary_representative():
    probes = discover_frr_fec_probes({"edge": CONFIG})
    assert "10.0.0.0/8" in probes["edge"]
    assert "10.0.0.0/24" in probes["edge"]


def test_augmentation_crosses_known_fec_with_all_subjects():
    observed = [
        BehaviorRecord(
            "edge|192.0.2.1|203.0.113.0/24",
            "edge",
            "192.0.2.1",
            "203.0.113.0/24",
            {"decision": "implicit-deny", "local_pref": None, "session": "established"},
            "observed",
        ),
        BehaviorRecord(
            "edge|192.0.2.2|192.0.2.0/24",
            "edge",
            "192.0.2.2",
            "192.0.2.0/24",
            {"decision": "implicit-deny", "local_pref": None, "session": "established"},
            "observed",
        ),
    ]
    augmented, provenance = augment_behavior_universe_with_frr_probes({"edge": CONFIG}, observed)
    ids = {row.behavior_id for row in augmented}
    assert "edge|192.0.2.1|10.0.0.0/24" in ids
    assert "edge|192.0.2.2|203.0.113.0/24" in ids
    assert provenance["candidate_patch_used"] is False
