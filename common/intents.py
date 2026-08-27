"""
集中维护 PathDelta 支持的意图类型，供 intent_layer / policy_layer 共享，减少重复硬编码。
"""

INTENT_TYPES = (
    "prefer_with_backup",
    "ecmp",
    "ordered_preference",
    "pin_to_exit",
    "avoid_exit",
    "path_migration",
)

# 便于快速 membership 判断
INTENT_TYPES_SET = set(INTENT_TYPES)
