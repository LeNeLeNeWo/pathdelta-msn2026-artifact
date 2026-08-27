# v8 evidence-driven pilot report

The pilot subset was frozen before its live paired call and spans four generated candidate scenarios, three public-source-conditioned pre-states from distinct repositories/splits, and one Batfish/Rela path-relation scenario. Hashes and selection rules are in `results/msn2026_v8_evidence_pilot/pilot_subset_manifest.json`. This is development evidence, not a formal freeze.

## RQ1 — does the envelope catch goal-satisfying collateral?

On 38 frozen candidate rows, Goal-only accepted all 12 target-success collateral candidates; Full accepted 0/12. Full accepted 18/18 ground-truth-safe alternatives across local, reuse, conformant, style-nonconformant, and direct-LLM classes. Static write scope did not reject the collateral class in the larger 57-candidate development run; semantic-frame checking was the decisive component.

This supports the motivating mechanism on the current finite universe. It does not support a population estimate because most unsafe candidates are template mutations and labels await a second auditor.

## RQ2 — does Full feedback improve the same LLM editor?

The pre-frozen paired trial generated one fresh DeepSeek candidate and evaluated that identical candidate under Goal-only and Full. It was already target-correct and collateral-free, so both contracts accepted on the first attempt. Result: **inconclusive**, not a win for PathDelta.

A separate harness smoke produced a natural Direct-LLM shared-object collateral patch while PathDelta's independent trajectory was safe, but different stochastic trajectories cannot isolate feedback. The experiment therefore has not established an LLM editing utility effect.

## RQ3 — is the envelope a hidden single-answer planner?

Full false-rejected 0/18 safe candidates in the frozen subset and accepted five distinct candidate classes, including a style-nonconformant safe class. This is positive development evidence for implementation freedom. It remains co-designed synthetic evidence and needs independent alternative implementations on public-source cases.

## RQ4 — is inference general or a rule library?

The code audit reports zero intent-family dispatch branches, expected-patch templates, or emitted strategy labels. The same target/complement/closure algorithm ran on FRR on-match-next, Kathara-derived route-map call/shared-child, and containerlab-derived reuse/style cases. The behavior universe was finite in every case, so generality is structural rather than semantic-completeness evidence.

The first cross-source run exposed an over-conservative closure bug: target-subject non-target FECs made a target-exclusive route-map appear externally shared. The mechanism was corrected to treat only other subjects as hard dependency sharers; same-subject other FECs remain protected by the SemanticFrame. All RQ1 candidates were rerun and aggregate results remained unchanged. The exact pilot LLM trace was retained rather than resampled.

## RQ5 — are Batfish and Rela complementary?

For both a valid target path replacement and a collateral path replacement, Batfish differential reachability returned zero rows because all destinations stayed reachable. Rela passed the target/control preserve case and rejected the collateral control-path change. This supports a narrow complementarity claim for path-only changes. FEC extraction remains destination-sampled and no end-to-end proof is claimed.

## Pilot verdict

RQ1, implementation freedom, generic code structure, and the single verification-complement example are promising. The decisive RQ2 effect is absent, public-source cases lack independently generated candidate corpora, FEC completeness is bounded, Kathara has not yet run on the new subset, and labels are not independently audited. Formal admission criteria must therefore be strict; the project is not ready for a full experiment.

