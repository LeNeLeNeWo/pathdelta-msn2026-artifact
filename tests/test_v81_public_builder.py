from tools.build_msn2026_v81_public_nontriviality import route_map_blocks, unsafe_shared_clause


def test_route_map_clone_keeps_clauses_and_renames_only_header():
    text = "route-map RM_A permit 10\n match community C\nroute-map RM_B permit 10\n set local-preference 2\n"
    cloned = route_map_blocks(text, "RM_A")
    assert "route-map RM_PD81_LOCAL permit 10" in cloned
    assert "match community C" in cloned
    assert "RM_B" not in cloned


def test_unsafe_clause_targets_declared_shared_object():
    text = "route-map RM_SHARED permit 10\n set local-preference 100\n!\nline vty\n!\n"
    candidate = unsafe_shared_clause(text, "RM_SHARED")
    assert candidate.count("route-map RM_SHARED") == 2
