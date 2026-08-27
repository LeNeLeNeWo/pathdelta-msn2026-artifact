import ast
from pathlib import Path

from tools.build_msn2026_v82_dataset import FAMILIES, candidate_configs, observations, scenario_spec


def test_generator_has_no_pathdelta_imports():
    path = Path("tools/build_msn2026_v82_dataset.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert not any("msn2026_v8_day2_agent" in name or "change_envelope" in name for name in imports)


def test_every_family_has_three_safe_and_three_unsafe_mutations():
    import random
    source = {
        "source_id": "synthetic",
        "style": {"step": 7, "rm_prefix": "legacyRm_", "pl_prefix": "legacyPl_"},
    }
    kinds = (
        "safe_local_fork", "safe_reuse", "safe_alternative",
        "unsafe_visible", "unsafe_active_hidden", "unsafe_opaque_or_scope",
    )
    for index, family in enumerate(FAMILIES):
        spec = scenario_spec(index, 0, source, random.Random(1))
        assert spec["family"] == family
        for kind in kinds:
            configs = candidate_configs(spec, kind)
            assert spec["device"] in configs
        obs = observations(spec)
        assert obs["visible"] and obs["active"] and obs["heldout"]
