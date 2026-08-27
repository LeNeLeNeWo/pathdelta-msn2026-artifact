#!/usr/bin/env python3
"""Audit whether the v8 full envelope beats strong composite baselines.

This consumes already-produced *development* classifications.  It does not
generate new candidates and must not be reported as an independent result.
Its purpose is to detect when an ablation story is explained by a simpler
conjunction such as "preserve every observed non-target behavior and stay in
the static write scope".
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence


BASELINES = {
    "goal_only": "Syntax and requested target behavior only",
    "write_scope": "Goal plus static write-scope budget",
    "dependency_only": "Goal plus protected dependency frame",
    "preserve_observed": "Goal plus preservation of every observed complement behavior",
    "preserve_observed_plus_scope": "Observed-complement preservation plus static write scope",
    "full_envelope": "Semantic frame, dependency frame, and hard footprint",
}


def classify(row: Mapping[str, Any]) -> Dict[str, bool]:
    accepted = row["accepted"]
    return {
        "goal_only": bool(accepted["V1_goal"]),
        "write_scope": bool(accepted["V2_write_scope"]),
        "dependency_only": bool(accepted["V3_dependency"]),
        "preserve_observed": bool(accepted["V4_semantic_frame"]),
        "preserve_observed_plus_scope": bool(
            accepted["V4_semantic_frame"] and accepted["V2_write_scope"]
        ),
        "full_envelope": bool(accepted["V5_full_envelope"]),
    }


def aggregate(rows: Sequence[Mapping[str, Any]], method: str) -> Dict[str, Any]:
    safe = [row for row in rows if row["ground_truth"]["semantically_acceptable"]]
    unsafe = [row for row in rows if not row["ground_truth"]["semantically_acceptable"]]
    collateral = [
        row
        for row in rows
        if row["ground_truth"]["target_satisfied"]
        and row["ground_truth"]["collateral_semantic_change"]
    ]
    safe_accept = sum(row["nontriviality_acceptance"][method] for row in safe)
    unsafe_accept = sum(row["nontriviality_acceptance"][method] for row in unsafe)
    collateral_accept = sum(row["nontriviality_acceptance"][method] for row in collateral)
    return {
        "method": method,
        "description": BASELINES[method],
        "safe_count": len(safe),
        "safe_accepted": safe_accept,
        "unsafe_count": len(unsafe),
        "unsafe_rejected": len(unsafe) - unsafe_accept,
        "goal_satisfying_collateral_count": len(collateral),
        "goal_satisfying_collateral_accepted": collateral_accept,
        "safe_acceptance_rate": safe_accept / len(safe) if safe else None,
        "unsafe_rejection_recall": (len(unsafe) - unsafe_accept) / len(unsafe) if unsafe else None,
    }


def run(input_path: Path, output_root: Path) -> Dict[str, Any]:
    source_rows = json.loads(input_path.read_text(encoding="utf-8"))
    rows = []
    for source in source_rows:
        row = dict(source)
        row["nontriviality_acceptance"] = classify(source)
        rows.append(row)

    summary = [aggregate(rows, method) for method in BASELINES]
    composite = next(row for row in summary if row["method"] == "preserve_observed_plus_scope")
    full = next(row for row in summary if row["method"] == "full_envelope")
    indistinguishable = all(
        row["nontriviality_acceptance"]["preserve_observed_plus_scope"]
        == row["nontriviality_acceptance"]["full_envelope"]
        for row in rows
    )
    conclusion = {
        "development_only": True,
        "candidate_count": len(rows),
        "composite_and_full_identical_on_every_candidate": indistinguishable,
        "composite_safe_accepted": composite["safe_accepted"],
        "composite_unsafe_rejected": composite["unsafe_rejected"],
        "full_safe_accepted": full["safe_accepted"],
        "full_unsafe_rejected": full["unsafe_rejected"],
        "interpretation": (
            "Current benchmark does not isolate the dependency component: a simpler observed-"
            "complement-preservation plus scope conjunction is observationally equivalent."
            if indistinguishable
            else "Current benchmark contains candidates that distinguish the full envelope from the composite baseline."
        ),
    }

    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "candidate_results.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_root / "summary.json").write_text(
        json.dumps({"methods": summary, "conclusion": conclusion}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (output_root / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    return {"methods": summary, "conclusion": conclusion}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("results/msn2026_v8_rq1_dev/rq1_candidate_results.json"),
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("results/msn2026_v8_nontriviality_dev")
    )
    args = parser.parse_args()
    print(json.dumps(run(args.input, args.output_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
