from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

from .models import PolicySketchDiff, RolePolicy


def _write_yaml(data, path: Path) -> None:
    if yaml:
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_role_policy(role_policy: RolePolicy, output_path: str) -> None:
    policies = []
    for p in role_policy.policies:
        pd = asdict(p)
        # 如果 preference_tiers 为空或 None，可选择不输出，保持 YAML 简洁
        if not pd.get("preference_tiers"):
            pd.pop("preference_tiers", None)
        policies.append(pd)
    data = {"policies": policies}
    _write_yaml(data, Path(output_path))


def write_policy_sketch_diff(diff: PolicySketchDiff, output_path: str) -> None:
    data = {"before": diff.before, "after": diff.after}
    _write_yaml(data, Path(output_path))


def write_policy_outputs(
    role_policy: RolePolicy,
    diff: PolicySketchDiff,
    output_dir: Path,
    intent_id: str,
) -> None:
    """
    写策略层输出，包含“快照”与“当前版本”：
    - 快照：RolePolicy_{intent_id}.yaml / PolicySketch_{intent_id}.diff（按 intent_id 命名，可覆盖）
    - 当前：RolePolicy.yaml / PolicySketch.diff（每次覆盖）
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if not isinstance(intent_id, str) or not intent_id.strip():
        raise ValueError("intent_id is required to write per-intent snapshot outputs (got empty intent_id).")
    intent_id = intent_id.strip()

    # 写当前版本
    write_role_policy(role_policy, str(output_dir / "RolePolicy.yaml"))
    write_policy_sketch_diff(diff, str(output_dir / "PolicySketch.diff"))

    # 写快照版本
    write_role_policy(role_policy, str(output_dir / f"RolePolicy_{intent_id}.yaml"))
    write_policy_sketch_diff(diff, str(output_dir / f"PolicySketch_{intent_id}.diff"))
