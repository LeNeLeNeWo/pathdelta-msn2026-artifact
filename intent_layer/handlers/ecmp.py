from __future__ import annotations

from intent_layer.handlers.base import BaseIntentHandler
from intent_layer.schema import IntentCard
from policy_layer.topology_view import Topology


class EcmpHandler(BaseIntentHandler):
    supported_type = "ecmp"

    def postprocess_intent(self, intent: IntentCard, topology: Topology | None = None) -> IntentCard:
        return intent
