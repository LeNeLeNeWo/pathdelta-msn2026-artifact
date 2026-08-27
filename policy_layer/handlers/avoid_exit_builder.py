from __future__ import annotations

from typing import Dict, List, Optional, Set

from intent_layer.schema import IntentCard
from policy_layer.handlers.base import BasePolicyBuilder, PreferenceTiers
from policy_layer.handlers.common import build_single_entry
from policy_layer.models import PolicyEntry, PolicySketch
from policy_layer.topology_view import Topology


class AvoidExitBuilder(BasePolicyBuilder):
    supported_type = "avoid_exit"

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
        avoid_exit tier logic:
        - avoid_exits: tier=-1 (explicit demotion/avoidance)
        - others: tier=0 (baseline)
        """
        tiers: PreferenceTiers = {}
        
        # Get exits to avoid
        normalizer = getattr(intent, "normalized_avoid_exits", None)
        avoids = normalizer() if callable(normalizer) else []
        avoids = avoids or []
        
        for exit_name in avoids:
            tiers[exit_name] = -1
        
        # Others stay at baseline
        for exit_name in affected_exits:
            if exit_name not in tiers:
                tiers[exit_name] = 0
        
        return tiers
