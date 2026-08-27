from __future__ import annotations

from typing import Dict, List, Optional, Set

from intent_layer.schema import IntentCard
from policy_layer.handlers.base import BasePolicyBuilder, PreferenceTiers
from policy_layer.handlers.common import build_single_entry
from policy_layer.models import PolicyEntry, PolicySketch
from policy_layer.topology_view import Topology


class PreferWithBackupBuilder(BasePolicyBuilder):
    supported_type = "prefer_with_backup"

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
        prefer_with_backup tier logic:
        - primary_exit: tier=3 (highest preference)
        - backup_exit: tier=2 (second preference)
        - others: tier=0 (baseline)
        """
        tiers: PreferenceTiers = {}
        remaining = set(affected_exits)
        
        primary = getattr(intent, "primary_exit", None)
        backup = getattr(intent, "backup_exit", None)
        
        if primary:
            tiers[primary] = 3
            remaining.discard(primary)
        if backup:
            tiers[backup] = 2
            remaining.discard(backup)
        
        # Others stay at baseline
        for exit_name in remaining:
            tiers.setdefault(exit_name, 0)
        
        return tiers
