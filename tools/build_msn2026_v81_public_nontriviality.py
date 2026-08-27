#!/usr/bin/env python3
"""Derive frozen nontriviality challenges from freshly downloaded public sources."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Mapping

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/msn2026_v81_public_brownfield"
OUTPUT = ROOT / "data/msn2026_v81_public_nontriviality"
TARGET = "203.0.113.0/24"


def record(device: str, neighbor: str, prefix: str, decision: str, local_pref: int | None) -> Dict[str, Any]:
    return {
        "behavior_id": f"{device}|{neighbor}|{prefix}",
        "device": device,
        "subject": neighbor,
        "fec": prefix,
        "attributes": {"decision": decision, "local_pref": local_pref, "session": "established"},
        "source": "public-source-conditioned-independent-oracle-v1",
    }


def changed(rows: list[Mapping[str, Any]], updates: Mapping[str, Mapping[str, Any]]) -> list[Dict[str, Any]]:
    output = json.loads(json.dumps(rows))
    for row in output:
        row["attributes"].update(updates.get(row["behavior_id"], {}))
        row["source"] = "public-source-conditioned-independent-oracle-v1-post"
    return output


def target_binding(text: str) -> tuple[str, str]:
    match = re.search(r"^\s*neighbor\s+(198\.51\.100\.1)\s+route-map\s+(\S+)\s+in\s*$", text, re.M)
    if not match:
        raise ValueError("target inbound route-map not found")
    return match.group(1), match.group(2)


def route_map_blocks(text: str, name: str) -> str:
    lines = text.splitlines()
    output = []
    active = False
    for line in lines:
        header = re.match(r"^route-map\s+(\S+)\s+", line)
        if header:
            active = header.group(1) == name
        elif line and not line.startswith((" ", "!")):
            active = False
        if active:
            output.append(line)
    if not output:
        raise ValueError(f"route-map {name} has no clauses")
    return "\n".join(output).replace(f"route-map {name} ", "route-map RM_PD81_LOCAL ")


def before_line_vty(text: str, fragment: str) -> str:
    marker = "!\nline vty\n"
    if marker not in text:
        raise ValueError("line vty marker absent")
    return text.replace(marker, f"!\n{fragment.rstrip()}\n!\nline vty\n", 1)


def safe_local_fork(text: str, target_rm: str) -> str:
    clone = route_map_blocks(text, target_rm)
    fragment = (
        f"ip prefix-list PL_PD81_TARGET seq 1 permit {TARGET}\n"
        "route-map RM_PD81_LOCAL permit 1\n"
        " match ip address prefix-list PL_PD81_TARGET\n"
        " set local-preference 250\n"
        f"{clone}"
    )
    return before_line_vty(text, fragment).replace(
        f"neighbor 198.51.100.1 route-map {target_rm} in",
        "neighbor 198.51.100.1 route-map RM_PD81_LOCAL in",
        1,
    )


def unsafe_shared_clause(text: str, shared_rm: str) -> str:
    fragment = (
        f"ip prefix-list PL_PD81_TARGET seq 1 permit {TARGET}\n"
        f"route-map {shared_rm} permit 1\n"
        " match ip address prefix-list PL_PD81_TARGET\n"
        " set local-preference 250"
    )
    return before_line_vty(text, fragment)


def build(output: Path = OUTPUT) -> Dict[str, Any]:
    if output.exists():
        shutil.rmtree(output)
    source_manifest = json.loads((SOURCE / "brownfield_manifest.json").read_text(encoding="utf-8"))
    source_provenance_hash = hashlib.sha256((SOURCE / "source_provenance.json").read_bytes()).hexdigest()
    cases = []
    for case_root in sorted((SOURCE / "cases").iterdir()):
        case_id = case_root.name
        baselines = {path.stem: path.read_text(encoding="utf-8") for path in sorted((case_root / "baseline").glob("*.conf"))}
        device = next(name for name in baselines if name.startswith("edge-"))
        base = baselines[device]
        _, target_rm = target_binding(base)
        visible = [
            record(device, "198.51.100.1", TARGET, "permit", 100),
            record(device, "198.51.100.2", "192.0.2.0/24", "permit", 100),
        ]
        heldout = [
            record(device, "198.51.100.2", TARGET, "permit", 100),
            record(
                device,
                "198.51.100.1",
                "10.1.0.0/24",
                "permit",
                150 if case_id in {"public_kathara_call", "public_kathara_shared_pl"} else 100,
            ),
        ]
        intent = {
            "intent_id": f"v81-{case_id}",
            "raw_text": f"On {device}, set local preference 250 only for {TARGET} from 198.51.100.1.",
            "selector": {"devices": [device], "subjects": ["198.51.100.1"], "fecs": [TARGET]},
            "changes": [{"dimension": "local_pref", "relation": "replace", "desired": 250}],
        }
        safe = safe_local_fork(base, target_rm)

        if case_id == "public_kathara_call":
            unsafe = unsafe_shared_clause(base, "RM_KATHARA_CHILD")
        elif case_id == "public_kathara_shared_pl":
            # This source family requires a decision change so that modifying
            # the shared match object alone can satisfy the target.
            visible[0] = record(device, "198.51.100.1", TARGET, "implicit-deny", None)
            visible[1] = record(device, "198.51.100.2", "192.0.2.0/24", "implicit-deny", None)
            heldout[0] = record(device, "198.51.100.2", TARGET, "implicit-deny", None)
            intent["raw_text"] = f"On {device}, permit {TARGET} from 198.51.100.1 with local preference 150 only."
            intent["changes"] = [
                {"dimension": "decision", "relation": "replace", "desired": "permit"},
                {"dimension": "local_pref", "relation": "replace", "desired": 150},
            ]
            safe = safe.replace("set local-preference 250", "set local-preference 150", 1)
            shared_pl = re.search(r"match ip address prefix-list (PL_\S+_SHARED)", base).group(1)
            unsafe = base.replace(
                f"ip prefix-list {shared_pl} ",
                f"ip prefix-list {shared_pl} seq 1 permit {TARGET}\nip prefix-list {shared_pl} ",
                1,
            )
        else:
            unsafe = unsafe_shared_clause(base, target_rm)

        target_update = {
            visible[0]["behavior_id"]: {
                "decision": intent["changes"][0].get("desired") if intent["changes"][0]["dimension"] == "decision" else "permit",
                "local_pref": intent["changes"][-1]["desired"],
            }
        }
        unsafe_heldout_update = {
            heldout[0]["behavior_id"]: {
                "decision": target_update[visible[0]["behavior_id"]]["decision"],
                "local_pref": target_update[visible[0]["behavior_id"]]["local_pref"],
            }
        }
        destination = output / "scenarios" / case_id
        for name, text in baselines.items():
            path = destination / "baseline" / f"{name}.conf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for candidate_id, config, heldout_updates in (
            ("safe_local_fork", safe, {}),
            ("unsafe_latent_shared_edit", unsafe, unsafe_heldout_update),
        ):
            candidate_root = destination / "candidates" / candidate_id
            for name, text in baselines.items():
                candidate_text = config if name == device else text
                path = candidate_root / "configs" / f"{name}.conf"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(candidate_text, encoding="utf-8")
            (candidate_root / "visible_post_observations.json").write_text(
                json.dumps(changed(visible, target_update), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            (candidate_root / "heldout_post_observations.json").write_text(
                json.dumps(changed(heldout, heldout_updates), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            (candidate_root / "candidate.json").write_text(
                json.dumps({"candidate_id": candidate_id, "generated_without_envelope": True}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        (destination / "intent.json").write_text(json.dumps(intent, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (destination / "visible_pre_observations.json").write_text(json.dumps(visible, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (destination / "heldout_pre_observations.json").write_text(json.dumps(heldout, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        meta = json.loads((case_root / "case.json").read_text(encoding="utf-8"))
        (destination / "scenario.json").write_text(
            json.dumps({**meta, "freeze_version": "v81-public-nontriviality-1.0.0", "source_re_downloaded": True}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        cases.append(case_id)

    files = {
        path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "dataset_manifest.json"
    }
    manifest = {
        "dataset_id": "msn2026_v81_public_nontriviality",
        "version": "1.0.0",
        "seed": 20260812,
        "source_dataset_id": source_manifest["dataset_id"],
        "source_provenance_sha256": source_provenance_hash,
        "source_re_downloaded": True,
        "public_source_count": source_manifest["public_source_count"],
        "scenario_count": len(cases),
        "candidate_count": 2 * len(cases),
        "cases": cases,
        "files_sha256": files,
        "claim_boundary": "Fresh public-source-conditioned challenge mutations, not unmodified production configurations.",
    }
    (output / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, sort_keys=True))
