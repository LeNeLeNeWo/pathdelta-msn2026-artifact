#!/usr/bin/env python3
"""Freeze the selected repair-v2 implementation before the 79-case replay."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FILES = [
    Path("experiments/msn2026_v84_agent_repair/run_agent128_repair_v2.py"),
    Path("experiments/msn2026_v84_agent_repair/development_protocol.md"),
    Path("experiments/msn2026_v83_external/run_agent128.py"),
    Path("experiments/msn2026_v83_external/vendor_policy_adapter.py"),
    Path("experiments/msn2026_v8_day2_agent/change_envelope_v2.py"),
    Path("experiments/msn2026_v8_day2_agent/semantic_metrics.py"),
    Path("experiments/msn2026_v8_day2_agent/llm_client_v2.py"),
    Path("data/msn2026_v83_external/agent128_subset.json"),
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    output = ROOT / "experiments/msn2026_v84_agent_repair/agent_repair_v2_freeze.json"
    if output.exists():
        raise SystemExit(f"freeze already exists: {output}")
    payload = {
        "schema_version": "msn2026-v84-agent-repair-freeze-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "FROZEN_AFTER_12_CASE_DEVELOPMENT_AND_4_CASE_THINKING_AB",
        "claim_scope": "post-freeze engineering replay; not v8.3 confirmatory evidence",
        "selection": {
            "development_corpus": "the 79 immutable v8.3 Full Envelope exhaustions",
            "first_candidates": "byte-identical v8.3 Agent128 candidates",
            "pilot12": "first 12 failures in the label-independent Agent128 order",
            "thinking_ab4": "four named Cisco failures from pilot12",
        },
        "fixed_parameters": {
            "model_env": "DEEPSEEK_MODEL",
            "thinking_mode": "enabled",
            "temperature": 0.1,
            "max_submissions": 8,
            "max_completion_tokens": 8000,
            "feedback_patch_free": True,
            "clean_context_each_attempt": True,
        },
        "engineering_target": {
            "recover_old_exhaustions_at_least": 41,
            "combined_completion_at_least": 90,
            "combined_denominator": 128,
            "unsafe_release_required": 0,
        },
        "inputs": [{"path": str(path), "sha256": digest(ROOT / path)} for path in FILES],
    }
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(output)
    print(digest(output))


if __name__ == "__main__":
    main()
