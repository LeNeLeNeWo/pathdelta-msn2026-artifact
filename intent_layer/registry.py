from __future__ import annotations

from typing import Dict, Optional

from intent_layer.handlers.base import BaseIntentHandler
from intent_layer.handlers.ecmp import EcmpHandler
from intent_layer.handlers.ordered_preference import OrderedPreferenceHandler
from intent_layer.handlers.prefer_with_backup import PreferWithBackupHandler
from intent_layer.handlers.pin_to_exit import PinToExitHandler
from intent_layer.handlers.avoid_exit import AvoidExitHandler
from intent_layer.handlers.path_migration import PathMigrationHandler
from common.intents import INTENT_TYPES

_HANDLERS: Dict[str, BaseIntentHandler] = {
    h.supported_type: h
    for h in [
        PreferWithBackupHandler(),
        EcmpHandler(),
        OrderedPreferenceHandler(),
        PinToExitHandler(),
        AvoidExitHandler(),
        PathMigrationHandler(),
    ]
}


def get_handler(intent_type: str) -> Optional[BaseIntentHandler]:
    return _HANDLERS.get(intent_type)


def list_supported_types() -> list[str]:
    # 使用集中定义的意图类型，减少分散硬编码
    return list(INTENT_TYPES)
