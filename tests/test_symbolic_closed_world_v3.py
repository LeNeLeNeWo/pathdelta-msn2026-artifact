import json
from pathlib import Path

from experiments.msn2026_v3.methods.symbolic_closed_world import compile_case
from experiments.msn2026_v3.evaluators.obab_semantics import evaluate


ROOT = Path(__file__).resolve().parents[1]


def test_dev_case_compiles_and_satisfies_properties():
    case = sorted((ROOT / "data/msn2026_v3/open_brownfield_v1/dev").iterdir())[0]
    inp, oracle = json.loads((case / "input.json").read_text()), json.loads((case / "oracle.json").read_text())
    result = compile_case(inp)
    assert result.status == "SUCCESS"
    assert evaluate(inp, oracle, result.patches)["semantic_ok"]


def test_call_control_flow_is_explicitly_unsupported():
    case = next(p for p in (ROOT / "data/msn2026_v3/open_brownfield_v1/heldout").iterdir()
                if json.loads((p / "case_manifest.json").read_text())["factor"] == "call_continue")
    inp = json.loads((case / "input.json").read_text())
    assert compile_case(inp).status == "UNSUPPORTED"
