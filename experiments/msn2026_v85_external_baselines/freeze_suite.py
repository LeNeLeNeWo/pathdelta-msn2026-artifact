#!/usr/bin/env python3
"""Freeze label-independent pilot/confirmatory splits and prompt receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/msn2026_v85_external_baselines"
HERE = Path(__file__).resolve().parent
SEED = 20260815
METHODS = [
    "llm_netcfg_adapted",
    "inta_adapted",
    "cosynth_vpp_adapted",
    "cornetto_agentic_adapted",
    "pathdelta_fullr",
]


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(cases: list[dict[str, Any]]) -> str:
    material = "\n".join(row["scenario_id"] for row in cases) + "\n"
    return hashlib.sha256(material.encode()).hexdigest()


def split() -> None:
    index = load(DATA / "scenario_index.json")
    if len(index) != 48:
        raise ValueError(f"expected 48 cases, found {len(index)}")
    cases = [{
        "case_id": row["scenario_id"],
        "scenario_id": row["scenario_id"],
        "source_scenario": row["source_scenario"],
        "vendor": row["vendor"],
        "family": row["family"],
    } for row in index]
    rng = random.Random(SEED)
    rng.shuffle(cases)
    pilot, confirmatory = cases[:8], cases[8:]
    if {row["source_scenario"] for row in pilot} & {row["source_scenario"] for row in confirmatory}:
        raise AssertionError("source-scenario leakage across splits")
    base = {
        "schema_version": "msn2026-v85-suite-split-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "selection_used_labels": False,
        "selection_opened_sealed_oracles": False,
        "development_source_configurations_excluded": True,
        "topology_disjoint_count": load(DATA / "dataset_manifest.json")["topology_disjoint_count"],
        "dataset_manifest_sha256": sha256(DATA / "dataset_manifest.json"),
        "source_manifest_sha256": sha256(DATA / "source_manifest.json"),
    }
    for name, rows in (("pilot8", pilot), ("confirmatory40", confirmatory), ("all48", cases)):
        write(DATA / "splits" / f"{name}.json", {
            **base,
            "split": name,
            "case_count": len(rows),
            "aggregate_sha256": aggregate(rows),
            "cases": rows,
        })
    receipt = {
        **base,
        "pilot8_sha256": sha256(DATA / "splits/pilot8.json"),
        "confirmatory40_sha256": sha256(DATA / "splits/confirmatory40.json"),
        "all48_sha256": sha256(DATA / "splits/all48.json"),
    }
    write(HERE / "split_freeze.json", receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))


def confirmatory() -> None:
    runner = HERE / "run_comparison.py"
    adapters = HERE / "method_adapters.py"
    prompts = HERE / "method_prompts.py"
    protocol = HERE / "preregistered_protocol.md"
    pilot_log = HERE / "pilot_development_log.md"
    source_manifest = DATA / "source_manifest.json"
    dataset_manifest = DATA / "dataset_manifest.json"
    required = [
        runner, adapters, prompts, protocol, pilot_log, source_manifest,
        dataset_manifest,
        DATA / "splits/confirmatory40.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(missing)
    freeze = {
        "schema_version": "msn2026-v85-confirmatory-freeze-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": "confirmatory",
        "methods": METHODS,
        "split_path": "data/msn2026_v85_external_baselines/splits/confirmatory40.json",
        "split_sha256": sha256(DATA / "splits/confirmatory40.json"),
        "runner_sha256": sha256(runner),
        "adapters_sha256": sha256(adapters),
        "prompts_sha256": sha256(prompts),
        "protocol_sha256": sha256(protocol),
        "pilot_development_log_sha256": sha256(pilot_log),
        "source_manifest_sha256": sha256(source_manifest),
        "dataset_manifest_sha256": sha256(dataset_manifest),
        "max_logical_llm_calls": 12,
        "max_completion_tokens_per_call": 5000,
        "max_completion_tokens_per_case": 40000,
        "temperature": 0.0,
        "selection_used_labels": False,
        "prompt_changes_after_this_freeze_forbidden": True,
    }
    write(HERE / "confirmatory_freeze.json", freeze)
    print(json.dumps(freeze, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=("split", "confirmatory"))
    args = parser.parse_args()
    if args.stage == "split":
        split()
    else:
        confirmatory()


if __name__ == "__main__":
    main()
