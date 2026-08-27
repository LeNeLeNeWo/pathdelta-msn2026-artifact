#!/usr/bin/env python3
"""Fail closed on integrity, anonymity, and credential hygiene violations."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.sha256"
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache"}
SKIP_FILES = {"MANIFEST.sha256"}
SKIP_TEXT_FILES = {".gitignore"}
BINARY_SUFFIXES = {".pdf", ".zip", ".png", ".jpg", ".jpeg"}
FORBIDDEN_NAMES = {".env", "blinding_key.json", "id_rsa", "id_ed25519"}
PRIVATE_UNIX_HOME = "/home/" + "yang"
PRIVATE_WINDOWS_HOME = "C:" + "\\\\Users\\\\" + "yang"
TEXT_PATTERNS = {
    "private home path": re.compile(
        rf"(?:{re.escape(PRIVATE_UNIX_HOME)}|{re.escape(PRIVATE_WINDOWS_HOME)})",
        re.I,
    ),
    "machine-specific WSL path": re.compile(r"wsl[.]localhost", re.I),
    "private directory": re.compile(r"(?:^|[/\\])private(?:[/\\]|$)", re.I),
    "credential-shaped token": re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})"),
    "embedded bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._-]{24,}", re.I),
}


def files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify_manifest(errors: list[str]) -> None:
    if not MANIFEST.exists():
        errors.append("MANIFEST.sha256 is missing")
        return
    expected = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sha, relative = line.split("  ", 1)
        expected[relative] = sha
    actual_paths = {
        path.relative_to(ROOT).as_posix()
        for path in files()
        if path.name not in SKIP_FILES
    }
    if actual_paths != set(expected):
        errors.append(
            "manifest file set differs: "
            f"missing={sorted(set(expected) - actual_paths)}, "
            f"unlisted={sorted(actual_paths - set(expected))}"
        )
    for relative in sorted(actual_paths & set(expected)):
        found = digest(ROOT / relative)
        if found != expected[relative]:
            errors.append(f"hash mismatch: {relative}")


def verify_anonymity(errors: list[str]) -> None:
    for path in files():
        relative = path.relative_to(ROOT).as_posix()
        if path.name in FORBIDDEN_NAMES or "private" in {part.lower() for part in path.parts}:
            errors.append(f"forbidden private file/path: {relative}")
        if path.suffix.lower() in BINARY_SUFFIXES or path.name in SKIP_TEXT_FILES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in TEXT_PATTERNS.items():
            if pattern.search(content):
                errors.append(f"{label}: {relative}")


def main() -> int:
    errors: list[str] = []
    verify_manifest(errors)
    verify_anonymity(errors)
    if errors:
        print("Artifact verification FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Artifact verification PASS ({sum(1 for _ in files())} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
