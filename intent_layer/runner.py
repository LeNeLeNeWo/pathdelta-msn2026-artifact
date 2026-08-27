from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

from . import parser
from .generator import generate_assertions, generate_testcases
from .schema import IntentCard


def _read_yaml(path: Path) -> Dict[str, Any]:
    if yaml:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return json.loads(path.read_text(encoding="utf-8"))


def _next_intent_id(out_dir: Path) -> str:
    """
    Generate a new intent_id (i-001, i-002, ...) based on existing intent_*.txt files.
    """
    max_num = 0
    if out_dir.exists():
        for p in out_dir.glob("intent_i-*.txt"):
            stem = p.stem  # e.g., intent_i-003 or intent_i-003_1
            try:
                core = stem.split("intent_i-")[1].split("_")[0]
                num = int(core)
                max_num = max(max_num, num)
            except Exception:
                continue
    return f"i-{max_num + 1:03d}"


def run_intent_layer(
    scenario_name: str,
    topology_path: str,
    intent_txt_path: str,
    output_dir: str,
) -> None:
    top_path = Path(topology_path)
    intent_path = Path(intent_txt_path)
    out_dir = Path(output_dir)

    if not top_path.exists():
        raise FileNotFoundError(f"topology.yaml not found: {top_path}")
    if not intent_path.exists():
        raise FileNotFoundError(
            f"intent.txt not found: {intent_path}. Please create one then rerun."
        )

    topology = _read_yaml(top_path)
    intent_text = intent_path.read_text(encoding="utf-8")

    # Build IntentCard/Assertions/TestCases first, even if topology validation may fail later.
    intent_card: IntentCard = parser.parse_intent_text(intent_text, topology, check_topology=False)

    # If the input filename is already intent_<id>.txt, reuse that id; otherwise auto-generate.
    intent_id_from_name = None
    name = intent_path.name
    if name.startswith("intent_") and name.endswith(".txt"):
        intent_id_from_name = name[len("intent_") : -len(".txt")]

    if intent_id_from_name:
        intent_card.intent_id = intent_id_from_name
    else:
        intent_card.intent_id = _next_intent_id(out_dir)

    assertions = generate_assertions(intent_card)
    testcases = generate_testcases(intent_card, topology)

    out_dir.mkdir(parents=True, exist_ok=True)

    # 同步写入“最新”文件与带 intent_id 后缀的版本，避免覆盖历史
    intentcard_latest = out_dir / "IntentCard.json"
    intentcard_with_id = out_dir / f"IntentCard_{intent_card.intent_id}.json"
    intent_json = intent_card.model_dump_json(indent=2)
    intentcard_latest.write_text(intent_json, encoding="utf-8")
    intentcard_with_id.write_text(intent_json, encoding="utf-8")

    def dump_yaml(obj: Any, path: Path) -> None:
        if yaml:
            path.write_text(yaml.safe_dump(obj, sort_keys=False, allow_unicode=True), encoding="utf-8")
        else:
            path.write_text(json.dumps(obj, indent=2), encoding="utf-8")

    assertions_latest = out_dir / "Assertions.yaml"
    assertions_with_id = out_dir / f"Assertions_{intent_card.intent_id}.yaml"
    dump_yaml([a.model_dump() for a in assertions], assertions_latest)
    dump_yaml([a.model_dump() for a in assertions], assertions_with_id)

    testcases_latest = out_dir / "TestCases.yaml"
    testcases_with_id = out_dir / f"TestCases_{intent_card.intent_id}.yaml"
    dump_yaml([t.model_dump() for t in testcases], testcases_latest)
    dump_yaml([t.model_dump() for t in testcases], testcases_with_id)

    # Only persist a new raw intent file when the input was not already an intent_<id>.txt.
    if not intent_id_from_name:
        base_raw_name = f"intent_{intent_card.intent_id}.txt"
        raw_intent_path = out_dir / base_raw_name
        counter = 1
        while raw_intent_path.exists():
            raw_intent_path = out_dir / f"intent_{intent_card.intent_id}_{counter}.txt"
            counter += 1
        raw_intent_path.write_text(intent_text, encoding="utf-8")

    # Validate topology coverage after files are written.
    parser.validate_topology_coverage(intent_card, topology)


def main() -> None:
    parser_cli = argparse.ArgumentParser(description="Run PathDelta intent layer for a single scenario.")
    parser_cli.add_argument(
        "--scenario-name",
        default="manual",
        help="Scenario name label (used only for logging/consistency).",
    )
    parser_cli.add_argument(
        "--topology-path",
        required=True,
        help="Path to topology.yaml.",
    )
    parser_cli.add_argument(
        "--intent-txt-path",
        required=True,
        help="Path to intent.txt (natural language intent).",
    )
    parser_cli.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write IntentCard/Assertions/TestCases and intent_<id>.txt history.",
    )
    args = parser_cli.parse_args()
    run_intent_layer(
        scenario_name=args.scenario_name,
        topology_path=args.topology_path,
        intent_txt_path=args.intent_txt_path,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
