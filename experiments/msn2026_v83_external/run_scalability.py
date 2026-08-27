#!/usr/bin/env python3
"""Fresh scale-only benchmark for Envelope compilation and evidence setup."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import shutil
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.msn2026_v8_day2_agent.change_envelope_v2 import BehaviorRecord, derive_change_envelope_v2  # noqa: E402
from experiments.msn2026_v83_external.vendor_policy_adapter import boundary_witnesses, build_dependency_graph, parse  # noqa: E402


SEED = 20260814
CASES = (
    ("objects-1e2", 10, 100, 1_000),
    ("objects-1e3", 10, 1_000, 10_000),
    ("objects-1e4", 10, 10_000, 100_000),
    ("fecs-1e2", 10, 100, 100),
    ("fecs-1e3", 10, 100, 1_000),
    ("fecs-1e4", 10, 100, 10_000),
    ("fecs-1e5", 10, 100, 100_000),
    ("devices-10", 10, 100, 1_000),
    ("devices-50", 50, 500, 5_000),
    ("devices-100", 100, 1_000, 10_000),
    ("devices-500", 500, 5_000, 100_000),
)


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def measure(fn: Callable[[], Any]) -> tuple[Any, float, float]:
    tracemalloc.start()
    started = time.perf_counter()
    value = fn()
    elapsed = (time.perf_counter() - started) * 1000
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return value, elapsed, peak / (1024 * 1024)


def prefix(index: int) -> str:
    return f"{ipaddress.IPv4Address(0x0A000000 + index)}/32"


def build_configs(device_count: int, object_count: int) -> dict[str, str]:
    configs: dict[str, list[str]] = {}
    pairs = max(1, object_count // 2)
    per_device = max(1, (pairs + device_count - 1) // device_count)
    created = 0
    for device_index in range(device_count):
        device = f"scale-r{device_index:04d}"
        neighbor = f"192.0.{device_index // 254}.{device_index % 254 + 1}"
        lines = [
            "!RANCID-CONTENT-TYPE: cisco",
            "version 15.2",
            f"hostname {device}",
            f"router bgp {65000 + device_index}",
            f" neighbor {neighbor} remote-as {65100 + device_index}",
            f" neighbor {neighbor} route-map RM_{device_index:04d}_0000 in",
            "!",
        ]
        for local in range(per_device):
            if created >= pairs:
                break
            name = f"{device_index:04d}_{local:04d}"
            lines.extend([
                f"ip prefix-list PL_{name} seq 10 permit {prefix(created)}",
                f"route-map RM_{name} permit 10",
                f" match ip address prefix-list PL_{name}",
                " set local-preference 100",
            ])
            created += 1
        lines.append("end")
        configs[device] = "\n".join(lines) + "\n"
    return configs


def behaviors(fec_count: int) -> list[BehaviorRecord]:
    device = "scale-r0000"
    subject = "192.0.0.1@in"
    return [
        BehaviorRecord(
            f"{device}|{subject}|{prefix(index)}",
            device,
            subject,
            prefix(index),
            {"decision": "permit", "local_pref": 100},
            "fresh_scale_generator_v1",
        )
        for index in range(fec_count)
    ]


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
        h.update(b"\n")
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v83_external/scale"))
    parser.add_argument("--output", type=Path, default=Path("results/msn2026_v83_external/scalability.json"))
    args = parser.parse_args()
    if args.data_root.exists():
        shutil.rmtree(args.data_root)
    results = []
    for ordinal, (case_id, devices, objects, fecs) in enumerate(CASES, 1):
        configs, generation_ms, generation_peak = measure(lambda: build_configs(devices, objects))
        case_root = args.data_root / case_id
        for device, text in configs.items():
            path = case_root / "configs" / f"{device}.cfg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        graph, graph_ms, graph_peak = measure(lambda: build_dependency_graph(configs))
        witness_plan, witness_ms, witness_peak = measure(
            lambda: {device: boundary_witnesses(parse(text), limit=48) for device, text in configs.items()}
        )
        pre, behavior_ms, behavior_peak = measure(lambda: behaviors(fecs))
        intent = {
            "intent_id": case_id,
            "raw_text": f"On scale-r0000 set local preference 200 for {prefix(0)} from 192.0.0.1 inbound.",
            "selector": {"devices": ["scale-r0000"], "subjects": ["192.0.0.1@in"], "fecs": [prefix(0)], "dimensions": ["local_pref"]},
            "changes": [{"dimension": "local_pref", "relation": "replace", "desired": 200}],
        }
        env, compile_ms, compile_peak = measure(
            lambda: derive_change_envelope_v2(
                intent,
                configs,
                pre,
                graph,
                behavior_universe_provenance={"backend": "fresh_scale_generator_v1", "complete": True},
            )
        )
        spec = {
            "case_id": case_id,
            "seed": SEED,
            "device_count": devices,
            "requested_object_count": objects,
            "fec_count": fecs,
            "config_bytes": sum(len(text.encode()) for text in configs.values()),
            "config_tree_sha256": tree_hash(case_root),
        }
        write(case_root / "spec.json", spec)
        row = {
            **spec,
            "graph_nodes": len(graph.nodes),
            "graph_edges": sum(len(targets) for targets in graph.edges.values()),
            "witness_count": sum(len(values) for values in witness_plan.values()),
            "frame_obligations": len(env.semantic_frame),
            "timing_ms": {
                "config_generation": generation_ms,
                "dependency_extraction": graph_ms,
                "witness_generation": witness_ms,
                "behavior_materialization": behavior_ms,
                "envelope_compile": compile_ms,
                "total_without_batfish": generation_ms + graph_ms + witness_ms + behavior_ms + compile_ms,
            },
            "peak_python_mib": {
                "config_generation": generation_peak,
                "dependency_extraction": graph_peak,
                "witness_generation": witness_peak,
                "behavior_materialization": behavior_peak,
                "envelope_compile": compile_peak,
                "maximum_phase": max(generation_peak, graph_peak, witness_peak, behavior_peak, compile_peak),
            },
            "batfish": {"status": "NOT_RUN_HERE", "reason": "measured separately on sampled generated snapshots"},
        }
        results.append(row)
        print(f"scale {ordinal}/{len(CASES)} {case_id} compile={compile_ms:.1f}ms peak={compile_peak:.1f}MiB", flush=True)
        del pre, env, graph, configs, witness_plan
    output = {
        "schema_version": "msn2026-v83-scalability-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "case_count": len(results),
        "cases": results,
        "data_tree_sha256": tree_hash(args.data_root),
    }
    write(args.output, output)
    print(args.output)


if __name__ == "__main__":
    main()
