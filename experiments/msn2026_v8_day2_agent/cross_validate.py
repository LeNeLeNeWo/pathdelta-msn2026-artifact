#!/usr/bin/env python3
"""Cross-check an accepted agent patch with real FRR syntax validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.msn2026_v8_day2_agent.change_envelope import SearchReplaceEdit, apply_search_replace_edits
from experiments.static_smoke.static_validator import StaticValidator


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(data_root: Path, agent_result_path: Path, output_root: Path) -> dict:
    record = json.loads(agent_result_path.read_text(encoding="utf-8"))
    accepted = record.get("accepted_candidate")
    if not accepted or not accepted.get("accepted"):
        raise RuntimeError("agent result has no accepted candidate")
    raw_edits = accepted.get("submitted_edits") or []
    edits = [SearchReplaceEdit(**item) for item in raw_edits]
    baseline_path = data_root / "scenario_shared_policy" / "configs" / "edge-1.conf"
    baseline = baseline_path.read_text(encoding="utf-8")
    candidate = apply_search_replace_edits({"edge-1": baseline}, edits)["edge-1"]

    output_root.mkdir(parents=True, exist_ok=True)
    candidate_path = output_root / "accepted_candidate.conf"
    candidate_path.write_text(candidate, encoding="utf-8")
    syntax = StaticValidator().check_syntax(candidate)
    result = {
        "candidate_sha256": _sha256(candidate_path),
        "baseline_sha256": _sha256(baseline_path),
        "frr_syntax": {
            "available": syntax.available,
            "method": syntax.method,
            "passed": syntax.passed,
            "error_message": syntax.error_message,
        },
        "accepted_by_change_envelope": True,
        "note": "Small cross-check only; no paper-scale dynamic experiment was run.",
    }
    output_path = output_root / "cross_validation.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v8_day2_dev"))
    parser.add_argument(
        "--agent-result", type=Path, default=Path("results/msn2026_v8_day2_dev/agent_smoke.json")
    )
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v8_day2_dev"))
    args = parser.parse_args()
    print(json.dumps(run(args.data_root, args.agent_result, args.output_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

