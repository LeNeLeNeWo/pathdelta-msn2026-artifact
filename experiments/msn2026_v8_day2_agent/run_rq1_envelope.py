#!/usr/bin/env python3
"""Run RQ1 verification-contract ablations on the fresh v8 benchmark."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

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


SCHEMES = {
    "V0_syntax": "FRR syntax only",
    "V1_goal": "syntax and target goal",
    "V2_write_scope": "goal plus static device/binding/object/count scope",
    "V3_dependency": "goal plus protected dependency frame",
    "V4_semantic_frame": "goal plus target-complement behavior frame",
    "V5_full_envelope": "goal, semantic frame, dependency frame, and hard footprint",
}


def _records(payload: Sequence[Mapping[str, Any]]) -> List[BehaviorRecord]:
    return [BehaviorRecord(**row) for row in payload]


def _configs(root: Path) -> Dict[str, str]:
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(root.glob("*.conf"))}


def _llm_post_records(
    candidate_configs: Mapping[str, str], pre_records: Sequence[BehaviorRecord]
) -> List[BehaviorRecord]:
    # The direct-LLM candidates are independently evaluated here because they
    # are collected after deterministic dataset generation.
    by_device: Dict[str, List[BehaviorRecord]] = {}
    for row in pre_records:
        by_device.setdefault(row.device, []).append(row)
    output: List[BehaviorRecord] = []
    for device, rows in by_device.items():
        config = candidate_configs[device]
        model = parse_config(config)
        neighbors = sorted({row.subject for row in rows})
        prefixes = sorted({row.fec for row in rows})
        matrix = evaluate_matrix(model, neighbors, prefixes)
        for row in rows:
            evaluated = matrix[f"{row.subject}|{row.fec}"]
            output.append(
                BehaviorRecord(
                    row.behavior_id,
                    row.device,
                    row.subject,
                    row.fec,
                    {**row.attributes, **evaluated},
                    "independent_lightweight_evaluator_for_llm_candidate",
                )
            )
    return output


def _frr_syntax(config_paths: Sequence[Path], container: str) -> Dict[str, Any]:
    evidence = []
    for path in config_paths:
        relative = path.resolve().relative_to(PROJECT_ROOT)
        command = ["docker", "exec", container, "vtysh", "-C", "-f", f"/workspace/{relative.as_posix()}"]
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=30)
            evidence.append(
                {
                    "path": str(relative),
                    "status": "PASS" if run.returncode == 0 else "FAIL",
                    "returncode": run.returncode,
                    "stdout": run.stdout[-2000:],
                    "stderr": run.stderr[-2000:],
                }
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            evidence.append({"path": str(relative), "status": "N/A", "error": str(exc)})
    statuses = {row["status"] for row in evidence}
    overall = "FAIL" if "FAIL" in statuses else "N/A" if "N/A" in statuses else "PASS"
    return {"status": overall, "files": evidence}


def _scope_ok(report: Mapping[str, Any], envelope: Any) -> bool:
    structural = report["structural"]
    textual = report["textual"]
    budget = envelope.footprint_budget
    return bool(
        set(structural["devices_touched"]) <= set(budget.allowed_devices)
        and len(structural["devices_touched"]) <= budget.max_devices_touched
        and len(structural["bindings_changed"]) <= budget.max_bindings_changed
        and len(structural["new_objects_created"]) <= budget.max_new_objects
        and textual["lines_touched"] <= budget.max_changed_lines
    )


def classify_candidate(
    *, syntax_status: str, report: Mapping[str, Any], envelope: Any
) -> Dict[str, bool]:
    syntax = syntax_status == "PASS"
    compliance = report["compliance"]
    goal = syntax and compliance["goal_success"]
    return {
        "V0_syntax": syntax,
        "V1_goal": goal,
        "V2_write_scope": goal and _scope_ok(report, envelope),
        "V3_dependency": goal and compliance["dependency_frame_preserved"],
        "V4_semantic_frame": goal and compliance["semantic_frame_preserved"],
        "V5_full_envelope": goal and compliance["semantic_frame_preserved"] and compliance["dependency_frame_preserved"] and compliance["hard_footprint_preserved"],
    }


def _metrics(rows: Sequence[Mapping[str, Any]], scheme: str) -> Dict[str, Any]:
    safe = [row for row in rows if row["ground_truth"]["semantically_acceptable"]]
    unsafe = [row for row in rows if not row["ground_truth"]["semantically_acceptable"]]
    collateral = [
        row
        for row in rows
        if row["ground_truth"]["target_satisfied"]
        and row["ground_truth"]["collateral_semantic_change"]
    ]
    safe_accepted = sum(row["accepted"][scheme] for row in safe)
    unsafe_accepted = sum(row["accepted"][scheme] for row in unsafe)
    collateral_accepted = sum(row["accepted"][scheme] for row in collateral)
    return {
        "scheme": scheme,
        "description": SCHEMES[scheme],
        "safe_count": len(safe),
        "unsafe_count": len(unsafe),
        "safe_accepted": safe_accepted,
        "unsafe_rejected": len(unsafe) - unsafe_accepted,
        "safe_acceptance_rate": safe_accepted / len(safe) if safe else None,
        "unsafe_rejection_recall": (len(unsafe) - unsafe_accepted) / len(unsafe) if unsafe else None,
        "false_accept_rate": unsafe_accepted / len(unsafe) if unsafe else None,
        "false_reject_rate": (len(safe) - safe_accepted) / len(safe) if safe else None,
        "goal_satisfying_collateral_count": len(collateral),
        "goal_satisfying_collateral_accepted": collateral_accepted,
        "goal_satisfying_collateral_false_accept_rate": collateral_accepted / len(collateral) if collateral else None,
    }


def run(benchmark_root: Path, output_root: Path, frr_container: str) -> Dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    syntax_evidence: Dict[str, Any] = {}
    for scenario_root in sorted((benchmark_root / "scenarios").iterdir()):
        if not scenario_root.is_dir():
            continue
        intent = json.loads((scenario_root / "intent.json").read_text(encoding="utf-8"))
        before_configs = _configs(scenario_root / "baseline")
        pre_records = _records(json.loads((scenario_root / "pre_observations.json").read_text(encoding="utf-8")))
        before_graph = build_frr_dependency_graph(before_configs)
        envelope = derive_change_envelope_v2(
            intent,
            before_configs,
            pre_records,
            before_graph,
            behavior_universe_provenance={
                "backend": "independent_benchmark_observation_generator",
                "complete": False,
                "uncovered_reason": "finite benchmark FEC set",
            },
        )
        for candidate_root in sorted((scenario_root / "candidates").iterdir()):
            candidate_configs = _configs(candidate_root / "configs")
            post_path = candidate_root / "post_observations.json"
            post_records = (
                _records(json.loads(post_path.read_text(encoding="utf-8")))
                if post_path.exists()
                else _llm_post_records(candidate_configs, pre_records)
            )
            report = build_blast_radius_report(
                before_configs,
                candidate_configs,
                pre_records,
                post_records,
                envelope,
                before_graph,
                build_frr_dependency_graph(candidate_configs),
            ).to_dict()
            syntax = _frr_syntax(sorted((candidate_root / "configs").glob("*.conf")), frr_container)
            key = f"{scenario_root.name}/{candidate_root.name}"
            syntax_evidence[key] = syntax
            truth = json.loads((candidate_root / "ground_truth.json").read_text(encoding="utf-8"))
            rows.append(
                {
                    "scenario_id": scenario_root.name,
                    "candidate_id": candidate_root.name,
                    "ground_truth": truth,
                    "syntax_status": syntax["status"],
                    "accepted": classify_candidate(syntax_status=syntax["status"], report=report, envelope=envelope),
                    "blast_radius": report,
                    "envelope_coverage": envelope.coverage,
                }
            )

    aggregate = [_metrics(rows, scheme) for scheme in SCHEMES]
    candidate_path = output_root / "rq1_candidate_results.json"
    candidate_path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_root / "frr_syntax_evidence.json").write_text(json.dumps(syntax_evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (output_root / "rq1_results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(aggregate[0]))
        writer.writeheader()
        writer.writerows(aggregate)

    failures = output_root / "failure_examples"
    failures.mkdir(exist_ok=True)
    goal_only_danger = [row for row in rows if row["accepted"]["V1_goal"] and not row["accepted"]["V5_full_envelope"]]
    alternatives = [row for row in rows if row["ground_truth"]["semantically_acceptable"] and row["accepted"]["V5_full_envelope"]]
    (failures / "goal_only_false_accepts.json").write_text(json.dumps(goal_only_danger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (failures / "accepted_safe_alternatives.json").write_text(json.dumps(alternatives, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "run_id": "msn2026_v8_rq1_envelope_dev",
        "status": "completed",
        "scope": "full development benchmark classification; not formal paper freeze",
        "benchmark_manifest_sha256": hashlib.sha256((benchmark_root / "benchmark_manifest.json").read_bytes()).hexdigest(),
        "candidate_count": len(rows),
        "frr_container": frr_container,
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "artifacts": ["rq1_results.csv", "rq1_candidate_results.json", "frr_syntax_evidence.json", "failure_examples/"],
    }
    (output_root / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"aggregate": aggregate, "rows": rows, "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-root", type=Path, default=Path("data/msn2026_v8_envelope_benchmark"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v8_rq1_dev"))
    parser.add_argument("--frr-container", default="pathdelta-msn2026-frr-syntax")
    args = parser.parse_args()
    result = run(args.benchmark_root, args.output_root, args.frr_container)
    print(json.dumps(result["aggregate"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

