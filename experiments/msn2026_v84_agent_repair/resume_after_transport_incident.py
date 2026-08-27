#!/usr/bin/env python3
"""Resume interrupted cases without charging transport failures as semantic attempts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_agent128_repair_v2 as v84  # noqa: E402
import run_union_repair as union  # noqa: E402
from run_batfish_agent_final import collect_combined_agent128  # noqa: E402


TRANSPORT_MARKERS = (
    "HTTPError", "IncompleteRead", "Connection Reset", "timed out", "RemoteDisconnected",
    "ConnectionError", "URLError",
)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def is_success(row: dict, mode: str) -> bool:
    arm = row["arm"]
    return bool(arm["verified_completion"] if mode == "holdout" else arm["accepted_registered"])


def has_transport_failure(row: dict) -> bool:
    errors = [
        str(attempt.get("evaluation", {}).get("transaction_error") or "")
        for attempt in row["arm"].get("trace", [])
    ]
    return any(any(marker in error for marker in TRANSPORT_MARKERS) for error in errors)


def archive(path: Path, incident_root: Path, round_index: int) -> dict:
    raw = path.read_bytes()
    target = incident_root / f"{path.stem}__transport_round{round_index}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return {"path": target.as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("holdout", "union"), required=True)
    parser.add_argument("--max-transport-rounds", type=int, default=3)
    parser.add_argument("--pace-seconds", type=float, default=2.0)
    args = parser.parse_args()

    data_root = Path("data/msn2026_v83_external")
    if args.mode == "holdout":
        cases = load(Path("data/msn2026_v84_agent_repair/agent_holdout32.json"))["cases"]
        output_root = Path("results/msn2026_v84_agent_repair/holdout32")
        audit_rows = None
    else:
        audit = load(Path("results/msn2026_v84_agent_repair/batfish_agent128/summary.json"))
        audit_rows = {f"{row['scenario_id']}::{row['mode']}": row for row in audit["rows"]}
        base = {row["case_id"]: row for row in collect_combined_agent128()}
        cases = [base[f"{row['scenario_id']}::{row['mode']}"] for row in audit["rows"] if row["symbolic_frame_verdict"] == "FAIL"]
        output_root = Path("results/msn2026_v84_agent_repair/union_repair_agent128")

    incident_root = output_root / "transport_incident"
    for ordinal, case in enumerate(cases, 1):
        path = output_root / "cases" / f"{case['scenario_id']}__{case['mode']}.json"
        if path.exists():
            current = load(path)
            if is_success(current, args.mode) or not has_transport_failure(current):
                continue
        history = []
        for round_index in range(1, args.max_transport_rounds + 1):
            if path.exists():
                history.append(archive(path, incident_root, round_index))
            started = time.perf_counter()
            if args.mode == "holdout":
                row = v84.run_case(case, data_root, 8000, 8, 0.1, "enabled")
                row["development_replay_of_v83"] = False
                row["agent_holdout"] = True
                row["holdout_subset"] = "data/msn2026_v84_agent_repair/agent_holdout32.json"
            else:
                row = union.run_one(case, audit_rows[case["case_id"]], data_root, 3, 8000, 0.1, "enabled")
            row["transport_recovery"] = {
                "incident": "concurrent EdgeOne HTTP 551 / connection reset",
                "transport_attempts_not_counted_as_semantic_submissions": True,
                "archived_prior_runs": history,
                "recovery_round": round_index,
            }
            v84.v83.write(path, row)
            print(f"transport-resume {args.mode} {ordinal}/{len(cases)} {case['case_id']} "
                  f"success={is_success(row, args.mode)} transport={has_transport_failure(row)} "
                  f"elapsed={time.perf_counter()-started:.1f}s", flush=True)
            if is_success(row, args.mode) or not has_transport_failure(row):
                break
            time.sleep(args.pace_seconds)
        time.sleep(args.pace_seconds)

    paths = sorted((output_root / "cases").glob("*.json"))
    if args.mode == "holdout":
        summary = v84.summarize(paths, output_root)
        summary.update({
            "development_replay_of_v83": False, "agent_holdout": True, "disjoint_from_agent128": True,
            "claim_status": "post-freeze held-out agent evaluation; transport incident recovered separately",
            "transport_incident_archives": len(list(incident_root.glob("*.json"))),
        })
    else:
        rows = [load(path) for path in paths]
        summary = {
            "schema_version": "msn2026-v84-union-repair-summary-1.1",
            "case_count": len(cases), "completed_case_files": len(rows),
            "registered_pass": sum(row["arm"]["accepted_registered"] for row in rows),
            "registered_exhaustion": sum(not row["arm"]["accepted_registered"] for row in rows),
            "logical_llm_calls": sum(row["llm_metrics"]["logical_llm_calls"] for row in rows),
            "backend_attempts": sum(row["llm_metrics"]["backend_attempts"] for row in rows),
            "retry_count": sum(row["llm_metrics"]["retry_count"] for row in rows),
            "token_usage": {key: sum(row["arm"]["llm_usage"]["token_usage"][key] for row in rows) for key in ("prompt", "completion", "total")},
            "transport_incident_archives": len(list(incident_root.glob("*.json"))),
            "requires_batfish_reaudit": True,
        }
    v84.v83.write(output_root / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
