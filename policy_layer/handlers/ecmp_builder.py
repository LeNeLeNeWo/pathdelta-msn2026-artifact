from __future__ import annotations

from typing import Dict, List, Optional, Set

from intent_layer.schema import IntentCard
from policy_layer.handlers.base import BasePolicyBuilder, PreferenceTiers
from policy_layer.handlers.common import build_single_entry
from policy_layer.models import PolicyEntry, PolicySketch
from policy_layer.topology_view import Topology


class EcmpBuilder(BasePolicyBuilder):
    supported_type = "ecmp"

    def build_policy_entries(
        self,
        intent: IntentCard,
        topology: Topology,
        sketch: PolicySketch,
        proto_hint: Optional[str] = None,
    ) -> List[PolicyEntry]:
        return build_single_entry(intent, topology, sketch, proto_hint=proto_hint)

    def build_preference_tiers(
        self,
        intent: IntentCard,
        affected_exits: Set[str],
    ) -> PreferenceTiers:
        """
        ecmp tier logic:
        - All ECMP exits: tier=2 (equal preference for load balancing)
        - others: tier=0 (baseline)
        """
        tiers: PreferenceTiers = {}
        
        # Get ECMP exits from intent
        ecmp_exits = getattr(intent, "exits", None)
        if not ecmp_exits:
            # Fallback to normalized_ordered_exits if available
            normalizer = getattr(intent, "normalized_ordered_exits", None)
            if callable(normalizer):
                ecmp_exits = normalizer()
        ecmp_exits = ecmp_exits or list(affected_exits)
        
        # All ECMP exits get equal tier
        for exit_name in ecmp_exits:
            tiers[exit_name] = 2
        
        # Others stay at baseline
        for exit_name in affected_exits:
            tiers.setdefault(exit_name, 0)
        
        return tiers
