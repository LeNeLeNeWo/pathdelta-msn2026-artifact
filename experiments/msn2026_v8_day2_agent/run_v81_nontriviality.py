#!/usr/bin/env python3
"""Evaluate frozen v8.1 cases against simple and full contracts."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.msn2026_v8_day2_agent.change_envelope_v2 import BehaviorRecord, build_frr_dependency_graph, derive_change_envelope_v2
from experiments.msn2026_v8_day2_agent.semantic_metrics import build_blast_radius_report
from experiments.msn2026_v8_day2_agent.run_rq1_envelope import _scope_ok


def load_records(path: Path) -> list[BehaviorRecord]:
    return [BehaviorRecord(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def configs(path: Path) -> Dict[str, str]:
    return {item.stem: item.read_text(encoding="utf-8") for item in sorted(path.glob("*.conf"))}


def behavior_changes(before: Sequence[BehaviorRecord], after: Sequence[BehaviorRecord]) -> list[str]:
    post = {row.behavior_id: row for row in after}
    changed = []
    for old in before:
        new = post.get(old.behavior_id)
        if new is None:
            changed.append(f"{old.behavior_id}::missing")
            continue
        for dimension in sorted(set(old.attributes) | set(new.attributes)):
            if old.attributes.get(dimension) != new.attributes.get(dimension):
                changed.append(f"{old.behavior_id}::{dimension}")
    return changed


def frozen_closure_unchanged(envelope: Any, before_graph: Any, after_graph: Any) -> bool:
    before_edges = {(source, target) for source, targets in before_graph.edges.items() for target in targets}
    after_edges = {(source, target) for source, targets in after_graph.edges.items() for target in targets}
    for node in envelope.dependency_closure:
        if node not in after_graph.nodes or before_graph.nodes.get(node) != after_graph.nodes.get(node):
            return False
        if {edge for edge in before_edges if edge[0] == node} != {edge for edge in after_edges if edge[0] == node}:
            return False
    return True


def syntax(path: Path, container: str) -> Dict[str, Any]:
    relative = path.resolve().relative_to(ROOT)
    command = ["docker", "exec", container, "vtysh", "-C", "-f", f"/workspace/{relative.as_posix()}"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    return {"pass": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout[-1000:], "stderr": result.stderr[-1000:]}


def run(data_root: Path, output_root: Path, frr_container: str) -> Dict[str, Any]:
    rows = []
    for scenario_root in sorted((data_root / "scenarios").iterdir()):
        before_configs = configs(scenario_root / "baseline")
        visible_pre = load_records(scenario_root / "visible_pre_observations.json")
        heldout_pre = load_records(scenario_root / "heldout_pre_observations.json")
        intent = json.loads((scenario_root / "intent.json").read_text(encoding="utf-8"))
        before_graph = build_frr_dependency_graph(before_configs)
        envelope = derive_change_envelope_v2(
            intent,
            before_configs,
            visible_pre,
            before_graph,
            behavior_universe_provenance={
                "backend": "visible_observation_slice",
                "complete": False,
                "uncovered_reason": "held-out dormant FECs are unavailable to every evaluated method",
            },
        )
        target_atoms = {f"{item.behavior_id}::{item.dimension}" for item in envelope.target_delta}
        for candidate_root in sorted((scenario_root / "candidates").iterdir()):
            after_configs = configs(candidate_root / "configs")
            visible_post = load_records(candidate_root / "visible_post_observations.json")
            heldout_post = load_records(candidate_root / "heldout_post_observations.json")
            after_graph = build_frr_dependency_graph(after_configs)
            report = build_blast_radius_report(
                before_configs, after_configs, visible_pre, visible_post, envelope, before_graph, after_graph
            ).to_dict()
            visible_changes = set(behavior_changes(visible_pre, visible_post))
            heldout_changes = behavior_changes(heldout_pre, heldout_post)
            syntax_rows = [syntax(path, frr_container) for path in sorted((candidate_root / "configs").glob("*.conf"))]
            syntax_ok = all(item["pass"] for item in syntax_rows)
            goal = syntax_ok and report["compliance"]["goal_success"]
            oracle_safe = bool(goal and not (visible_changes - target_atoms) and not heldout_changes)
            methods = {
                "goal_only": goal,
                "preserve_visible_plus_scope": bool(
                    goal and report["compliance"]["semantic_frame_preserved"] and _scope_ok(report, envelope)
                ),
                "freeze_entire_target_closure": bool(goal and frozen_closure_unchanged(envelope, before_graph, after_graph)),
                "full_change_envelope": bool(syntax_ok and report["compliance"]["envelope_compliance"]),
            }
            rows.append(
                {
                    "scenario_id": scenario_root.name,
                    "candidate_id": candidate_root.name,
                    "oracle_safe": oracle_safe,
                    "visible_non_target_changes": sorted(visible_changes - target_atoms),
                    "heldout_changes": heldout_changes,
                    "protected_dependencies": envelope.protected_dependencies,
                    "dependency_closure": envelope.dependency_closure,
                    "methods": methods,
                    "syntax": syntax_rows,
                    "report": report,
                }
            )

    methods = list(rows[0]["methods"])
    summary = []
    for method in methods:
        safe = [row for row in rows if row["oracle_safe"]]
        unsafe = [row for row in rows if not row["oracle_safe"]]
        summary.append(
            {
                "method": method,
                "safe_count": len(safe),
                "safe_accepted": sum(row["methods"][method] for row in safe),
                "unsafe_count": len(unsafe),
                "unsafe_rejected": sum(not row["methods"][method] for row in unsafe),
            }
        )
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "candidate_results.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (output_root / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    return {"summary": summary, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v81_nontriviality"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v81_nontriviality_dev"))
    parser.add_argument("--frr-container", default="pathdelta-msn2026-frr-syntax")
    args = parser.parse_args()
    result = run(args.data_root, args.output_root, args.frr_container)
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
