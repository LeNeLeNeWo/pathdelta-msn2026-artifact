"""
Preference Tiers - Registry-Based Implementation

Maps Intent + AffectScope to unified tier representation using the handler registry.
This module follows the Open-Closed Principle: adding new intent types requires
only updating the registry and handlers, not this core module.

Tier Semantics:
- tier > 0: Increase priority (higher BGP local-pref / lower OSPF cost)
- tier == 0: Keep baseline (minimize changes)
- tier < 0: Explicit demotion/avoidance (lower BGP local-pref / higher OSPF cost)
"""
from __future__ import annotations

from typing import Dict, Set

# Type alias defined locally to avoid circular imports
# This is the canonical definition - param_solver.py imports from here
PreferenceTiers = Dict[str, int]


def build_preference_tiers(intent, scope) -> PreferenceTiers:
    """
    Calculate preference tiers by delegating to the appropriate handler.
    
    This function uses the registry pattern to look up the correct handler
    for the intent type, then delegates tier calculation to that handler.
    
    Args:
        intent: IntentCard object with type and exit information
        scope: AffectScope object with affected_devices/exits
        
    Returns:
        PreferenceTiers dict mapping exit_name -> tier (integer)
        
    Architecture:
        This function is now a thin dispatcher that:
        1. Extracts affected exits from scope
        2. Looks up the handler via registry
        3. Delegates to handler.build_preference_tiers()
        4. Falls back to default behavior for unknown types
        
    Adding New Intent Types:
        To add a new intent type (e.g., "latency_sensitive"):
        1. Create handler in policy_layer/handlers/latency_sensitive_builder.py
        2. Implement build_preference_tiers() method
        3. Register in policy_layer/registry.py
        NO changes needed to this file!
    """
    # Extract affected exits from scope
    exits = getattr(scope, "exits", None) or getattr(scope, "affected_devices", None) or []
    affected_exits: Set[str] = set(exits)
    
    # Get intent type
    intent_type = getattr(intent, "type", None)
    
    # Lazy import to avoid circular dependency:
    # preference_tiers -> registry -> handlers -> ... -> param_solver -> preference_tiers
    from policy_layer.registry import get_policy_builder
    
    # Look up handler via registry
    builder = get_policy_builder(intent_type)
    
    if builder is not None:
        # Delegate to handler's build_preference_tiers method
        return builder.build_preference_tiers(intent, affected_exits)
    
    # Fallback for unknown intent types: all affected exits get tier=1
    # This provides a safe default while logging a warning
    tiers: PreferenceTiers = {}
    for exit_name in affected_exits:
        tiers[exit_name] = 1
    
    return tiers
