#!/usr/bin/env python3
"""Independent Batfish audit of final Full-R agent configurations."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.msn2026_v83_external.run_batfish_symbolic_external import (  # noqa: E402
    difference_fields,
    networks,
    observed_output_value,
    query,
    snapshot_input,
)
from experiments.msn2026_v83_external.vendor_policy_adapter import parse, policy_for_subject  # noqa: E402


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def collect_combined_agent128() -> list[dict]:
    old_root = Path("results/msn2026_v83_external/agent128/cases")
    replay_root = Path("results/msn2026_v84_agent_repair/frozen_replay79/cases")
    replay = {load(path)["case_id"]: load(path) for path in replay_root.glob("*.json")}
    rows = []
    for path in sorted(old_root.glob("*.json")):
        old = load(path)
        if old["case_id"] in replay:
            source = replay[old["case_id"]]
            arm = source["arm"]
            result_source = "v84_failure_replay"
        else:
            source = old
            arm = old["arms"]["full_envelope"]
            result_source = "v83_original_completion"
        if not arm["verified_completion"]:
            continue
        rows.append({
            "case_id": old["case_id"],
            "scenario_id": old["scenario_id"],
            "mode": old["mode"],
            "candidate_configs": arm["trace"][-1]["evaluation"]["candidate_configs"],
            "result_source": result_source,
        })
    return rows


def collect_holdout() -> list[dict]:
    rows = []
    for path in sorted(Path("results/msn2026_v84_agent_repair/holdout32/cases").glob("*.json")):
        source = load(path)
        arm = source["arm"]
        if not arm["verified_completion"]:
            continue
        rows.append({
            "case_id": source["case_id"],
            "scenario_id": source["scenario_id"],
            "mode": source["mode"],
            "candidate_configs": arm["trace"][-1]["evaluation"]["candidate_configs"],
            "result_source": "v84_holdout32",
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--population", choices=("agent128", "holdout32"), default="agent128")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    rows = collect_combined_agent128() if args.population == "agent128" else collect_holdout()
    rows = rows[args.offset:(args.offset + args.limit) if args.limit else None]
    output_root = args.output_root or Path(f"results/msn2026_v84_agent_repair/batfish_{args.population}")

    try:
        from pybatfish.client.session import Session
    except ImportError as exc:
        raise RuntimeError("run with .venv-batfish/bin/python") from exc
    bf = Session(host=args.host)
    bf.set_network(f"pathdelta-v84-agent-{args.population}")
    output = []
    snapshots = output_root / "snapshot_inputs"
    for ordinal, candidate in enumerate(rows, 1):
        sid, mode = candidate["scenario_id"], candidate["mode"]
        scenario = Path("data/msn2026_v83_external/public/scenarios") / sid
        metadata, intent = load(scenario / "metadata.json"), load(scenario / "intent.json")
        device = metadata["device"]
        baseline_text = (scenario / "baseline/configs" / f"{device}.conf").read_text(encoding="utf-8")
        candidate_text = candidate["candidate_configs"][device]
        subject, target_prefix = intent["selector"]["subjects"][0], intent["selector"]["fecs"][0]
        pre_policy = policy_for_subject(parse(baseline_text), subject)
        post_policy = policy_for_subject(parse(candidate_text), subject)
        key = f"{ordinal + args.offset:03d}-{re.sub('[^a-zA-Z0-9]+', '-', sid)[-20:]}-{mode}"
        pre_name, post_name = f"pre-{key}", f"post-{key}"
        pre_root = snapshot_input(snapshots, pre_name, device, baseline_text)
        post_root = snapshot_input(snapshots, post_name, device, candidate_text)
        error = None
        parse_status = {"status": "N/A", "rows": []}
        symbolic = {"status": "N/A", "rows": [], "row_count": None, "truncated": False}
        try:
            bf.init_snapshot(str(pre_root), name=pre_name, overwrite=True)
            bf.init_snapshot(str(post_root), name=post_name, overwrite=True)
            parse_status = query(bf.q.fileParseStatus(), snapshot=post_name)
            symbolic = query(bf.q.compareRoutePolicies(policy=post_policy, referencePolicy=pre_policy),
                             snapshot=post_name, reference=pre_name)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        parse_values = {str(item.get("Status")) for item in parse_status.get("rows", [])}
        diff_networks = networks(symbolic.get("rows", []))
        dimension, desired = intent["changes"][0]["dimension"], intent["changes"][0]["desired"]
        batfish_dimension = {"local_pref": "localPreference", "communities": "communities", "decision": "action"}.get(dimension, dimension)
        target_rows = [row for row in symbolic.get("rows", []) if target_prefix in networks([row])]
        observed = [observed_output_value(row, dimension) for row in target_rows]
        unauthorized_fields = sorted({field for row in target_rows for field in difference_fields(row) if field != batfish_dimension})
        target_pass = bool(target_rows and desired in observed)
        if error or symbolic["status"] != "PASS" or not parse_values or parse_values - {"PASSED"} or symbolic.get("truncated"):
            verdict = "N/A"
        elif any(network != target_prefix for network in diff_networks) or unauthorized_fields or not target_pass:
            verdict = "FAIL"
        else:
            verdict = "PASS"
        row = {
            **{key: candidate[key] for key in ("case_id", "scenario_id", "mode", "result_source")},
            "vendor": metadata["vendor"], "device": device, "subject": subject,
            "target_prefix": target_prefix, "pre_policy": pre_policy, "post_policy": post_policy,
            "parse_status": parse_status, "symbolic_difference": symbolic,
            "symbolic_difference_networks": diff_networks,
            "unauthorized_target_fields": unauthorized_fields,
            "symbolic_target_observed_values": observed, "symbolic_target_required": desired,
            "symbolic_frame_verdict": verdict, "error": error,
        }
        output.append(row)
        write(output_root / "cases" / f"{sid}__{mode}.json", row)
        print(f"agent-batfish {ordinal}/{len(rows)} {candidate['case_id']} {verdict}", flush=True)
    summary = {
        "schema_version": "msn2026-v84-agent-batfish-audit-1.0",
        "population": args.population,
        "candidate_count": len(output),
        "pass": sum(row["symbolic_frame_verdict"] == "PASS" for row in output),
        "fail": sum(row["symbolic_frame_verdict"] == "FAIL" for row in output),
        "verifier_na": sum(row["symbolic_frame_verdict"] == "N/A" for row in output),
        "fail_closed_verified_completion": sum(row["symbolic_frame_verdict"] == "PASS" for row in output),
        "rows": output,
    }
    write(output_root / "summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
