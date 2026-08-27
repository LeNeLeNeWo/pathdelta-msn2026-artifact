from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

BGP_START = re.compile(r"^router\s+bgp\s+(\d+)", re.IGNORECASE)
OSPF_START = re.compile(r"^router\s+ospf", re.IGNORECASE)
RIP_START = re.compile(r"^router\s+rip", re.IGNORECASE)
ROUTEMAP_START = re.compile(r"^route-map\s+(\S+)", re.IGNORECASE)
PFXLIST_START = re.compile(r"^ip\s+prefix-list\s+(\S+)", re.IGNORECASE)
COMMLIST_START = re.compile(r"^ip\s+community-list\s+(\S+)", re.IGNORECASE)
NEIGHBOR_REMOTE_AS = re.compile(r"neighbor\s+(\S+).*remote-as\s+(\d+)", re.IGNORECASE)
MAX_PATHS_RE = re.compile(r"maximum-paths\s+(\d+)", re.IGNORECASE)
LOCAL_PREF_RE = re.compile(r"set\s+local-preference\s+(\d+)", re.IGNORECASE)

DeviceConfigAST = Dict[str, Any]
DevicesAST = Dict[str, DeviceConfigAST]


def _collect_block(lines: List[str], start_idx: int, is_new_block) -> Tuple[List[str], int]:
    block: List[str] = []
    i = start_idx
    while i < len(lines):
        line = lines[i]
        if i != start_idx and is_new_block(line):
            break
        block.append(line)
        i += 1
    return block, i


def parse_frr_config(path: Path) -> DeviceConfigAST:
    """
    Parse a FRR config into a lightweight AST.
    Returns dict with keys:
    {
        "device": <name>,
        "bgp": {"asn": int, "lines": [...], "neighbors": [{"ip": str, "remote_as": int}], "max_paths": Optional[int]} | None,
        "ospf": {"lines": [...]} | None,
        "rip": {"lines": [...]} | None,
        "route_maps": {name: [lines...]},
        "prefix_lists": {name: [lines...]},
        "community_lists": {name: [lines...]},
        "all_lines": [...],
    }
    """
    content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    ast: DeviceConfigAST = {
        "device": path.stem,
        "bgp": None,
        "ospf": None,
        "rip": None,
        "route_maps": {},
        "prefix_lists": {},
        "community_lists": {},
        "all_lines": content,
    }

    i = 0
    while i < len(content):
        line = content[i].strip()
        # BGP
        m = BGP_START.match(line)
        if m:
            asn = int(m.group(1))
            block, i = _collect_block(content, i, lambda l: BGP_START.match(l) or OSPF_START.match(l) or RIP_START.match(l))
            neighbors = []
            for l in block:
                nm = NEIGHBOR_REMOTE_AS.search(l)
                if nm:
                    neighbors.append({"ip": nm.group(1), "remote_as": int(nm.group(2))})
            max_paths = None
            for l in block:
                mm = MAX_PATHS_RE.search(l)
                if mm:
                    try:
                        max_paths = int(mm.group(1))
                    except ValueError:
                        pass
            ast["bgp"] = {"asn": asn, "lines": block, "neighbors": neighbors, "max_paths": max_paths}
            continue

        # OSPF
        if OSPF_START.match(line):
            block, i = _collect_block(content, i, lambda l: BGP_START.match(l) or OSPF_START.match(l) or RIP_START.match(l))
            ast["ospf"] = {"lines": block}
            continue

        # RIP
        if RIP_START.match(line):
            block, i = _collect_block(content, i, lambda l: BGP_START.match(l) or OSPF_START.match(l) or RIP_START.match(l))
            ast["rip"] = {"lines": block}
            continue

        # route-map
        rm = ROUTEMAP_START.match(line)
        if rm:
            name = rm.group(1)
            block, i = _collect_block(content, i, lambda l: ROUTEMAP_START.match(l) or PFXLIST_START.match(l) or COMMLIST_START.match(l) or BGP_START.match(l) or OSPF_START.match(l) or RIP_START.match(l))
            ast["route_maps"][name] = block
            continue

        # prefix-list
        pm = PFXLIST_START.match(line)
        if pm:
            name = pm.group(1)
            block, i = _collect_block(content, i, lambda l: ROUTEMAP_START.match(l) or PFXLIST_START.match(l) or COMMLIST_START.match(l) or BGP_START.match(l) or OSPF_START.match(l) or RIP_START.match(l))
            ast["prefix_lists"][name] = block
            continue

        # community-list
        cm = COMMLIST_START.match(line)
        if cm:
            name = cm.group(1)
            block, i = _collect_block(content, i, lambda l: ROUTEMAP_START.match(l) or PFXLIST_START.match(l) or COMMLIST_START.match(l) or BGP_START.match(l) or OSPF_START.match(l) or RIP_START.match(l))
            ast["community_lists"][name] = block
            continue

        i += 1

    return ast


def load_device_asts(baseline_dir: Path) -> DevicesAST:
    """
    Parse all *.frr configs under baseline_dir into a mapping
    { device_name: DeviceConfigAST }.
    """
    result: DevicesAST = {}
    if not baseline_dir.exists():
        return result
    for cfg in baseline_dir.glob("*.frr"):
        ast = parse_frr_config(cfg)
        dev = ast.get("device") or cfg.stem
        result[dev] = ast
    return result
