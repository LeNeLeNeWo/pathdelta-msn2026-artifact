#!/usr/bin/env python3
"""Generate a fresh, tiny brownfield Day-2 development corpus.

This generator does not read any prior PathDelta data, CSV, result, or
benchmark artifact.  The corpus is deliberately small: it exists to test the
v8 mechanism before designing or running a paper-scale experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path
from typing import Any, Dict


DEFAULT_OUTPUT = Path("data/msn2026_v8_day2_dev")
GENERATOR_VERSION = "msn2026-v8-day2-dev-0.1"


BASELINE_TEMPLATE = """hostname edge-1
!
router bgp 65001
 neighbor 198.51.100.1 remote-as 65101
 neighbor 198.51.100.2 remote-as 65102
 neighbor 198.51.100.1 route-map RM_EDGE_IN in
 neighbor 198.51.100.2 route-map RM_EDGE_IN in
!
ip prefix-list PL_CUSTOMER seq 10 permit 10.0.0.0/8 le 24
ip prefix-list PL_ALL seq 10 permit 0.0.0.0/0 le 32
!
route-map RM_EDGE_IN permit 10
 match ip address prefix-list PL_CUSTOMER
 set local-preference 150
route-map RM_EDGE_IN permit 20
 match ip address prefix-list PL_ALL
 set local-preference 100
!
line vty
!
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build(output_dir: Path, seed: int) -> Dict[str, Any]:
    resolved = output_dir.resolve()
    if resolved == Path("/") or len(resolved.parts) < 3:
        raise ValueError(f"refusing unsafe output path: {resolved}")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    config_dir = output_dir / "scenario_shared_policy" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)

    # The seed controls benign cosmetic residue so generation is reproducible
    # while preserving a stable semantic core.
    rng = random.Random(seed)
    residue_metric = rng.choice([20, 30, 40])
    baseline = BASELINE_TEMPLATE.replace(
        "hostname edge-1", f"hostname edge-1\n! legacy metric habit {residue_metric}"
    )
    config_path = config_dir / "edge-1.conf"
    config_path.write_text(baseline, encoding="utf-8")

    scenario = {
        "scenario_id": "scenario_shared_policy",
        "description": "Two BGP neighbors share one inbound route-map; only one neighbor/prefix may change.",
        "intent": {
            "intent_id": "day2_prefer_partner_prefix",
            "target_device": "edge-1",
            "target_neighbor": "198.51.100.1",
            "target_prefix": "203.0.113.0/24",
            "desired_local_pref": 250,
        },
        "probe_prefixes": ["10.1.0.0/24", "192.0.2.0/24"],
        "latent_hazards": [
            "shared route-map blast radius",
            "catch-all clause makes append semantically inert",
            "local uppercase naming and sequence-step habits",
        ],
    }
    scenario_path = output_dir / "scenario_shared_policy" / "scenario.json"
    _write_json(scenario_path, scenario)

    files = {
        str(path.relative_to(output_dir)): _sha256(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "dataset_id": "msn2026_v8_day2_dev",
        "dataset_version": "0.1.0",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "source": {
            "type": "generated_from_scratch",
            "generator": "tools/build_msn2026_v8_day2_dev.py",
            "uses_legacy_experiment_inputs": False,
            "note": "No old data/experiments/results artifact is read by this generator.",
        },
        "scenario_count": 1,
        "files_sha256": files,
    }
    _write_json(output_dir / "dataset_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=20260811)
    args = parser.parse_args()
    manifest = build(args.output_dir, args.seed)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
