# v8 pilot pivot decision

Decision: **keep the Change Envelope thesis, narrow the immediate claim, and do not formal-freeze yet.** Do not change the ground truth, remove difficult cases, or weaken iterative/write-scope baselines.

Mechanism changes accepted from pilot evidence:

1. Dependency hard protection now means external sharing across subjects. Target-exclusive policy objects may be modified if the semantic frame remains satisfied; this restores Agent freedom and avoids encoding local fork as the answer.
2. Soft conformance remains outside safety acceptance. The old v1 hard style gate is retired from v2 evidence.
3. Backend completeness and N/A are first-class. “Safe” becomes “bounded verified over the extracted behavior universe.”
4. Counterexamples remain evidence-only and are guarded against patch/strategy leakage.

Scope is narrowed to BGP Day-2 policy and path/session relations on FRR-like brownfield networks. Multi-protocol and production-scale claims are removed. Public artifacts are described as source-conditioned mutations, not production configs.

Before formal admission, the next evidence must be collected without result-chasing: independently audit labels; generate public-source candidate patches including naturally failed LLM trajectories; preregister multiple paired LLM seeds/cases rather than rerun until collateral appears; add preserve-all and dependency-blacklist baselines; verify a meaningful Batfish/Rela subset; and execute a stratified Kathara dynamic sample. If the same-LLM paired effect remains absent, the paper can at most claim an acceptance-contract benchmark/mechanism, not an Agent efficacy improvement.

