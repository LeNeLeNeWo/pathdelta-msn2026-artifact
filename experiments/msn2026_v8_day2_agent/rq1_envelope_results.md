# RQ1 development result: verification-contract ablation

This is a complete run over the fresh **development** benchmark (6 atomic scenario families, 57 candidates, including 3 newly collected Direct-LLM candidates). It is not a formal paper result: labels still need an independent second audit, FEC coverage is finite, and the unsafe corpus is mutation-heavy.

| Scheme | Unsafe rejection recall | Safe acceptance | False accept | False reject | Goal-satisfying collateral accepted |
|---|---:|---:|---:|---:|---:|
| V0 syntax | 0/30 (0%) | 27/27 (100%) | 100% | 0% | 18/18 |
| V1 goal | 0/30 (0%) | 27/27 (100%) | 100% | 0% | 18/18 |
| V2 write scope | 12/30 (40%) | 27/27 (100%) | 60% | 0% | 18/18 |
| V3 dependency | 6/30 (20%) | 27/27 (100%) | 80% | 0% | 12/18 |
| V4 semantic frame | 18/30 (60%) | 27/27 (100%) | 40% | 0% | 0/18 |
| V5 full envelope | 30/30 (100%) | 27/27 (100%) | 0% | 0% | 0/18 |

The decisive pilot observation is that goal-only approved all 18 target-success candidates with collateral behavior, while the semantic frame rejected all 18. Static write scope rejected over-broad/footprint candidates but none of the collateral class. Dependency protection caught the six shared-object mutations but not target-local edits that changed another FEC/path or a non-target session. Full acceptance needed the union of semantic, dependency, and footprint dimensions.

V5 also accepted all four independently constructed safe classes (A local, G reuse, H brownfield-conformant, and I stylistically nonconformant) across all six scenarios, plus all three direct-LLM candidates. This is development evidence that conformance is not a hidden hard gate and the contract permits multiple implementations.

All 57 candidates passed real FRR `vtysh -C` after a full dataset rebuild fixed an invalid community-list command. The first run and reason for rebuild are not reported as model performance; the corrected generator version is `0.1.1`. Machine-readable aggregate results are in `results/msn2026_v8_rq1_dev/rq1_results.csv`, candidate reports in `rq1_candidate_results.json`, and the retained counterexamples in `failure_examples/`.

## What this does not establish

Perfect V5 separation is a warning as well as a positive signal. The six families were generated in-repository, every deterministic unsafe candidate belongs to a named A–I class, and all scenarios retain a shared-policy base motif. The three fresh LLM candidates happened to be safe and therefore do not test rejection of naturally occurring LLM collateral. Before any formal claim, the corpus must add public-source atomic families, naturally failed LLM trajectories, independent labels, Batfish/Rela coverage, and stronger simple baselines such as preserve-all and dependency blacklist. No confidence interval or significance claim is made here.

