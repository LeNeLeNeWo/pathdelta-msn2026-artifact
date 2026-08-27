#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.msn2026_v8_day2_agent.agent import run_from_dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v8_day2_dev"))
    parser.add_argument(
        "--output", type=Path, default=Path("results/msn2026_v8_day2_dev/agent_smoke.json")
    )
    parser.add_argument("--max-steps", type=int, default=10)
    args = parser.parse_args()
    result = run_from_dataset(args.data_root, args.output, max_steps=args.max_steps)
    summary = {
        "status": result.status,
        "steps": result.steps,
        "tool_calls": result.tool_calls,
        "patch_submissions": result.patch_submissions,
        "agent_revision_count": result.agent_revision_count,
        "llm_calls": result.api_metrics.llm_calls,
        "api_retry_count": result.api_metrics.api_retry_count,
        "token_usage": {
            "prompt": result.api_metrics.prompt_tokens,
            "completion": result.api_metrics.completion_tokens,
            "total": result.api_metrics.total_tokens,
        },
        "backend": {
            "provider": result.api_metrics.provider,
            "base_url": result.api_metrics.base_url,
            "model": result.api_metrics.model,
        },
    }
    manifest = {
        "schema_version": "1.0.0-dev",
        "run_id": "msn2026_v8_day2_agent_smoke",
        "status": result.status,
        "scope": "single_case_agent_smoke_not_paper_result",
        "seed": 20260811,
        "dataset_manifest": str((args.data_root / "dataset_manifest.json").resolve()),
        "backend": summary["backend"],
        "metrics": {
            "llm_calls": result.api_metrics.llm_calls,
            "api_retry_count": result.api_metrics.api_retry_count,
            "agent_revision_count": result.agent_revision_count,
            "patch_submissions": result.patch_submissions,
            "token_usage": summary["token_usage"],
            "latency_ms": result.api_metrics.latency_ms,
            "tool_calls": result.tool_calls,
        },
        "artifact": {
            "path": str(args.output.resolve()),
            "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        },
    }
    manifest_path = args.output.with_name("agent_run_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
