from __future__ import annotations

from intent_layer.handlers.base import BaseIntentHandler
from intent_layer.schema import IntentCard
from policy_layer.topology_view import Topology


class AvoidExitHandler(BaseIntentHandler):
    supported_type = "avoid_exit"

    def postprocess_intent(self, intent: IntentCard, topology: Topology | None = None) -> IntentCard:
        """
        Postprocess example: normalize single avoid_exit to avoid_exits list.
        If LLM gave avoid_exit (singular), convert to avoid_exits (plural list).
        """
        if not intent.avoid_exits and intent.avoid_exit:
            intent.avoid_exits = [intent.avoid_exit]
        return intent
