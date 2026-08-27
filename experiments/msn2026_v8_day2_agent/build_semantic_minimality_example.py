#!/usr/bin/env python3
"""Materialize the audited Patch A/Patch B semantic-minimality example."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.msn2026_v8_day2_agent.change_envelope import evaluate_matrix, parse_config
from experiments.msn2026_v8_day2_agent.change_envelope_v2 import (
    BehaviorRecord,
    build_frr_dependency_graph,
    derive_change_envelope_v2,
)
from experiments.msn2026_v8_day2_agent.semantic_metrics import build_blast_radius_report
from tools.build_msn2026_v8_day2_dev import BASELINE_TEMPLATE


PREFIXES = ["10.1.0.0/24", "203.0.113.0/24"]
NEIGHBORS = ["198.51.100.1", "198.51.100.2"]


def _records(config: str, source: str):
    matrix = evaluate_matrix(parse_config(config), NEIGHBORS, PREFIXES)
    records = []
    for key, outcome in matrix.items():
        neighbor, prefix = key.split("|", 1)
        records.append(
            BehaviorRecord(
                behavior_id=f"edge-1|{neighbor}|{prefix}",
                device="edge-1",
                subject=neighbor,
                fec=prefix,
                attributes={**outcome, "session": "established"},
                source=source,
            )
        )
    return records


def build(output: Path) -> dict:
    before = BASELINE_TEMPLATE
    patch_a = before.replace(
        "ip prefix-list PL_CUSTOMER seq 10 permit 10.0.0.0/8 le 24",
        "ip prefix-list PL_TARGET seq 10 permit 203.0.113.0/24\n"
        "ip prefix-list PL_CUSTOMER seq 10 permit 10.0.0.0/8 le 24",
    ).replace(
        "route-map RM_EDGE_IN permit 10",
        "route-map RM_EDGE_IN permit 5\n"
        " match ip address prefix-list PL_TARGET\n"
        " set local-preference 250\n"
        "route-map RM_EDGE_IN permit 10",
    )
    local_policy = """ip prefix-list PL_TARGET seq 10 permit 203.0.113.0/24
route-map RM_EDGE_A_IN permit 10
 match ip address prefix-list PL_TARGET
 set local-preference 250
route-map RM_EDGE_A_IN permit 20
 match ip address prefix-list PL_CUSTOMER
 set local-preference 150
route-map RM_EDGE_A_IN permit 30
 match ip address prefix-list PL_ALL
 set local-preference 100
!
line vty"""
    patch_b = before.replace(
        " neighbor 198.51.100.1 route-map RM_EDGE_IN in",
        " neighbor 198.51.100.1 route-map RM_EDGE_A_IN in",
    ).replace("line vty", local_policy)

    intent = {
        "intent_id": "semantic-minimality-motivator",
        "raw_text": "On edge-1 set local_pref 250 for 203.0.113.0/24 from 198.51.100.1",
        "selector": {"devices": ["edge-1"], "subjects": ["198.51.100.1"], "fecs": ["203.0.113.0/24"]},
        "changes": [{"dimension": "local_pref", "relation": "replace", "desired": 250}],
    }
    configs = {"edge-1": before}
    before_records = _records(before, "development-evaluator-pre")
    before_graph = build_frr_dependency_graph(configs)
    envelope = derive_change_envelope_v2(
        intent,
        configs,
        before_records,
        before_graph,
        behavior_universe_provenance={"backend": "development-route-policy-evaluator", "complete": False, "uncovered_reason": "two-prefix motivating sample"},
    )
    payload = {"not_a_benchmark_result": True, "candidates": {}}
    for candidate_id, config in (("patch_a_short_shared", patch_a), ("patch_b_long_local", patch_b)):
        after_configs = {"edge-1": config}
        report = build_blast_radius_report(
            configs,
            after_configs,
            before_records,
            _records(config, f"development-evaluator-{candidate_id}"),
            envelope,
            before_graph,
            build_frr_dependency_graph(after_configs),
        )
        payload["candidates"][candidate_id] = report.to_dict()
    output.mkdir(parents=True, exist_ok=True)
    (output / "baseline.conf").write_text(before, encoding="utf-8")
    (output / "patch_a_short_shared.conf").write_text(patch_a, encoding="utf-8")
    (output / "patch_b_long_local.conf").write_text(patch_b, encoding="utf-8")
    (output / "metrics.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    root = Path(__file__).resolve().parent / "example_artifacts" / "semantic_minimality"
    print(json.dumps(build(root), indent=2, sort_keys=True))
