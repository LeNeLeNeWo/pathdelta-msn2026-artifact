"""
Assertions module for BGP/OSPF parsing and state checking.

This module provides reusable assertion logic for parsing and checking
BGP and OSPF state from FRR JSON output.
"""

from typing import Any, Dict, Tuple


def parse_bgp_summary_json(bgp_json: Dict[str, Any]) -> Dict[str, str]:
    """
    Parse BGP summary JSON and extract peer states.
    
    Handles different FRR JSON output formats.
    
    Args:
        bgp_json: Parsed JSON from "show ip bgp summary json".
    
    Returns:
        Dictionary mapping peer IP to state string.
    """
    peers = {}
    
    # Try ipv4Unicast structure (newer FRR versions)
    if "ipv4Unicast" in bgp_json:
        raw_peers = bgp_json.get("ipv4Unicast", {}).get("peers", {})
        for peer_ip, peer_info in raw_peers.items():
            state = peer_info.get("state", "Unknown")
            peers[peer_ip] = str(state)
    # Try direct peers structure (older FRR versions)
    elif "peers" in bgp_json:
        raw_peers = bgp_json.get("peers", {})
        for peer_ip, peer_info in raw_peers.items():
            state = peer_info.get("state", "Unknown")
            peers[peer_ip] = str(state)
    
    return peers


def is_bgp_neighbor_established(state: str) -> bool:
    """
    Check if a BGP neighbor state indicates Established.
    
    Args:
        state: BGP state string (e.g., "Established", "6", "Active").
    
    Returns:
        True if state indicates Established, False otherwise.
    """
    # Established state can be represented as string or numeric (6)
    return state == "Established" or state == "6"


def check_all_neighbors_established(
    bgp_data: Dict[str, Any],
    device_name: str = "",
) -> Tuple[bool, str]:
    """
    Check if all BGP neighbors are in Established state.
    
    Args:
        bgp_data: Parsed BGP summary JSON.
        device_name: Optional device name for error messages.
    
    Returns:
        (True, "") if all neighbors established.
        (False, "BGP Neighbor {ip} Down") if any not established.
    """
    peers = parse_bgp_summary_json(bgp_data)
    
    for peer_ip, state in peers.items():
        if not is_bgp_neighbor_established(state):
            if device_name:
                return (False, f"BGP Neighbor {peer_ip} Down on {device_name}")
            return (False, f"BGP Neighbor {peer_ip} Down")
    
    return (True, "")


def parse_ospf_neighbor_json(ospf_json: Dict[str, Any]) -> Dict[str, str]:
    """
    Parse OSPF neighbor JSON and extract neighbor states.
    
    Args:
        ospf_json: Parsed JSON from "show ip ospf neighbor json".
    
    Returns:
        Dictionary mapping neighbor ID to state string.
    """
    neighbors = {}
    
    # OSPF neighbor JSON structure: {"neighbors": {"<id>": [{"state": "..."}]}}
    # or {"<interface>": [{"neighborId": "...", "state": "..."}]}
    if "neighbors" in ospf_json:
        raw_neighbors = ospf_json.get("neighbors", {})
        for neighbor_id, neighbor_list in raw_neighbors.items():
            if isinstance(neighbor_list, list) and neighbor_list:
                state = neighbor_list[0].get("state", "Unknown")
                neighbors[neighbor_id] = str(state)
            elif isinstance(neighbor_list, dict):
                state = neighbor_list.get("state", "Unknown")
                neighbors[neighbor_id] = str(state)
    else:
        # Try interface-based structure
        for interface, neighbor_list in ospf_json.items():
            if isinstance(neighbor_list, list):
                for neighbor_info in neighbor_list:
                    neighbor_id = neighbor_info.get("neighborId", "")
                    state = neighbor_info.get("state", "Unknown")
                    if neighbor_id:
                        neighbors[neighbor_id] = str(state)
    
    return neighbors


def is_ospf_neighbor_full(state: str) -> bool:
    """
    Check if an OSPF neighbor state indicates Full adjacency.
    
    Args:
        state: OSPF state string (e.g., "Full", "2-Way", "Full/DR").
    
    Returns:
        True if state indicates Full, False otherwise.
    """
    # Full state can appear as "Full", "Full/DR", "Full/BDR", etc.
    return state.startswith("Full")
