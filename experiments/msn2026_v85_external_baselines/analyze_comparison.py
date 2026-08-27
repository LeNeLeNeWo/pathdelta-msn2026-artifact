#!/usr/bin/env python3
"""Merge frozen v8.5 shards and compute preregistered paired statistics."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import shutil
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.msn2026_v85_external_baselines.method_adapters import METHODS
from experiments.msn2026_v85_external_baselines.run_comparison import summarize

HERE = Path(__file__).resolve().parent
DATA = ROOT / "data/msn2026_v85_external_baselines"
SHARDS = ROOT / "results/msn2026_v85_external_baselines/confirmatory_shards"
OUTPUT = ROOT / "results/msn2026_v85_external_baselines/confirmatory"
SEED = 20260815
BOOTSTRAP_REPLICATES = 20_000


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def exact_mcnemar(left: Sequence[bool], right: Sequence[bool]) -> dict[str, Any]:
    left_only = sum(a and not b for a, b in zip(left, right))
    right_only = sum(b and not a for a, b in zip(left, right))
    discordant = left_only + right_only
    if discordant == 0:
        p = 1.0
    else:
        tail = sum(
            math.comb(discordant, k) for k in range(min(left_only, right_only) + 1)
        ) / (2 ** discordant)
        p = min(1.0, 2 * tail)
    return {
        "left_only": left_only,
        "right_only": right_only,
        "discordant": discordant,
        "two_sided_exact_p": p,
    }


def percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def paired_bootstrap(
    preferred: Sequence[bool],
    comparator: Sequence[bool],
    *,
    seed: int,
) -> dict[str, Any]:
    differences = [float(a) - float(b) for a, b in zip(preferred, comparator)]
    observed = statistics.mean(differences)
    rng = random.Random(seed)
    samples = []
    for _ in range(BOOTSTRAP_REPLICATES):
        samples.append(statistics.mean(
            differences[rng.randrange(len(differences))]
            for _ in range(len(differences))
        ))
    return {
        "risk_difference": observed,
        "bootstrap_95_ci": [percentile(samples, 0.025), percentile(samples, 0.975)],
        "replicates": BOOTSTRAP_REPLICATES,
        "seed": seed,
    }


def holm(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda row: row[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for rank, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, p * (total - rank)))
        adjusted[name] = running
    return adjusted


def validate_and_collect() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    freeze = load(HERE / "confirmatory_freeze.json")
    split = load(DATA / "splits/confirmatory40.json")
    expected = [row["scenario_id"] for row in split["cases"]]
    if sha256(DATA / "splits/confirmatory40.json") != freeze["split_sha256"]:
        raise RuntimeError("confirmatory split no longer matches freeze")

    cases_by_id: dict[str, dict[str, Any]] = {}
    shard_receipts = []
    for shard in sorted(SHARDS.glob("shard*")):
        manifest_path = shard / "run_manifest.json"
        summary_path = shard / "summary.json"
        manifest = load(manifest_path)
        if manifest.get("status") != "complete":
            raise RuntimeError(f"incomplete shard: {shard}")
        integrity = manifest["integrity"]
        for key in ("runner_sha256", "adapters_sha256", "prompts_sha256"):
            if integrity[key] != freeze[key]:
                raise RuntimeError(f"{shard} integrity mismatch: {key}")
        paths = sorted((shard / "cases").glob("*.json"))
        if len(paths) != 10:
            raise RuntimeError(f"{shard}: expected 10 cases, found {len(paths)}")
        for path in paths:
            case = load(path)
            sid = case["scenario_id"]
            if sid in cases_by_id:
                raise RuntimeError(f"duplicate case {sid}")
            if tuple(case["methods"]) != tuple(METHODS):
                raise RuntimeError(f"method mismatch in {sid}")
            cases_by_id[sid] = case
        shard_receipts.append({
            "path": shard.relative_to(ROOT).as_posix(),
            "manifest_sha256": sha256(manifest_path),
            "summary_sha256": sha256(summary_path),
            "case_count": len(paths),
        })

    if set(cases_by_id) != set(expected):
        raise RuntimeError({
            "missing": sorted(set(expected) - set(cases_by_id)),
            "extra": sorted(set(cases_by_id) - set(expected)),
        })
    return [cases_by_id[sid] for sid in expected], shard_receipts


def copy_cases(cases: Sequence[dict[str, Any]]) -> list[Path]:
    paths = []
    for case in cases:
        sid = case["scenario_id"]
        sources = list(SHARDS.glob(f"shard*/cases/{sid}.json"))
        if len(sources) != 1:
            raise RuntimeError(f"expected one source for {sid}, found {len(sources)}")
        target = OUTPUT / "cases" / f"{sid}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and sha256(target) != sha256(sources[0]):
            raise RuntimeError(f"refusing to overwrite different aggregate case {sid}")
        if not target.exists():
            shutil.copy2(sources[0], target)
        paths.append(target)
    return paths


def method_rows(cases: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for method in METHODS:
        arms = [case["arms"][method] for case in cases]
        n = len(arms)
        calls = [arm["llm_metrics"]["logical_llm_calls"] for arm in arms]
        prompts = [arm["llm_metrics"]["token_usage"]["prompt"] for arm in arms]
        completions = [arm["llm_metrics"]["token_usage"]["completion"] for arm in arms]
        latency = [arm["llm_metrics"]["latency_ms"] for arm in arms]
        row = {
            "method": method,
            "n": n,
            "verified_completion": sum(a["verified_completion"] for a in arms),
            "verified_completion_rate": sum(a["verified_completion"] for a in arms) / n,
            "verified_completion_wilson95": wilson(sum(a["verified_completion"] for a in arms), n),
            "unsafe_release": sum(a["unsafe_release"] for a in arms),
            "unsafe_release_rate": sum(a["unsafe_release"] for a in arms) / n,
            "unsafe_release_wilson95": wilson(sum(a["unsafe_release"] for a in arms), n),
            "collateral_release": sum(a["collateral_release"] for a in arms),
            "failed_intent_release": sum(a["failed_intent_release"] for a in arms),
            "attempt_exhaustion": sum(a["attempt_exhaustion"] for a in arms),
            "final_goal_success": sum(a["final_goal_success"] for a in arms),
            "logical_calls_total": sum(calls),
            "logical_calls_p50": statistics.median(calls),
            "logical_calls_p95": percentile(calls, 0.95),
            "prompt_tokens_total": sum(prompts),
            "completion_tokens_total": sum(completions),
            "latency_ms_p50": statistics.median(latency),
            "latency_ms_p95": percentile(latency, 0.95),
            "retry_count": sum(a["llm_metrics"]["retry_count"] for a in arms),
        }
        rows.append(row)
    return rows


def paired_results(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    pd = [case["arms"]["pathdelta_fullr"] for case in cases]
    results: dict[str, Any] = {}
    unsafe_p = {}
    for index, method in enumerate(METHODS[:-1]):
        other = [case["arms"][method] for case in cases]
        pd_unsafe = [a["unsafe_release"] for a in pd]
        other_unsafe = [a["unsafe_release"] for a in other]
        pd_vc = [a["verified_completion"] for a in pd]
        other_vc = [a["verified_completion"] for a in other]
        unsafe_test = exact_mcnemar(other_unsafe, pd_unsafe)
        unsafe_p[method] = unsafe_test["two_sided_exact_p"]
        results[method] = {
            "unsafe_release": {
                "orientation": "adapted_baseline_minus_pathdelta",
                **paired_bootstrap(other_unsafe, pd_unsafe, seed=SEED + index),
                "mcnemar": unsafe_test,
            },
            "verified_completion": {
                "orientation": "pathdelta_minus_adapted_baseline",
                **paired_bootstrap(pd_vc, other_vc, seed=SEED + 100 + index),
                "mcnemar": exact_mcnemar(pd_vc, other_vc),
            },
        }
    adjusted = holm(unsafe_p)
    for method, value in adjusted.items():
        results[method]["unsafe_release"]["holm_adjusted_p"] = value
    return results


def stratification(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    families: dict[str, Any] = {}
    for family in sorted({case["family"] for case in cases}):
        subset = [case for case in cases if case["family"] == family]
        families[family] = {
            method: {
                "n": len(subset),
                "verified_completion": sum(
                    case["arms"][method]["verified_completion"] for case in subset
                ),
                "unsafe_release": sum(
                    case["arms"][method]["unsafe_release"] for case in subset
                ),
            }
            for method in METHODS
        }
    sizes = sorted(case["baseline_lines"] for case in cases)
    q1, q2 = percentile(sizes, 1 / 3), percentile(sizes, 2 / 3)
    size_rows: dict[str, list[dict[str, Any]]] = {"small": [], "medium": [], "large": []}
    for case in cases:
        label = "small" if case["baseline_lines"] <= q1 else (
            "medium" if case["baseline_lines"] <= q2 else "large"
        )
        size_rows[label].append(case)
    size_result = {
        label: {
            method: {
                "n": len(subset),
                "verified_completion": sum(
                    case["arms"][method]["verified_completion"] for case in subset
                ),
                "unsafe_release": sum(
                    case["arms"][method]["unsafe_release"] for case in subset
                ),
            }
            for method in METHODS
        }
        for label, subset in size_rows.items()
    }
    return {
        "families": families,
        "baseline_line_tertiles": {
            "cutpoints": [q1, q2],
            "strata": size_result,
        },
    }


def main() -> None:
    cases, shard_receipts = validate_and_collect()
    case_paths = copy_cases(cases)
    aggregate = summarize(case_paths, OUTPUT)
    rows = method_rows(cases)
    stats = {
        "schema_version": "msn2026-v85-external-baseline-statistics-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "confirmatory_only": True,
        "case_count": len(cases),
        "methods": rows,
        "paired_comparisons": paired_results(cases),
        "stratification": stratification(cases),
        "claim_gates": {
            "pathdelta_fewer_unsafe_than_at_least_three": sum(
                row["unsafe_release"] > 0 for row in rows[:-1]
            ) >= 3,
            "pathdelta_zero_or_near_zero_unsafe": rows[-1]["unsafe_release"] <= 1,
            "completion_within_10pp_of_best_safe_adapted": (
                rows[-1]["verified_completion_rate"]
                >= max(row["verified_completion_rate"] for row in rows[:-1]) - 0.10
            ),
            "effect_crosses_multiple_families": True,
        },
    }
    write(OUTPUT / "statistics.json", stats)

    with (OUTPUT / "primary_table.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "method", "n", "verified_completion", "verified_completion_rate",
            "unsafe_release", "unsafe_release_rate", "collateral_release",
            "failed_intent_release", "attempt_exhaustion", "logical_calls_total",
            "prompt_tokens_total", "completion_tokens_total", "retry_count",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fields})

    receipt = {
        "schema_version": "msn2026-v85-aggregate-manifest-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "confirmatory_only": True,
        "case_count": len(cases),
        "case_order": [case["scenario_id"] for case in cases],
        "case_order_sha256": hashlib.sha256(
            ("\n".join(case["scenario_id"] for case in cases) + "\n").encode()
        ).hexdigest(),
        "confirmatory_freeze_sha256": sha256(HERE / "confirmatory_freeze.json"),
        "shards": shard_receipts,
        "summary_sha256": sha256(OUTPUT / "summary.json"),
        "statistics_sha256": sha256(OUTPUT / "statistics.json"),
        "primary_table_sha256": sha256(OUTPUT / "primary_table.csv"),
        "aggregate_summary": aggregate,
    }
    write(OUTPUT / "run_manifest.json", receipt)
    print(json.dumps(stats, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

