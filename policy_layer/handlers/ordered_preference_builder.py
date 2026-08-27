from __future__ import annotations

from typing import Dict, List, Optional, Set

from intent_layer.schema import IntentCard
from policy_layer.handlers.base import BasePolicyBuilder, PreferenceTiers
from policy_layer.handlers.common import build_single_entry
from policy_layer.models import PolicyEntry, PolicySketch
from policy_layer.topology_view import Topology


class OrderedPreferenceBuilder(BasePolicyBuilder):
    supported_type = "ordered_preference"

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
        ordered_preference tier logic:
        - ordered_exits = [e1, e2, e3, ...]
        - Assign descending tiers: e1=N, e2=N-1, ..., eN=1
        - others: tier=0 (baseline)
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
        
        # Others stay at baseline
        for exit_name in affected_exits:
            tiers.setdefault(exit_name, 0)
        
        return tiers
