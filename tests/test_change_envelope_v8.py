from pathlib import Path

from experiments.msn2026_v8_day2_agent.change_envelope import (
    Day2Intent,
    SearchReplaceEdit,
    derive_change_envelope,
    evaluate_candidate,
    parse_config,
)
from experiments.msn2026_v8_day2_agent.run_pilot import _candidate_edits
from tools.build_msn2026_v8_day2_dev import BASELINE_TEMPLATE


def _setup():
    intent = Day2Intent(
        intent_id="test",
        target_device="edge-1",
        target_neighbor="198.51.100.1",
        target_prefix="203.0.113.0/24",
        desired_local_pref=250,
    )
    baseline = {"edge-1": BASELINE_TEMPLATE}
    envelope = derive_change_envelope(baseline, intent, ["10.1.0.0/24", "192.0.2.0/24"])
    return baseline, envelope


def test_parser_keeps_all_route_map_clauses():
    model = parse_config(BASELINE_TEMPLATE)
    assert [clause.sequence for clause in model.route_maps["RM_EDGE_IN"]] == [10, 20]
    assert model.route_map_refcounts()["RM_EDGE_IN"] == 2


def test_envelope_is_derived_from_shared_dependency():
    _, envelope = _setup()
    assert envelope.derivation_evidence["target_route_map_refcount"] == 2
    assert "RM_EDGE_IN" in envelope.protected_existing_route_maps
    assert set(envelope.protected_existing_prefix_lists) == {"PL_ALL", "PL_CUSTOMER"}
    assert "198.51.100.2|203.0.113.0/24" in envelope.preservation_frame


def test_goal_only_would_accept_unsafe_shared_edit_but_envelope_rejects_it():
    baseline, envelope = _setup()
    verdict = evaluate_candidate(
        "unsafe_shared_in_place", baseline, _candidate_edits()["unsafe_shared_in_place"], envelope
    )
    assert verdict.goal_satisfied
    assert not verdict.semantic_frame_preserved
    assert not verdict.structural_scope_preserved
    assert not verdict.accepted


def test_only_safe_local_fork_is_accepted():
    baseline, envelope = _setup()
    verdicts = {
        name: evaluate_candidate(name, baseline, edits, envelope)
        for name, edits in _candidate_edits().items()
    }
    assert [name for name, verdict in verdicts.items() if verdict.accepted] == ["safe_local_fork"]
    assert verdicts["semantic_ok_style_mismatch"].goal_satisfied
    assert verdicts["semantic_ok_style_mismatch"].semantic_frame_preserved
    assert not verdicts["semantic_ok_style_mismatch"].style_preserved
    assert verdicts["semantic_ok_broad_rewrite"].semantic_frame_preserved
    assert not verdicts["semantic_ok_broad_rewrite"].structural_scope_preserved
    assert not verdicts["semantic_ok_broad_rewrite"].budget_preserved

