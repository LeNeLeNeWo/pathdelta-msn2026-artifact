from __future__ import annotations

from intent_layer.handlers.base import BaseIntentHandler
from intent_layer.schema import IntentCard
from policy_layer.topology_view import Topology


class PreferWithBackupHandler(BaseIntentHandler):
    supported_type = "prefer_with_backup"

    def postprocess_intent(self, intent: IntentCard, topology: Topology | None = None) -> IntentCard:
        # No special processing for now; placeholder for future enrichment.
        return intent
