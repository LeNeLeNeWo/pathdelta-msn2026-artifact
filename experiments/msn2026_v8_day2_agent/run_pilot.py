#!/usr/bin/env python3
"""Run the mechanism-only v8 pilot on freshly generated development data."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.msn2026_v8_day2_agent.change_envelope import (
    Day2Intent,
    SearchReplaceEdit,
    derive_change_envelope,
    evaluate_candidate,
    write_envelope_json,
)


def _candidate_edits() -> Dict[str, List[SearchReplaceEdit]]:
    device = "edge-1"
    binding_a = " neighbor 198.51.100.1 route-map RM_EDGE_IN in"
    binding_b = " neighbor 198.51.100.2 route-map RM_EDGE_IN in"
    anchor = "route-map RM_EDGE_IN permit 10"
    end_anchor = "!\nline vty"

    unsafe = [
        SearchReplaceEdit(
            device,
            "ip prefix-list PL_CUSTOMER seq 10 permit 10.0.0.0/8 le 24",
            "ip prefix-list PL_DAY2_TARGET seq 10 permit 203.0.113.0/24\n"
            "ip prefix-list PL_CUSTOMER seq 10 permit 10.0.0.0/8 le 24",
        ),
        SearchReplaceEdit(
            device,
            anchor,
            "route-map RM_EDGE_IN permit 5\n"
            " match ip address prefix-list PL_DAY2_TARGET\n"
            " set local-preference 250\n"
            + anchor,
        ),
    ]

    safe_block = """ip prefix-list PL_DAY2_TARGET seq 10 permit 203.0.113.0/24
route-map RM_EDGE_A_IN permit 10
 match ip address prefix-list PL_DAY2_TARGET
 set local-preference 250
route-map RM_EDGE_A_IN permit 20
 match ip address prefix-list PL_CUSTOMER
 set local-preference 150
route-map RM_EDGE_A_IN permit 30
 match ip address prefix-list PL_ALL
 set local-preference 100
!
line vty"""
    safe = [
        SearchReplaceEdit(device, binding_a, " neighbor 198.51.100.1 route-map RM_EDGE_A_IN in"),
        SearchReplaceEdit(device, end_anchor, safe_block),
    ]

    style_block = """ip prefix-list targetPartner seq 7 permit 203.0.113.0/24
route-map partnerIn permit 7
  match ip address prefix-list targetPartner
  set local-preference 250
route-map partnerIn permit 17
  match ip address prefix-list PL_CUSTOMER
  set local-preference 150
route-map partnerIn permit 27
  match ip address prefix-list PL_ALL
  set local-preference 100
!
line vty"""
    style_mismatch = [
        SearchReplaceEdit(device, binding_a, " neighbor 198.51.100.1 route-map partnerIn in"),
        SearchReplaceEdit(device, end_anchor, style_block),
    ]

    broad_block = safe_block.replace(
        "!\nline vty",
        "route-map RM_EDGE_B_COPY_IN permit 10\n"
        " match ip address prefix-list PL_CUSTOMER\n"
        " set local-preference 150\n"
        "route-map RM_EDGE_B_COPY_IN permit 20\n"
        " match ip address prefix-list PL_ALL\n"
        " set local-preference 100\n"
        "!\nline vty",
    )
    broad = [
        SearchReplaceEdit(device, binding_a, " neighbor 198.51.100.1 route-map RM_EDGE_A_IN in"),
        SearchReplaceEdit(device, binding_b, " neighbor 198.51.100.2 route-map RM_EDGE_B_COPY_IN in"),
        SearchReplaceEdit(device, end_anchor, broad_block),
    ]
    return {
        "unsafe_shared_in_place": unsafe,
        "safe_local_fork": safe,
        "semantic_ok_style_mismatch": style_mismatch,
        "semantic_ok_broad_rewrite": broad,
    }


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "not-a-git-worktree"


def run(data_root: Path, output_root: Path) -> Dict[str, object]:
    scenario_root = data_root / "scenario_shared_policy"
    scenario = json.loads((scenario_root / "scenario.json").read_text(encoding="utf-8"))
    baseline = (scenario_root / "configs" / "edge-1.conf").read_text(encoding="utf-8")
    intent = Day2Intent(**scenario["intent"])
    baseline_configs = {intent.target_device: baseline}
    envelope = derive_change_envelope(baseline_configs, intent, scenario["probe_prefixes"])

    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "change_envelope.json").write_text(write_envelope_json(envelope), encoding="utf-8")
    verdicts = [
        evaluate_candidate(candidate_id, baseline_configs, edits, envelope)
        for candidate_id, edits in _candidate_edits().items()
    ]
    verdict_payload = [verdict.to_dict() for verdict in verdicts]
    (output_root / "candidate_verdicts.json").write_text(
        json.dumps(verdict_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    goal_only_accepted = [row.candidate_id for row in verdicts if row.goal_satisfied]
    smallest_goal_patch = min(
        (row for row in verdicts if row.goal_satisfied), key=lambda row: row.footprint.changed_lines
    )
    envelope_accepted = [row.candidate_id for row in verdicts if row.accepted]
    summary = {
        "pilot_id": "msn2026_v8_day2_change_envelope_dev",
        "not_a_paper_result": True,
        "candidate_count": len(verdicts),
        "goal_only_accepted": goal_only_accepted,
        "textually_smallest_goal_patch": smallest_goal_patch.candidate_id,
        "textually_smallest_goal_patch_lines": smallest_goal_patch.footprint.changed_lines,
        "change_envelope_accepted": envelope_accepted,
        "mechanism_observation": (
            "Goal-only validation accepts collateral, style-inconsistent, and over-broad edits; "
            "textual minimization selects the collateral edit; the derived envelope accepts only the local fork."
        ),
    }
    (output_root / "pilot_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    run_manifest = {
        "schema_version": "1.0.0-dev",
        "run_id": "msn2026_v8_day2_change_envelope_dev",
        "status": "completed",
        "scope": "mechanism_only_small_pilot",
        "dataset_manifest": str((data_root / "dataset_manifest.json").resolve()),
        "code_revision": _git_revision(),
        "seed": 20260811,
        "backend": {
            "kind": "deterministic_candidate_fixture",
            "llm_provider": None,
            "llm_model": None,
            "llm_calls": 0,
            "retry_count": 0,
            "token_usage": {"prompt": 0, "completion": 0, "total": 0},
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "deepseek_env_present": {
                "DEEPSEEK_API_KEY": bool(os.getenv("DEEPSEEK_API_KEY")),
                "DEEPSEEK_BASE_URL": bool(os.getenv("DEEPSEEK_BASE_URL")),
                "DEEPSEEK_MODEL": bool(os.getenv("DEEPSEEK_MODEL")),
            },
        },
        "artifacts": ["change_envelope.json", "candidate_verdicts.json", "pilot_summary.json"],
    }
    (output_root / "run_manifest.json").write_text(
        json.dumps(run_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v8_day2_dev"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v8_day2_dev"))
    args = parser.parse_args()
    print(json.dumps(run(args.data_root, args.output_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

