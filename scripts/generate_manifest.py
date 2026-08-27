#!/usr/bin/env python3
"""Generate the deterministic artifact hash manifest."""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "MANIFEST.sha256"
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache"}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


rows = []
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or path == OUTPUT or any(part in SKIP_DIRS for part in path.parts):
        continue
    rows.append(f"{digest(path)}  {path.relative_to(ROOT).as_posix()}")
OUTPUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
print(f"Wrote {OUTPUT.relative_to(ROOT)} with {len(rows)} entries.")
