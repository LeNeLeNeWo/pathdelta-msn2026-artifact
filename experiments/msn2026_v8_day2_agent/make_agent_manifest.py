#!/usr/bin/env python3
"""Materialize a run manifest from an already completed agent smoke artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-result", type=Path, default=Path("results/msn2026_v8_day2_dev/agent_smoke.json"))
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v8_day2_dev"))
    parser.add_argument(
        "--output", type=Path, default=Path("results/msn2026_v8_day2_dev/agent_run_manifest.json")
    )
    args = parser.parse_args()
    record = json.loads(args.agent_result.read_text(encoding="utf-8"))
    api = record["api_metrics"]
    manifest = {
        "schema_version": "1.0.0-dev",
        "run_id": "msn2026_v8_day2_agent_smoke",
        "status": record["status"],
        "scope": "single_case_agent_smoke_not_paper_result",
        "seed": 20260811,
        "dataset_manifest": str((args.data_root / "dataset_manifest.json").resolve()),
        "backend": {
            "provider": api["provider"],
            "base_url": api["base_url"],
            "model": api["model"],
        },
        "metrics": {
            "llm_calls": api["llm_calls"],
            "api_retry_count": api["api_retry_count"],
            "agent_revision_count": record["agent_revision_count"],
            "patch_submissions": record["patch_submissions"],
            "token_usage": {
                "prompt": api["prompt_tokens"],
                "completion": api["completion_tokens"],
                "total": api["total_tokens"],
            },
            "latency_ms": api["latency_ms"],
            "tool_calls": record["tool_calls"],
        },
        "artifact": {
            "path": str(args.agent_result.resolve()),
            "sha256": hashlib.sha256(args.agent_result.read_bytes()).hexdigest(),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

