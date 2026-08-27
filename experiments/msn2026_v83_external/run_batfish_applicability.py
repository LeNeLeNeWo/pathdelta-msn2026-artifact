#!/usr/bin/env python3
"""Run Batfish parse/applicability checks on every frozen external candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pybatfish.client.session import Session


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def key(scenario_id: str, mode: str) -> str:
    return hashlib.sha256(f"{scenario_id}|{mode}".encode()).hexdigest()[:20]


def run_one(bf: Session, network: str, snapshot_root: Path, snapshot_name: str, device: str, text: str) -> dict[str, Any]:
    config_root = snapshot_root / snapshot_name / "configs"
    if config_root.parent.exists():
        shutil.rmtree(config_root.parent)
    config_root.mkdir(parents=True)
    (config_root / f"{device}.cfg").write_text(text, encoding="utf-8")
    started = time.perf_counter()
    error = None
    status_rows: list[dict[str, Any]] = []
    warning_rows: list[dict[str, Any]] = []
    try:
        bf.set_network(network)
        bf.init_snapshot(str(config_root.parent), name=snapshot_name, overwrite=True)
        status_rows = bf.q.fileParseStatus().answer(snapshot=snapshot_name).frame().to_dict(orient="records")
        warning_rows = bf.q.parseWarning().answer(snapshot=snapshot_name).frame().to_dict(orient="records")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency = (time.perf_counter() - started) * 1000
    statuses = {str(row.get("Status")) for row in status_rows}
    status = "PASS" if statuses and statuses <= {"PASSED"} else ("ERROR" if error else "N/A")
    return {
        "status": status,
        "latency_ms": latency,
        "file_parse_status": status_rows,
        "parse_warnings": warning_rows,
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v83_external"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v83_external/batfish_applicability"))
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    candidate_paths = sorted((args.data_root / "public/candidates").glob("*/*.json"))
    selected = candidate_paths[args.offset : (args.offset + args.limit) if args.limit else None]
    bf = Session(host=args.host)
    network = "pathdelta-v83-external"
    cases = []
    snapshots = args.output_root / "snapshot_inputs"
    for ordinal, path in enumerate(selected, 1):
        candidate = load(path)
        sid, mode = candidate["scenario_id"], candidate["mode"]
        device, text = next(iter(candidate["candidate_configs"].items()))
        result = run_one(bf, network, snapshots, f"cand-{key(sid, mode)}", device, text)
        row = {
            "scenario_id": sid,
            "mode": mode,
            "vendor": candidate["vendor"],
            "source_group": candidate["source_group"],
            "candidate_sha256": candidate["candidate_sha256"],
            "batfish": result,
        }
        cases.append(row)
        write(args.output_root / "cases" / sid / f"{mode}.json", row)
        print(f"batfish {ordinal}/{len(selected)} {sid} {mode} {result['status']} {result['latency_ms']:.0f}ms", flush=True)
    statuses = {"PASS": 0, "N/A": 0, "ERROR": 0}
    for row in cases:
        statuses[row["batfish"]["status"]] = statuses.get(row["batfish"]["status"], 0) + 1
    latencies = sorted(row["batfish"]["latency_ms"] for row in cases)
    summary = {
        "schema_version": "msn2026-v83-batfish-applicability-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(cases),
        "status_counts": statuses,
        "parse_warning_candidate_count": sum(bool(row["batfish"]["parse_warnings"]) for row in cases),
        "latency_ms": {
            "total": sum(latencies),
            "p50": latencies[len(latencies) // 2] if latencies else None,
            "p95": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else None,
        },
        "batfish_host": args.host,
        "network": network,
        "cases": cases,
    }
    write(args.output_root / "summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "cases"}, indent=2))


if __name__ == "__main__":
    main()

