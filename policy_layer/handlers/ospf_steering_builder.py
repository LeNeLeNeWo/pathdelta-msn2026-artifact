from __future__ import annotations

from typing import List, Optional, Set

from intent_layer.schema import IntentCard
from policy_layer.handlers.base import BasePolicyBuilder, PreferenceTiers
from policy_layer.handlers.common import build_single_entry
from policy_layer.models import PolicyEntry, PolicySketch
from policy_layer.topology_view import Topology


class OspfSteeringBuilder(BasePolicyBuilder):
    """
    Builder for 'ospf_steering' intent type.
    
    Logic:
    - Similar to OrderedPreference: derives preference tiers from ordered_exits.
    - These tiers are then used by the runner's _fill_ospf_steering to calculate edge costs.
    """
    supported_type = "ospf_steering"

    def build_policy_entries(
        self,
        intent: IntentCard,
        topology: Topology,
        sketch: PolicySketch,
        proto_hint: Optional[str] = None,
    ) -> List[PolicyEntry]:
        # Force OSPF if proto_hint is not provided or auto
        # OSPF steering inherently implies OSPF protocol
        effective_proto = proto_hint
        if not effective_proto or effective_proto == "auto":
            effective_proto = "ospf"
            
        return build_single_entry(intent, topology, sketch, proto_hint=effective_proto)

    def build_preference_tiers(
        self,
        intent: IntentCard,
        affected_exits: Set[str],
    ) -> PreferenceTiers:
        """
        Derive preference tiers from ordered_exits, identical to OrderedPreference.
        """
        tiers: PreferenceTiers = {}
        
        # Get ordered exits
        normalizer = getattr(intent, "normalized_ordered_exits", None)
        ordered = normalizer() if callable(normalizer) else []
        ordered = ordered or []
        
        n = len(ordered)
        for idx, exit_name in enumerate(ordered):
            # First exit gets highest tier, descending from there
            tiers[exit_name] = n - idx if n > 0 else 0
        
        # Others stay at baseline (0)
        for exit_name in affected_exits:
            tiers.setdefault(exit_name, 0)
        
        return tiers
