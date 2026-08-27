import pytest

from experiments.msn2026_v3.validity_controls import canonical_input, strict_outcome, topology_cluster_bootstrap


def test_hidden_hint_rejected():
    with pytest.raises(ValueError):
        canonical_input({"case_id":"x","topology":{},"current_configs":{},"raw_intent":"x","template_hint":"bad"})


def test_na_is_distinct_from_fail():
    assert strict_outcome(available=False, synthesis=None, semantic=None, external=None) == "N/A"
    assert strict_outcome(available=True, synthesis=True, semantic=False, external=True) == "FAIL"
    with pytest.raises(ValueError): strict_outcome(available=False, synthesis=False, semantic=None, external=None)


def test_bootstrap_resamples_clusters_not_rows():
    rows=[{"topology_cluster":"a","ok":1},{"topology_cluster":"a","ok":1},{"topology_cluster":"b","ok":0}]
    result=topology_cluster_bootstrap(rows,"ok",replicates=100)
    assert result["resampling_unit"] == "topology_cluster"
    assert result["estimate"] == 0.5
