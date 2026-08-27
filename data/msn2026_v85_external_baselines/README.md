# MSN 2026 v8.5 external-method comparison data

This directory is generated from freshly downloaded public sources. It must
not contain copied v2-v8.4 experimental inputs, candidates, labels, CSV files,
or results. Earlier metadata is used only to exclude source scenarios seen
during controller development.

Public inputs are under `public/`. Candidate-independent active observations
and complete finite scoring observations are under `sealed/`; the runner must
not read `sealed/` until it has produced a method's final candidate.

Every source revision, seed, split, and tree hash is recorded in JSON
manifests. The eight-case pilot and forty-case confirmatory split remain
separate throughout analysis.
