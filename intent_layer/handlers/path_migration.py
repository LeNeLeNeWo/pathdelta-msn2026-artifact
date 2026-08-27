from __future__ import annotations

from intent_layer.handlers.base import BaseIntentHandler
from intent_layer.schema import IntentCard
from policy_layer.topology_view import Topology


class PathMigrationHandler(BaseIntentHandler):
    """Handler for path_migration intent type."""
    
    supported_type = "path_migration"

    def postprocess_intent(self, intent: IntentCard, topology: Topology | None = None) -> IntentCard:
        """
        Postprocess path_migration intent.
        
        For path_migration, we ensure:
        - mode defaults to "soft" if not specified (already handled by schema)
        - old_exits defaults to empty list if not specified
        """
        # Ensure old_exits is a list (not None) for downstream processing
        if intent.old_exits is None:
            intent = intent.model_copy(update={"old_exits": []})
        return intent
