#!/usr/bin/env python3
"""Run the latent shared-route-map case in converged Kathara/FRR labs."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.utils.kathara_utils import kathara_lclean, kathara_lstart, kathara_vtysh


DAEMONS = """zebra=yes
bgpd=yes
ospfd=no
ospf6d=no
ripd=no
ripngd=no
isisd=no
pimd=no
ldpd=no
nhrpd=no
eigrpd=no
babeld=no
sharpd=no
pbrd=no
bfdd=no
fabricd=no
vrrpd=no
pathd=no
staticd=yes
watchfrr=yes
"""

STARTUP = """#!/bin/sh
/usr/lib/frr/zebra -d -F traditional -A 127.0.0.1
/usr/lib/frr/staticd -d -F traditional -A 127.0.0.1
/usr/lib/frr/bgpd -d -F traditional -A 127.0.0.1
sleep 1
vtysh -b
"""


def wrap_edge(policy: str) -> str:
    policy = policy.replace("192.0.2.1", "10.0.0.2").replace("192.0.2.2", "10.0.1.2")
    policy = policy.replace(
        "router bgp 65000\n",
        "interface eth0\n ip address 10.0.0.1/30\n!\n"
        "interface eth1\n ip address 10.0.1.1/30\n!\n"
        "router bgp 65000\n bgp router-id 10.255.0.1\n no bgp ebgp-requires-policy\n",
        1,
    )
    return (
        "frr version 8.4\nfrr defaults traditional\nservice integrated-vtysh-config\n"
        + policy
        + "line vty\n!\n"
    )


def peer(name: str, asn: int, address: str, edge: str, router_id: str) -> str:
    return f"""frr version 8.4
frr defaults traditional
hostname {name}
service integrated-vtysh-config
interface eth0
 ip address {address}
!
ip route 10.10.0.0/24 Null0
ip route 10.20.0.0/24 Null0
!
router bgp {asn}
 bgp router-id {router_id}
 no bgp ebgp-requires-policy
 no bgp network import-check
 neighbor {edge} remote-as 65000
 address-family ipv4 unicast
  network 10.10.0.0/24
  network 10.20.0.0/24
  neighbor {edge} activate
 exit-address-family
!
line vty
!
"""


def write_node(lab: Path, node: str, config: str) -> None:
    target = lab / node / "etc" / "frr"
    target.mkdir(parents=True, exist_ok=True)
    (target / "frr.conf").write_text(config, encoding="utf-8")
    (target / "daemons").write_text(DAEMONS, encoding="utf-8")
    (target / "vtysh.conf").write_text("service integrated-vtysh-config\n", encoding="utf-8")
    startup = lab / f"{node}.startup"
    startup.write_text(STARTUP, encoding="utf-8")
    startup.chmod(0o755)


def build_lab(lab: Path, policy: str) -> None:
    lab.mkdir(parents=True, exist_ok=False)
    (lab / "lab.conf").write_text(
        'edge[0]="A"\nedge[1]="B"\npeer1[0]="A"\npeer2[0]="B"\n'
        'edge[image]="frrouting/frr:v8.4.0"\npeer1[image]="frrouting/frr:v8.4.0"\npeer2[image]="frrouting/frr:v8.4.0"\n',
        encoding="utf-8",
    )
    write_node(lab, "edge", wrap_edge(policy))
    write_node(lab, "peer1", peer("peer1", 65101, "10.0.0.2/30", "10.0.0.1", "10.255.1.1"))
    write_node(lab, "peer2", peer("peer2", 65102, "10.0.1.2/30", "10.0.1.1", "10.255.2.1"))


def route_attributes(payload: str) -> Dict[str, Any]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    paths = data.get("paths", []) if isinstance(data, dict) else []
    output: Dict[str, Any] = {}
    for path in paths:
        peer_value = path.get("peerId") or path.get("peer") or ""
        if isinstance(peer_value, dict):
            peer_value = peer_value.get("peerId") or peer_value.get("hostname") or ""
        peer_id = str(peer_value)
        if not peer_id:
            nexthops = path.get("nexthops") or []
            peer_id = str(nexthops[0].get("ip")) if nexthops else "unknown"
        output[peer_id] = {
            "local_pref": path.get("locPrf", path.get("localPref")),
            "valid": bool(path.get("valid", True)),
            "best": bool((path.get("bestpath") or {}).get("overall", False)),
        }
    return output


def observe(lab: Path, timeout_s: int) -> Dict[str, Any]:
    started = kathara_lstart(lab, show_terminals=False)
    if not started.success:
        return {"status": "FAIL", "stage": "lstart", "stderr": started.stderr}
    try:
        deadline = time.time() + timeout_s
        summary = ""
        sessions = 0
        target_raw = "{}"
        attributes: Dict[str, Any] = {}
        while time.time() < deadline:
            answer = kathara_vtysh(lab, "edge", ["show ip bgp summary"])
            summary = answer.stdout
            sessions = 0
            for line in summary.splitlines():
                parts = line.split()
                if parts and parts[0] in {"10.0.0.2", "10.0.1.2"} and len(parts) > 9 and parts[9].isdigit():
                    sessions += 1
            target_poll = kathara_vtysh(lab, "edge", ["show bgp ipv4 unicast 10.10.0.0/24 json"])
            target_raw = target_poll.stdout
            attributes = route_attributes(target_raw)
            if sessions == 2 and len(attributes) == 2:
                break
            time.sleep(2)
        control = kathara_vtysh(lab, "edge", ["show bgp ipv4 unicast 10.20.0.0/24 json"])
        return {
            "status": "PASS" if sessions == 2 and len(attributes) == 2 else "FAIL",
            "session_count": sessions,
            "summary": summary,
            "target_route_attributes": attributes,
            "target_raw": target_raw,
            "non_target_raw": control.stdout,
        }
    finally:
        kathara_lclean(lab)


def lp(observation: Dict[str, Any], peer_ip: str) -> Any:
    return (observation.get("target_route_attributes") or {}).get(peer_ip, {}).get("local_pref")


def run(data_root: Path, output_root: Path, timeout_s: int) -> Dict[str, Any]:
    scenario = data_root / "scenarios" / "latent_shared_route_map"
    baseline_path = next((scenario / "baseline").glob("*.conf"))
    variants = {"baseline": baseline_path.read_text(encoding="utf-8")}
    for candidate in (scenario / "candidates").iterdir():
        variants[candidate.name] = next((candidate / "configs").glob("*.conf")).read_text(encoding="utf-8")
    observations = {}
    for variant, config in variants.items():
        lab = output_root / "labs" / variant
        if lab.exists():
            import shutil
            shutil.rmtree(lab)
        build_lab(lab, config)
        observations[variant] = observe(lab, timeout_s)
        print(f"{variant}: {observations[variant]['status']}", flush=True)

    baseline = observations["baseline"]
    unsafe = observations["unsafe_value_only_shared_edit"]
    safe = observations["safe_local_fork"]
    checks = {
        "all_labs_converged": all(row["status"] == "PASS" for row in observations.values()),
        "baseline_both_peers_100": lp(baseline, "10.0.0.2") == 100 and lp(baseline, "10.0.1.2") == 100,
        "unsafe_changes_target_peer": lp(unsafe, "10.0.0.2") == 250,
        "unsafe_changes_heldout_peer": lp(unsafe, "10.0.1.2") == 250,
        "safe_changes_target_peer": lp(safe, "10.0.0.2") == 250,
        "safe_preserves_heldout_peer": lp(safe, "10.0.1.2") == 100,
    }
    summary = {
        "backend": "Kathara 3.8.0 + FRR 8.4.0",
        "scenario": "latent_shared_route_map",
        "adapter_mapping": {"192.0.2.1": "10.0.0.2", "192.0.2.2": "10.0.1.2"},
        "checks": checks,
        "passed": all(checks.values()),
        "observations": observations,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v81_nontriviality"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v81_kathara_dynamic_dev"))
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    result = run(args.data_root, args.output_root, args.timeout)
    print(json.dumps({"passed": result["passed"], "checks": result["checks"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
