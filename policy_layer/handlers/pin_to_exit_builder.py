from __future__ import annotations

from typing import Dict, List, Optional, Set

from intent_layer.schema import IntentCard
from policy_layer.handlers.base import BasePolicyBuilder, PreferenceTiers
from policy_layer.handlers.common import build_single_entry
from policy_layer.models import PolicyEntry, PolicySketch
from policy_layer.topology_view import Topology


class PinToExitBuilder(BasePolicyBuilder):
    supported_type = "pin_to_exit"

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
        pin_to_exit tier logic:
        - pinned_exit: tier=3 (highest preference)
        - others: tier=0 (baseline)
        """
        tiers: PreferenceTiers = {}
        
        pinned = getattr(intent, "pinned_exit", None)
        if pinned:
            tiers[pinned] = 3
        
        # Others stay at baseline
        for exit_name in affected_exits:
            if exit_name != pinned:
                tiers.setdefault(exit_name, 0)
        
        return tiers
