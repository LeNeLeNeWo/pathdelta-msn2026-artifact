from experiments.msn2026_v8_day2_agent.run_v81_paired_agent import changed_atoms
from experiments.msn2026_v8_day2_agent.change_envelope_v2 import BehaviorRecord


def test_heldout_atom_diff_is_explicit():
    before = [BehaviorRecord("b", "r1", "n1", "10.0.0.0/24", {"local_pref": 100}, "pre")]
    after = [BehaviorRecord("b", "r1", "n1", "10.0.0.0/24", {"local_pref": 250}, "post")]
    assert changed_atoms(before, after) == ["b::local_pref"]
