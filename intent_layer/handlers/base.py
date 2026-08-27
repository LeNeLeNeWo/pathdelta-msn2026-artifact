from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from intent_layer.schema import IntentCard
from policy_layer.topology_view import Topology  # lightweight topology view for optional postprocess


class BaseIntentHandler(ABC):
    """Base class for intent-layer handlers."""

    @property
    @abstractmethod
    def supported_type(self) -> str:
        ...

    def postprocess_intent(self, intent: IntentCard, topology: Optional[Topology] = None) -> IntentCard:
        """
        Optional hook to adjust/augment an IntentCard after parsing.
        Default: no-op.
        """
        return intent
