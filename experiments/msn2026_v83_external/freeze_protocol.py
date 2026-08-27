#!/usr/bin/env python3
"""Freeze the v8.3 review-response protocol and immutable mechanism inputs."""

from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name("protocol_freeze.json")
INPUTS = (
    Path("experiments/msn2026_v83_external/preregistered_protocol.md"),
    Path("experiments/msn2026_v8_day2_agent/change_envelope_v2.py"),
    Path("experiments/msn2026_v8_day2_agent/change_envelope.schema.json"),
    Path("experiments/msn2026_v8_day2_agent/counterexample_feedback.py"),
    Path("experiments/msn2026_v8_day2_agent/counterexample_feedback.schema.json"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    records = []
    aggregate = hashlib.sha256()
    for relative in INPUTS:
        path = PROJECT / relative
        digest = sha256(path)
        records.append({"path": relative.as_posix(), "sha256": digest})
        aggregate.update(relative.as_posix().encode())
        aggregate.update(b"\0")
        aggregate.update(digest.encode())
        aggregate.update(b"\n")
    payload = {
        "schema_version": "msn2026-v83-protocol-freeze-1.0",
        "protocol_version": "msn2026-v83-1.0.0",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "inputs": records,
        "aggregate_sha256": aggregate.hexdigest(),
        "policy": "No result-dependent changes to the frozen mechanism or protocol.",
    }
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()

