from __future__ import annotations

from typing import Dict, Optional

from policy_layer.handlers.avoid_exit_builder import AvoidExitBuilder
from policy_layer.handlers.base import BasePolicyBuilder
from policy_layer.handlers.ecmp_builder import EcmpBuilder
from policy_layer.handlers.ordered_preference_builder import OrderedPreferenceBuilder
from policy_layer.handlers.pin_to_exit_builder import PinToExitBuilder
from policy_layer.handlers.prefer_with_backup_builder import PreferWithBackupBuilder
from policy_layer.handlers.path_migration_builder import PathMigrationBuilder
from policy_layer.handlers.ospf_steering_builder import OspfSteeringBuilder

_BUILDERS: Dict[str, BasePolicyBuilder] = {
    b.supported_type: b
    for b in [
        PreferWithBackupBuilder(),
        EcmpBuilder(),
        OrderedPreferenceBuilder(),
        PinToExitBuilder(),
        AvoidExitBuilder(),
        PathMigrationBuilder(),
        OspfSteeringBuilder(),
    ]
}


def get_policy_builder(intent_type: str) -> Optional[BasePolicyBuilder]:
    return _BUILDERS.get(intent_type)


def list_supported_types() -> list[str]:
    return list(_BUILDERS.keys())
