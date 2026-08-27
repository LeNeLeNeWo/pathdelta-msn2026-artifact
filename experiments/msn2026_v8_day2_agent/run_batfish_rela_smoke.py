#!/usr/bin/env python3
"""Compute pre/post paths with Batfish and verify their relation with Rela."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _trace_nodes(trace: Any) -> List[str]:
    nodes: List[str] = []
    hops = getattr(trace, "hops", None) or []
    for hop in hops:
        node = getattr(hop, "node", None)
        if node is not None:
            name = getattr(node, "name", None) or str(node)
            if name and (not nodes or nodes[-1] != str(name)):
                nodes.append(str(name))
    return nodes


def _paths_for(bf: Any, snapshot: str, destination_ip: str) -> List[List[str]]:
    from pybatfish.datamodel.flow import HeaderConstraints

    frame = bf.q.traceroute(
        startLocation="edge",
        headers=HeaderConstraints(dstIps=destination_ip),
        maxTraces=32,
    ).answer(snapshot=snapshot).frame()
    paths: List[List[str]] = []
    for _, row in frame.iterrows():
        trace_values = row.get("Traces") or row.get("Trace") or []
        if not isinstance(trace_values, (list, tuple, set)):
            trace_values = [trace_values]
        for trace in trace_values:
            path = _trace_nodes(trace)
            if path and path not in paths:
                paths.append(path)
    return sorted(paths)


def _frame_records(frame: Any, limit: int = 50) -> List[Dict[str, Any]]:
    return [
        {str(key): _jsonable(value) for key, value in row.items()}
        for _, row in frame.head(limit).iterrows()
    ]


def run(data_root: Path, output_root: Path, rela_image: str) -> Dict[str, Any]:
    try:
        from pybatfish.client.session import Session
    except ImportError as exc:
        raise RuntimeError("run this script with the project's .venv-batfish interpreter") from exc

    scenario_root = data_root / "scenario_path_replace"
    scenario = json.loads((scenario_root / "scenario.json").read_text(encoding="utf-8"))
    output_root.mkdir(parents=True, exist_ok=True)
    network_name = "pathdelta-v8-batfish-rela-smoke"
    bf = Session(host="localhost")
    bf.set_network(network_name)
    bf.init_snapshot(str(scenario_root / "pre"), name="pre", overwrite=True)
    bf.init_snapshot(str(scenario_root / "post"), name="post", overwrite=True)
    bf.init_snapshot(
        str(scenario_root / "post_collateral"), name="post_collateral", overwrite=True
    )

    parse_evidence: Dict[str, Any] = {}
    for snapshot in ("pre", "post", "post_collateral"):
        status = bf.q.fileParseStatus().answer(snapshot=snapshot).frame()
        warnings = bf.q.parseWarning().answer(snapshot=snapshot).frame()
        parse_evidence[snapshot] = {
            "status": _frame_records(status),
            "warnings": _frame_records(warnings),
            "all_nodes_recognized": bool(len(status)) and all(len(_jsonable(row.get("Nodes"))) > 0 for _, row in status.iterrows()),
        }

    target = scenario["target"]
    target_before = _paths_for(bf, "pre", target["destination_ip"])
    target_after = _paths_for(bf, "post", target["destination_ip"])
    fecs = [
        {
            "fec_id": "target",
            "prefix": target["prefix"],
            "class": "target",
            "before_paths": target_before,
            "after_paths": target_after,
            "allowed_change": target["expected_path_relation"],
        }
    ]
    path_evidence = {
        "target": {"before": target_before, "after": target_after},
        "controls": {},
    }
    for index, control in enumerate(scenario["controls"]):
        before = _paths_for(bf, "pre", control["destination_ip"])
        after = _paths_for(bf, "post", control["destination_ip"])
        fec_id = f"control_{index + 1}"
        fecs.append(
            {
                "fec_id": fec_id,
                "prefix": control["prefix"],
                "class": "non_target",
                "before_paths": before,
                "after_paths": after,
            }
        )
        path_evidence["controls"][fec_id] = {"before": before, "after": after}

    # Standard differential reachability should be empty here because both
    # snapshots remain reachable; the intended change is the path relation.
    diff_reach = bf.q.differentialReachability(maxTraces=32).answer(
        snapshot="post", reference_snapshot="pre"
    ).frame()
    policy_diff = bf.q.compareRoutePolicies(
        nodes="edge", policy="RM_A_IN", referencePolicy="RM_A_IN"
    ).answer(snapshot="post", reference_snapshot="pre").frame()

    rela_payload = {
        "schema_version": "msn2026.rela_snapshot.v1",
        "snapshot_id": scenario["scenario_id"],
        "path_source": scenario["path_source"],
        "fecs": fecs,
    }
    rela_input = output_root / "rela_input.json"
    rela_output = output_root / "rela_result.json"
    rela_input.write_text(json.dumps(rela_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    started = time.perf_counter()
    command = [
        "docker", "run", "--rm",
        "-v", f"{PROJECT_ROOT}:/workspace",
        "-w", "/workspace",
        rela_image,
        "python", "experiments/msn2026_v2/rela_adapter.py",
        str(rela_input.resolve().relative_to(PROJECT_ROOT)),
        str(rela_output.resolve().relative_to(PROJECT_ROOT)),
    ]
    rela_run = subprocess.run(command, capture_output=True, text=True, timeout=120)
    rela_duration = time.perf_counter() - started
    rela_result = json.loads(rela_output.read_text(encoding="utf-8")) if rela_output.exists() else None

    negative_fecs = [
        {
            "fec_id": "target",
            "prefix": target["prefix"],
            "class": "target",
            "before_paths": target_before,
            "after_paths": _paths_for(bf, "post_collateral", target["destination_ip"]),
            "allowed_change": target["expected_path_relation"],
        }
    ]
    for index, control in enumerate(scenario["controls"]):
        negative_fecs.append(
            {
                "fec_id": f"control_{index + 1}",
                "prefix": control["prefix"],
                "class": "non_target",
                "before_paths": _paths_for(bf, "pre", control["destination_ip"]),
                "after_paths": _paths_for(bf, "post_collateral", control["destination_ip"]),
            }
        )
    negative_payload = {
        "schema_version": "msn2026.rela_snapshot.v1",
        "snapshot_id": scenario["scenario_id"] + "-collateral-negative",
        "path_source": scenario["path_source"],
        "fecs": negative_fecs,
    }
    negative_input = output_root / "rela_input_collateral_negative.json"
    negative_output = output_root / "rela_result_collateral_negative.json"
    negative_input.write_text(
        json.dumps(negative_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    negative_command = [
        "docker", "run", "--rm",
        "-v", f"{PROJECT_ROOT}:/workspace",
        "-w", "/workspace",
        rela_image,
        "python", "experiments/msn2026_v2/rela_adapter.py",
        str(negative_input.resolve().relative_to(PROJECT_ROOT)),
        str(negative_output.resolve().relative_to(PROJECT_ROOT)),
    ]
    negative_run = subprocess.run(negative_command, capture_output=True, text=True, timeout=120)
    negative_result = (
        json.loads(negative_output.read_text(encoding="utf-8")) if negative_output.exists() else None
    )
    negative_diff_reach = bf.q.differentialReachability(maxTraces=32).answer(
        snapshot="post_collateral", reference_snapshot="pre"
    ).frame()

    summary = {
        "smoke_id": "msn2026_v8_batfish_to_rela",
        "not_a_paper_result": True,
        "parse_evidence": parse_evidence,
        "batfish": {
            "target_and_control_paths": path_evidence,
            "differential_reachability_rows": len(diff_reach),
            "differential_reachability_preview": _frame_records(diff_reach),
            "route_policy_difference_rows": len(policy_diff),
            "route_policy_difference_preview": _frame_records(policy_diff),
        },
        "rela": {
            "image": rela_image,
            "executed": True,
            "returncode": rela_run.returncode,
            "duration_seconds": round(rela_duration, 6),
            "stdout": rela_run.stdout[-2000:],
            "stderr": rela_run.stderr[-2000:],
            "result": rela_result,
            "collateral_negative": {
                "returncode": negative_run.returncode,
                "stdout": negative_run.stdout[-2000:],
                "stderr": negative_run.stderr[-2000:],
                "result": negative_result,
                "batfish_differential_reachability_rows": len(negative_diff_reach),
            },
        },
        "passed": bool(
            all(item["all_nodes_recognized"] for item in parse_evidence.values())
            and target_before
            and target_after
            and rela_run.returncode == 0
            and rela_result
            and rela_result.get("passed")
            and negative_run.returncode == 0
            and negative_result
            and not negative_result.get("passed")
        ),
    }
    summary_path = output_root / "batfish_rela_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": "1.0.0-dev",
        "run_id": summary["smoke_id"],
        "scope": "single_synthetic_integration_smoke_not_paper_result",
        "dataset_manifest": str((data_root / "dataset_manifest.json").resolve()),
        "backends": {
            "batfish": {"executed": True, "network": network_name},
            "rela": {"executed": True, "image": rela_image, "returncode": rela_run.returncode},
        },
        "artifacts": {
            "summary": {"path": str(summary_path.resolve()), "sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest()},
            "rela_input": {"path": str(rela_input.resolve()), "sha256": hashlib.sha256(rela_input.read_bytes()).hexdigest()},
            "rela_result": {"path": str(rela_output.resolve()), "sha256": hashlib.sha256(rela_output.read_bytes()).hexdigest()} if rela_output.exists() else None,
            "rela_negative_input": {"path": str(negative_input.resolve()), "sha256": hashlib.sha256(negative_input.read_bytes()).hexdigest()},
            "rela_negative_result": {"path": str(negative_output.resolve()), "sha256": hashlib.sha256(negative_output.read_bytes()).hexdigest()} if negative_output.exists() else None,
        },
    }
    (output_root / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v8_batfish_rela_dev"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v8_batfish_rela_dev"))
    parser.add_argument("--rela-image", default="pathdelta-msn2026-rela:32c533b")
    args = parser.parse_args()
    result = run(args.data_root, args.output_root, args.rela_image)
    print(json.dumps({
        "passed": result["passed"],
        "target_paths": result["batfish"]["target_and_control_paths"]["target"],
        "control_paths": result["batfish"]["target_and_control_paths"]["controls"],
        "differential_reachability_rows": result["batfish"]["differential_reachability_rows"],
        "route_policy_difference_rows": result["batfish"]["route_policy_difference_rows"],
        "rela_passed": bool(result["rela"]["result"] and result["rela"]["result"].get("passed")),
        "rela_rejected_collateral": bool(
            result["rela"]["collateral_negative"]["result"]
            and not result["rela"]["collateral_negative"]["result"].get("passed")
        ),
        "collateral_differential_reachability_rows": result["rela"]["collateral_negative"]["batfish_differential_reachability_rows"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
