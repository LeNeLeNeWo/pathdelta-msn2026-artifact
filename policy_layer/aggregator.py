from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from intent_layer.schema import IntentCard


def aggregate_intents(intents: List[IntentCard]) -> Dict[Tuple[str, str, int | None], List[IntentCard]]:
    """
    Group intents by (scope, prefix, src_as). Keeps deterministic ordering.
    """
    groups: Dict[Tuple[str, str, int | None], List[IntentCard]] = defaultdict(list)
    for intent in intents:
        key = (intent.scope, intent.prefix or "", intent.src_as)
        groups[key].append(intent)
    return dict(groups)
