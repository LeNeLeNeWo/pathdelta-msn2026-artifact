from __future__ import annotations

from intent_layer.handlers.base import BaseIntentHandler
from intent_layer.schema import IntentCard
from policy_layer.topology_view import Topology


class PinToExitHandler(BaseIntentHandler):
    supported_type = "pin_to_exit"

    def postprocess_intent(self, intent: IntentCard, topology: Topology | None = None) -> IntentCard:
        return intent
