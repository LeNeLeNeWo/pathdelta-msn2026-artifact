from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Set

from intent_layer.schema import IntentCard
from policy_layer.models import PolicyEntry, PolicySketch
from policy_layer.topology_view import Topology

# Type alias for preference tiers: exit_name -> tier (integer)
# Semantics:
#   tier > 0: Increase priority (higher BGP local-pref / lower OSPF cost)
#   tier == 0: Keep baseline (minimize changes)
#   tier < 0: Explicit demotion/avoidance (lower BGP local-pref / higher OSPF cost)
PreferenceTiers = Dict[str, int]


class BasePolicyBuilder(ABC):
    """Base class for policy builders per intent type.
    
    Each handler implements:
    - supported_type: The intent type string this handler processes
    - build_policy_entries: Generate PolicyEntry objects for synthesis
    - build_preference_tiers: Calculate exit preference tiers for this intent type
    """

    @property
    @abstractmethod
    def supported_type(self) -> str:
        """Return the intent type string this builder handles."""
        ...

    @abstractmethod
    def build_policy_entries(
        self,
        intent: IntentCard,
        topology: Topology,
        sketch: PolicySketch,
        proto_hint: Optional[str] = None,
    ) -> List[PolicyEntry]:
        """
        Build one or more PolicyEntry objects for a single intent.
        sketch: structured PolicySketch object (no longer raw dict).
        """
        ...

    @abstractmethod
    def build_preference_tiers(
        self,
        intent: IntentCard,
        affected_exits: Set[str],
    ) -> PreferenceTiers:
        """
        Calculate preference tiers for exits based on intent semantics.
        
        Args:
            intent: The IntentCard being processed
            affected_exits: Set of exit names in scope
            
        Returns:
            Dict mapping exit_name -> tier (integer)
            
        Tier Semantics:
            tier > 0: Increase priority (prefer this exit)
            tier == 0: Keep baseline (no change)
            tier < 0: Decrease priority (avoid this exit)
        """
        ...
