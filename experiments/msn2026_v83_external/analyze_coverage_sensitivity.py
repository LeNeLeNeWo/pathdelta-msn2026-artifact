#!/usr/bin/env python3
"""Sensitivity of external verdicts to missing passive behavior records."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.msn2026_v83_external.run_external_boundaries import (
    envelope,
    load,
    method_verdicts,
    records,
    report,
    write,
    wilson,
)


SEED = 83191
LEVELS = (0.10, 0.25, 0.50)
REPLICATES = 20


def stable_seed(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16)


def choose_removed(
    passive: list[Any], target_id: str, level: float, policy: str, victim_ids: set[str], sid: str, replicate: int
) -> set[str]:
    eligible = [row.behavior_id for row in passive if row.behavior_id != target_id]
    count = min(len(eligible), max(1, round(level * len(eligible)))) if eligible else 0
    rng = random.Random(stable_seed(SEED, sid, level, policy, replicate))
    if policy == "random":
        rng.shuffle(eligible)
        return set(eligible[:count])
    priority = [item for item in eligible if item in victim_ids]
    rest = [item for item in eligible if item not in victim_ids]
    rng.shuffle(priority)
    rng.shuffle(rest)
    return set((priority + rest)[:count])


def metric(rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    safe = [row for row in rows if row["safe"]]
    unsafe = [row for row in rows if not row["safe"]]
    ua = sum(row[method] for row in unsafe)
    sr = sum(not row[method] for row in safe)
    return {
        "safe_count": len(safe),
        "unsafe_count": len(unsafe),
        "unsafe_accepted": ua,
        "unsafe_false_accept_rate": ua / len(unsafe) if unsafe else None,
        "unsafe_false_accept_wilson95": wilson(ua, len(unsafe)),
        "safe_rejected": sr,
        "safe_false_reject_rate": sr / len(safe) if safe else None,
        "safe_false_reject_wilson95": wilson(sr, len(safe)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v83_external"))
    parser.add_argument("--boundary-root", type=Path, default=Path("results/msn2026_v83_external/external_boundaries"))
    parser.add_argument("--output", type=Path, default=Path("results/msn2026_v83_external/coverage_sensitivity.json"))
    args = parser.parse_args()
    labels = {
        (row["scenario_id"], row["mode"]): row["oracle"]
        for row in load(args.boundary_root / "unblinded_results.json")
        if row["oracle"]["goal_success"]
    }
    victim_by_scenario: dict[str, set[str]] = {}
    for (sid, _mode), label in labels.items():
        for atom in label.get("collateral_atoms", []):
            victim_by_scenario.setdefault(sid, set()).add(atom.rsplit("::", 1)[0])
    conditions = []
    index = load(args.data_root / "scenario_index.json")
    for policy in ("random", "adversarial"):
        for level in LEVELS:
            replicate_summaries = []
            for replicate in range(REPLICATES):
                outcomes = []
                coverage = []
                for metadata in index:
                    sid = metadata["scenario_id"]
                    scenario = args.data_root / "public/scenarios" / sid
                    intent = load(scenario / "intent.json")
                    device = metadata["device"]
                    baseline = {device: (scenario / "baseline/configs" / f"{device}.conf").read_text(encoding="utf-8")}
                    passive = records(load(scenario / "passive_observations.json"))
                    active = records(load(args.data_root / "sealed/oracles" / sid / "active_observations.json"))
                    target_id = f"{device}|{metadata['subject']}|{metadata['target_prefix']}"
                    removed = choose_removed(passive, target_id, level, policy, victim_by_scenario.get(sid, set()), sid, replicate)
                    remaining = [row for row in passive if row.behavior_id not in removed]
                    active_universe = remaining + active
                    passive_env = envelope(intent, baseline, remaining, "coverage_sensitivity_passive")
                    active_env = envelope(intent, baseline, active_universe, "coverage_sensitivity_active")
                    coverage.append({
                        "scenario_id": sid,
                        "removed": len(removed),
                        "passive_before": len(passive),
                        "passive_after": len(remaining),
                        "active_witnesses": len(active),
                        "discovered_fraction": len(active_universe) / (len(passive) + len(active)),
                        "removed_victim_records": len(removed & victim_by_scenario.get(sid, set())),
                    })
                    for candidate_path in sorted((args.data_root / "public/candidates" / sid).glob("*.json")):
                        mode = candidate_path.stem
                        label = labels.get((sid, mode))
                        if label is None:
                            continue
                        candidate = load(candidate_path)["candidate_configs"]
                        passive_report = report(baseline, candidate, remaining, passive_env)
                        active_report = report(baseline, candidate, active_universe, active_env)
                        pv = method_verdicts(passive_report, passive_report)
                        av = method_verdicts(active_report, active_report)
                        outcomes.append({
                            "safe": bool(label["safe"]),
                            "passive": pv["full_envelope"],
                            "active": av["full_envelope"],
                            # A known missing hard class with no evidence is an
                            # explicit fail-closed policy, not a PASS.
                            "active_fail_closed_known_gap": bool(av["full_envelope"] and not removed),
                        })
                replicate_summaries.append({
                    "replicate": replicate,
                    "passive": metric(outcomes, "passive"),
                    "active": metric(outcomes, "active"),
                    "active_fail_closed_known_gap": metric(outcomes, "active_fail_closed_known_gap"),
                    "mean_discovered_fraction": statistics.mean(row["discovered_fraction"] for row in coverage),
                    "removed_records": sum(row["removed"] for row in coverage),
                    "removed_victim_records": sum(row["removed_victim_records"] for row in coverage),
                })
            conditions.append({
                "removal_policy": policy,
                "removal_fraction": level,
                "replicates": REPLICATES,
                "replicate_results": replicate_summaries,
                "mean_active_unsafe_false_accept_rate": statistics.mean(row["active"]["unsafe_false_accept_rate"] for row in replicate_summaries),
                "max_active_unsafe_false_accept_rate": max(row["active"]["unsafe_false_accept_rate"] for row in replicate_summaries),
                "mean_passive_unsafe_false_accept_rate": statistics.mean(row["passive"]["unsafe_false_accept_rate"] for row in replicate_summaries),
                "mean_active_safe_false_reject_rate": statistics.mean(row["active"]["safe_false_reject_rate"] for row in replicate_summaries),
                "mean_discovered_fraction": statistics.mean(row["mean_discovered_fraction"] for row in replicate_summaries),
            })
            print(policy, level, conditions[-1]["mean_passive_unsafe_false_accept_rate"], conditions[-1]["mean_active_unsafe_false_accept_rate"])
    output = {
        "schema_version": "msn2026-v83-coverage-sensitivity-1.0",
        "seed": SEED,
        "replicates": REPLICATES,
        "levels": LEVELS,
        "conditions": conditions,
        "interpretation": "Active witnesses are finite evidence. Known uncovered hard classes fail closed; silent unknown gaps remain outside any empirical guarantee.",
    }
    write(args.output, output)
    print(args.output)


if __name__ == "__main__":
    main()
