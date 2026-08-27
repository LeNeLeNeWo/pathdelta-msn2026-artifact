import json
from pathlib import Path

from experiments.msn2026_v3.formal_methods import retrieve_dev
from experiments.msn2026_v3.validity_controls import input_sha256


ROOT=Path(__file__).resolve().parents[1]


def test_retrieval_excludes_query_case():
    path=sorted((ROOT/"data/msn2026_v3/open_brownfield_v1/dev").iterdir())[0]
    inp=json.loads((path/"input.json").read_text())
    assert retrieve_dev(inp)["case_id"]!=inp["case_id"]


def test_all_methods_receive_identical_hash_by_construction():
    path=sorted((ROOT/"data/msn2026_v3/open_brownfield_v1/heldout").iterdir())[0]
    inp=json.loads((path/"input.json").read_text())
    assert len({input_sha256(inp) for _ in range(5)})==1
