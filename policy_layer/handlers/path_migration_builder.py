from __future__ import annotations

from typing import Dict, List, Optional, Set

from intent_layer.schema import IntentCard
from policy_layer.handlers.base import BasePolicyBuilder, PreferenceTiers
from policy_layer.handlers.common import build_single_entry
from policy_layer.models import PolicyEntry, PolicySketch
from policy_layer.topology_view import Topology


class PathMigrationBuilder(BasePolicyBuilder):
    """Policy builder for path_migration intent type."""
    
    supported_type = "path_migration"

    def build_policy_entries(
        self,
        intent: IntentCard,
        topology: Topology,
        sketch: PolicySketch,
        proto_hint: Optional[str] = None,
    ) -> List[PolicyEntry]:
        """
        Build policy entries for path_migration intent.
        
        path_migration uses BGP local-pref mechanisms to steer traffic
        from old_exits to new_exit for the specified prefixes.
        """
        return build_single_entry(intent, topology, sketch, proto_hint=proto_hint)

    def build_preference_tiers(
        self,
        intent: IntentCard,
        affected_exits: Set[str],
    ) -> PreferenceTiers:
        """
        path_migration tier logic:
        - new_exit: tier=3 (highest preference)
        - old_exits: tier=-1 (hard mode) or tier=1 (soft mode)
        - others: tier=0 (baseline)
        
        Mode semantics:
        - "soft": old_exits get tier=1 (lower than new, but not penalized)
        - "hard": old_exits get tier=-1 (explicit penalty below baseline)
        """
        tiers: PreferenceTiers = {}
        remaining = set(affected_exits)
        
        new_exit = getattr(intent, "new_exit", None)
        old_exits = getattr(intent, "old_exits", None) or []
        mode = getattr(intent, "mode", "soft")
        
        if new_exit:
            tiers[new_exit] = 3
            remaining.discard(new_exit)
        
        for old in old_exits:
            if mode == "hard":
                tiers[old] = -1  # Explicit penalty
            else:  # soft
                tiers[old] = 1   # Lower than new, but not penalized
            remaining.discard(old)
        
        # Others stay at baseline
        for exit_name in remaining:
            tiers.setdefault(exit_name, 0)
        
        return tiers
