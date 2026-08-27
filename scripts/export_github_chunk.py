#!/usr/bin/env python3
"""Emit a base64 JSON chunk of Git-tracked files for release tooling."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk", type=int, required=True)
    parser.add_argument("--size", type=int, default=20)
    args = parser.parse_args()
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    paths = [item.decode("utf-8") for item in raw.split(b"\0") if item]
    selected = paths[args.chunk * args.size : (args.chunk + 1) * args.size]
    rows = []
    for relative in selected:
        data = (ROOT / relative).read_bytes()
        try:
            text = data.decode("utf-8")
            binary = False
        except UnicodeDecodeError:
            text = None
            binary = True
        rows.append(
            {
                "path": relative,
                "binary": binary,
                "base64": base64.b64encode(data).decode("ascii") if binary else None,
                "content": text,
            }
        )
    print(json.dumps({"total": len(paths), "rows": rows}, separators=(",", ":")))


if __name__ == "__main__":
    main()
