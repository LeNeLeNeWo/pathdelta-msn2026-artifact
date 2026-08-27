"""
Lab Generator module for Kathara lab configuration generation.

This module provides functions to generate Kathara lab.conf files
from topology definitions.
"""

import shutil
from pathlib import Path
from typing import Any, Dict, List


# Kathara FRR image
KATHARA_FRR_IMAGE = "kathara/frr"


def generate_lab_conf(
    topology: Dict[str, Any],
    device_configs: Dict[str, Path],
    output_dir: Path,
) -> Path:
    """
    Generate Kathara lab.conf from topology.
    
    Creates:
    - lab.conf with node definitions and collision domains
    - Device directories with FRR configs mounted
    - Startup scripts for each device
    
    Args:
        topology: Topology dictionary with 'nodes' and 'lans' keys.
        device_configs: Map of device_name -> config_path.
        output_dir: Directory to write lab files to.
    
    Returns:
        Path to generated lab directory.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    nodes = topology.get("nodes", {})
    lans = topology.get("lans", {})
    
    # Build node-to-interface mapping
    # Each node needs interfaces for each LAN it's connected to
    node_interfaces: Dict[str, List[str]] = {node: [] for node in nodes}
    
    for lan_name, lan_info in lans.items():
        members = lan_info.get("members", [])
        for member in members:
            device = member.get("device")
            if device in node_interfaces:
                node_interfaces[device].append(lan_name)
    
    # Generate lab.conf content
    lab_conf_lines = []
    
    # Add image definitions for each node
    for node_name in nodes:
        lab_conf_lines.append(f'{node_name}[image]="{KATHARA_FRR_IMAGE}"')
    
    lab_conf_lines.append("")  # Empty line separator
    
    # Add interface-to-LAN mappings
    for node_name, connected_lans in node_interfaces.items():
        for idx, lan_name in enumerate(sorted(connected_lans)):
            lab_conf_lines.append(f'{node_name}[{idx}]="{lan_name}"')
    
    # Write lab.conf
    lab_conf_path = output_dir / "lab.conf"
    lab_conf_path.write_text("\n".join(lab_conf_lines) + "\n")
    
    # Create device directories and startup scripts
    for node_name, node_info in nodes.items():
        node_dir = output_dir / node_name
        node_dir.mkdir(exist_ok=True)
        
        # Create etc/frr directory for FRR config
        frr_dir = node_dir / "etc" / "frr"
        frr_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy FRR config if provided
        if node_name in device_configs:
            config_src = Path(device_configs[node_name])
            if config_src.exists():
                config_dst = frr_dir / "frr.conf"
                shutil.copy(config_src, config_dst)
        
        # Create daemons file to enable FRR daemons
        daemons_content = """zebra=yes
bgpd=yes
ospfd=yes
ospf6d=no
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
"""
        (frr_dir / "daemons").write_text(daemons_content)
        
        # Create vtysh.conf
        (frr_dir / "vtysh.conf").write_text("service integrated-vtysh-config\n")
        
        # Generate startup script
        startup_lines = [
            "#!/bin/bash",
            "echo 1 > /proc/sys/net/ipv4/ip_forward",
            "",
        ]
        
        # Configure interfaces based on LAN connections
        connected_lans = node_interfaces.get(node_name, [])
        for idx, lan_name in enumerate(sorted(connected_lans)):
            lan_info = lans.get(lan_name, {})
            members = lan_info.get("members", [])
            
            # Find this node's IP in the LAN members
            for member in members:
                if member.get("device") == node_name:
                    ip_with_mask = member.get("ip", "")
                    if ip_with_mask:
                        startup_lines.append(f"# {lan_name}: eth{idx}")
                        startup_lines.append(f"ip addr add {ip_with_mask} dev eth{idx}")
                        startup_lines.append(f"ip link set eth{idx} up")
                        startup_lines.append("")
                    break
        
        # Add loopback configuration if present
        loopback = node_info.get("loopback", "")
        if loopback:
            startup_lines.append("# Loopback")
            startup_lines.append(f"ip addr add {loopback} dev lo")
            startup_lines.append("")
        
        # Start FRR
        startup_lines.append("/etc/init.d/frr start")
        startup_lines.append("")
        
        # Write startup script
        startup_path = output_dir / f"{node_name}.startup"
        startup_path.write_text("\n".join(startup_lines))
    
    return output_dir
