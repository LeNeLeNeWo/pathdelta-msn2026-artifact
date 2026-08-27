from __future__ import annotations

from .param_solver import solve_bgp_local_pref, solve_ospf_cost


def test_solve_bgp_local_pref_respects_baseline_and_degrade() -> None:
    """
    tiers 语义收敛：
    - tier>0：提升（lp 更大）
    - tier==0：保持基线（lp=baseline）
    - tier<0：降权（lp 更小）
    """
    tiers = {"edge_hi": 3, "edge_base": 0, "edge_avoid": -1}
    sketch = {"existing_lprefs": [100, 100, 200]}

    lp_map = solve_bgp_local_pref(tiers, sketch, band_width=1000)
    assert lp_map["edge_base"] == 100
    assert lp_map["edge_hi"] > lp_map["edge_base"] > lp_map["edge_avoid"]
    assert lp_map["edge_avoid"] >= 10

    exits = list(tiers.keys())
    for a in exits:
        for b in exits:
            if a == b:
                continue
            if tiers[a] > tiers[b]:
                assert lp_map[a] > lp_map[b]
            if tiers[a] == tiers[b]:
                assert lp_map[a] == lp_map[b]


def test_solve_ospf_cost_respects_baseline_and_degrade() -> None:
    """
    tiers 语义收敛：
    - tier>0：提升（cost 更小）
    - tier==0：保持基线（cost=baseline）
    - tier<0：降权（cost 更大）
    """
    tiers = {"edge_hi": 3, "edge_base": 0, "edge_avoid": -1}
    sketch = {"existing_costs": [10, 10, 20]}

    cost_map = solve_ospf_cost(tiers, sketch, cost_step=10)
    assert cost_map["edge_base"] == 10
    assert cost_map["edge_hi"] < cost_map["edge_base"] < cost_map["edge_avoid"]

    exits = list(tiers.keys())
    for a in exits:
        for b in exits:
            if a == b:
                continue
            if tiers[a] > tiers[b]:
                assert cost_map[a] < cost_map[b]
            if tiers[a] == tiers[b]:
                assert cost_map[a] == cost_map[b]

