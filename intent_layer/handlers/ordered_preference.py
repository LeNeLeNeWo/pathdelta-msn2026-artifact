from __future__ import annotations

from intent_layer.handlers.base import BaseIntentHandler
from intent_layer.schema import IntentCard
from policy_layer.topology_view import Topology


class OrderedPreferenceHandler(BaseIntentHandler):
    supported_type = "ordered_preference"

    def postprocess_intent(self, intent: IntentCard, topology: Topology | None = None) -> IntentCard:
        """
        Postprocess example: ensure ordered_exits is populated.
        If LLM output 'exits' instead of 'ordered_exits', convert it here.
        This is a safety net in addition to parser-level normalization.
        """
        if not intent.ordered_exits and intent.exits:
            intent.ordered_exits = intent.exits
            intent.exits = None
        return intent
