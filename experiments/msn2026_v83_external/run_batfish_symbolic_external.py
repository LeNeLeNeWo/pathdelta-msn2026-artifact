#!/usr/bin/env python3
"""Independent Batfish route-policy differential validation for v8.3."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.msn2026_v83_external.vendor_policy_adapter import parse, policy_for_subject  # noqa: E402


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def serial(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serial(v) for v in value]
    return str(value)


def query(question: Any, *, snapshot: str, reference: str | None = None, row_limit: int = 100) -> dict[str, Any]:
    try:
        answer = question.answer(snapshot=snapshot, reference_snapshot=reference) if reference else question.answer(snapshot=snapshot)
        frame = answer.frame()
        rows = [{str(k): serial(v) for k, v in row.items()} for _, row in frame.head(row_limit).iterrows()]
        return {"status": "PASS", "row_count": len(frame), "rows": rows, "truncated": len(frame) > row_limit}
    except Exception as exc:
        return {"status": "N/A", "row_count": None, "rows": [], "truncated": False, "reason": f"{type(exc).__name__}: {exc}"}


def networks(rows: list[Mapping[str, Any]]) -> list[str]:
    output = set()
    for row in rows:
        text = str(row.get("Input_Route", ""))
        for pattern in (r"network='([^']+)'", r"network=([^,\)]+)"):
            match = re.search(pattern, text)
            if match:
                output.add(match.group(1).strip())
                break
    return sorted(output)


def difference_fields(row: Mapping[str, Any]) -> list[str]:
    return sorted(set(re.findall(r"fieldName='([^']+)'", str(row.get("Difference", "")))))


def observed_output_value(row: Mapping[str, Any], dimension: str) -> Any:
    text = str(row.get("Snapshot_Output_Route", ""))
    if dimension == "local_pref":
        match = re.search(r"localPreference=(\d+)", text)
        return int(match.group(1)) if match else None
    if dimension == "communities":
        match = re.search(r"communities=\[([^\]]*)\]", text)
        return sorted(re.findall(r"'?([0-9]+:[0-9]+)'?", match.group(1))) if match else None
    if dimension == "decision":
        return str(row.get("Snapshot_Action", "")).lower()
    return None


def snapshot_input(root: Path, name: str, device: str, text: str) -> Path:
    path = root / name
    if path.exists():
        shutil.rmtree(path)
    configs = path / "configs"
    configs.mkdir(parents=True)
    (configs / f"{device}.cfg").write_text(text, encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v83_external"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v83_external/batfish_symbolic_external"))
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    try:
        from pybatfish.client.session import Session
    except ImportError as exc:
        raise RuntimeError("run with .venv-batfish/bin/python") from exc
    candidates = sorted((args.data_root / "public/candidates").glob("*/*.json"))
    selected = candidates[args.offset : (args.offset + args.limit) if args.limit else None]
    bf = Session(host=args.host)
    bf.set_network("pathdelta-v83-symbolic-external")
    rows = []
    snapshots = args.output_root / "snapshot_inputs"
    for ordinal, path in enumerate(selected, 1):
        candidate = load(path)
        sid, mode = candidate["scenario_id"], candidate["mode"]
        scenario = args.data_root / "public/scenarios" / sid
        metadata, intent = load(scenario / "metadata.json"), load(scenario / "intent.json")
        device = metadata["device"]
        baseline_text = (scenario / "baseline/configs" / f"{device}.conf").read_text(encoding="utf-8")
        candidate_text = candidate["candidate_configs"][device]
        subject, target_prefix = intent["selector"]["subjects"][0], intent["selector"]["fecs"][0]
        pre_model, post_model = parse(baseline_text), parse(candidate_text)
        pre_policy, post_policy = policy_for_subject(pre_model, subject), policy_for_subject(post_model, subject)
        key = f"{ordinal + args.offset:03d}-{re.sub('[^a-zA-Z0-9]+', '-', sid)[-24:]}-{mode}"
        pre_name, post_name = f"pre-{key}", f"post-{key}"
        pre_root = snapshot_input(snapshots, pre_name, device, baseline_text)
        post_root = snapshot_input(snapshots, post_name, device, candidate_text)
        error = None
        parse_status = {"status": "N/A", "rows": []}
        symbolic = {"status": "N/A", "rows": [], "row_count": None}
        coverage = {"permit": {"status": "N/A"}, "deny": {"status": "N/A"}}
        try:
            bf.init_snapshot(str(pre_root), name=pre_name, overwrite=True)
            bf.init_snapshot(str(post_root), name=post_name, overwrite=True)
            parse_status = query(bf.q.fileParseStatus(), snapshot=post_name)
            coverage = {
                "permit": query(bf.q.searchRoutePolicies(policies=pre_policy, inputConstraints={}, action="permit",
                                                           outputConstraints={}, pathOption="per_path"), snapshot=pre_name),
                "deny": query(bf.q.searchRoutePolicies(policies=pre_policy, inputConstraints={}, action="deny",
                                                         pathOption="per_path"), snapshot=pre_name),
            }
            symbolic = query(bf.q.compareRoutePolicies(policy=post_policy, referencePolicy=pre_policy),
                             snapshot=post_name, reference=pre_name)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        parse_values = {str(item.get("Status")) for item in parse_status.get("rows", [])}
        diff_networks = networks(symbolic.get("rows", []))
        dimension = intent["changes"][0]["dimension"]
        desired = intent["changes"][0]["desired"]
        batfish_dimension = {"local_pref": "localPreference", "communities": "communities", "decision": "action"}.get(dimension, dimension)
        target_rows = [row for row in symbolic.get("rows", []) if target_prefix in networks([row])]
        target_observed = [observed_output_value(row, dimension) for row in target_rows]
        changed_fields = {field for row in symbolic.get("rows", []) for field in difference_fields(row)}
        unauthorized_target_fields = {
            field for row in target_rows for field in difference_fields(row)
            if field != batfish_dimension
        }
        target_pass = bool(target_rows and desired in target_observed)
        if error or symbolic["status"] != "PASS" or not parse_values or parse_values - {"PASSED"}:
            verdict = "N/A"
        elif symbolic["truncated"]:
            verdict = "N/A"
        elif not diff_networks and (symbolic["row_count"] or 0) > 0:
            verdict = "N/A"
        elif any(network != target_prefix for network in diff_networks) or unauthorized_target_fields:
            verdict = "FAIL"
        elif not target_pass:
            verdict = "FAIL"
        else:
            verdict = "PASS"
        label = load(args.data_root / "sealed/candidate_oracles" / sid / f"{mode}.json")
        row = {"scenario_id": sid, "mode": mode, "source_group": metadata["source_group"], "vendor": metadata["vendor"],
               "device": device, "subject": subject, "target_prefix": target_prefix, "pre_policy": pre_policy, "post_policy": post_policy,
               "parse_status": parse_status, "symbolic_difference": symbolic, "symbolic_difference_networks": diff_networks,
               "symbolic_changed_fields": sorted(changed_fields), "unauthorized_target_fields": sorted(unauthorized_target_fields),
               "symbolic_target_observed_values": target_observed, "symbolic_target_required": desired,
               "symbolic_target_verdict": "PASS" if target_pass else "FAIL",
               "baseline_symbolic_coverage": coverage,
               "symbolic_equivalence_classes": sum((value.get("row_count") or 0) for value in coverage.values()),
               "symbolic_frame_verdict": verdict, "error": error,
               "oracle": {"goal_success": label["goal_success"], "safe": label["safe"]}}
        rows.append(row)
        write(args.output_root / "cases" / sid / f"{mode}.json", row)
        print(f"symbolic {ordinal}/{len(selected)} {sid} {mode} {verdict} diffs={symbolic.get('row_count')}", flush=True)
    applicable = [row for row in rows if row["symbolic_frame_verdict"] != "N/A"]
    safe = [row for row in applicable if row["oracle"]["goal_success"] and row["oracle"]["safe"]]
    unsafe = [row for row in applicable if row["oracle"]["goal_success"] and not row["oracle"]["safe"]]
    summary = {"schema_version": "msn2026-v83-batfish-symbolic-external-1.0", "candidate_count": len(rows),
               "applicable_count": len(applicable), "verifier_na": len(rows) - len(applicable),
               "symbolic_equivalence_classes": sum(row["symbolic_equivalence_classes"] for row in rows),
               "safe_passed": sum(row["symbolic_frame_verdict"] == "PASS" for row in safe), "safe_count": len(safe),
               "unsafe_failed": sum(row["symbolic_frame_verdict"] == "FAIL" for row in unsafe), "unsafe_count": len(unsafe),
               "rows": rows,
               "interpretation": "Independent Batfish differential evidence; N/A is not promoted to PASS and does not relabel the adapter oracle."}
    write(args.output_root / "summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
