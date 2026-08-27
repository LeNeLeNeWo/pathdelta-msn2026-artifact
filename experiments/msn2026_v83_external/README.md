# MSN 2026 v8.3 major-revision experiments

This experiment line addresses the external-validity and coverage concerns in
the independent review of the v8.2 paper. The v8.1 Change Envelope compiler and
acceptance semantics remain frozen. v8.3 may add source adapters, benchmark
builders, metrics, strong baselines, and analysis code, but it must not change
the frozen boundary after protocol freeze.

Fresh inputs: `data/msn2026_v83_external/`

Fresh outputs: `results/msn2026_v83_external/`

Primary additions are an external adversarial challenge, a verifier-in-the-loop
baseline, an oracle-contract upper bound, a larger paired Agent study,
leave-one-out component ablations, missing-coverage sensitivity, complete
backend applicability accounting, and scalability measurements.

