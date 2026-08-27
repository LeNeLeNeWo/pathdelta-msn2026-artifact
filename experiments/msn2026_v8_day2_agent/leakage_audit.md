# Benchmark leakage audit

The fresh builder deletes and recreates only `data/msn2026_v8_envelope_benchmark/`; it reads no old `data/`, CSV, results, benchmark, v1 candidate fixture, or expected patch. Every generated file is hashed in `benchmark_manifest.json` with seed and generator version.

Candidate mutation code and label generation live in `tools/build_msn2026_v8_envelope_benchmark.py`, which imports no Change Envelope module. Envelope inference consumes only intent, baseline, pre-state observations, and dependencies; it cannot read candidate IDs, labels, post observations, or provenance. RQ1 execution will enforce this with separate loader functions and tests.

Known development risks remain. Candidate templates and the envelope are maintained in the same repository and currently have one primary author. Synthetic observation generation is not a replacement for Batfish/Kathara. Most unsafe candidates are mutation-generated, and the initial six scenarios retain a shared-policy motif even when additional brownfield constructs are present. Accordingly, this corpus is a pilot input, not a formal test set. Public-source atomic splits, actual raw LLM candidates, independent audit, verifier agreement, and a frozen hash set are required before formal use.

The unsafe generator is allowed to contain candidate-family branches because its job is to create diverse faults. Those branches are not shared with the general envelope algorithm. The benchmark will not delete failures, change labels after seeing V5, or regenerate only cases rejected by one method.

