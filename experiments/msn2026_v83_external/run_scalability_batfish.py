#!/usr/bin/env python3
"""Batfish parsing latency for sampled fresh scalability snapshots."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from pybatfish.client.session import Session


SAMPLED = ("objects-1e2", "objects-1e3", "objects-1e4", "devices-100")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v83_external/scale"))
    parser.add_argument("--input", type=Path, default=Path("results/msn2026_v83_external/scalability.json"))
    parser.add_argument("--output", type=Path, default=Path("results/msn2026_v83_external/scalability_batfish.json"))
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()
    bf = Session(host=args.host)
    bf.set_network("pathdelta-v83-scale")
    results = []
    for index, case_id in enumerate(SAMPLED, 1):
        started = time.perf_counter()
        error = None
        rows = []
        try:
            bf.init_snapshot(str(args.data_root / case_id), name=case_id, overwrite=True)
            rows = bf.q.fileParseStatus().answer(snapshot=case_id).frame().to_dict(orient="records")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        latency = (time.perf_counter() - started) * 1000
        statuses = {str(row.get("Status")) for row in rows}
        status = "PASS" if statuses and statuses <= {"PASSED"} else ("ERROR" if error else "N/A")
        result = {"case_id": case_id, "status": status, "latency_ms": latency, "file_count": len(rows), "status_values": sorted(statuses), "error": error}
        results.append(result)
        print(f"batfish-scale {index}/{len(SAMPLED)} {case_id} {status} {latency:.1f}ms", flush=True)
    payload = {"schema_version": "msn2026-v83-scale-batfish-1.0", "sampled_cases": results}
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

