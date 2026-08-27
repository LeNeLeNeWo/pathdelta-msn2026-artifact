# RQ2 LLM editing methods

All A–G methods use `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL`, temperature 0, exact baseline-relative edits, identical FRR syntax backend, the same immutable pre-state eligibility, and the same attempt/token caps. A/B are one-shot by method definition but receive the same maximum cap; unused attempts are reported rather than reassigned.

Direct and Context methods stop after the model's first candidate. The strong iterative baseline receives retrieved dependency/configuration facts and syntax/target counterexamples. Write-Scope and Goal-Verified methods change only the acceptance contract. PathDelta receives inferred-envelope violation counterexamples. Oracle-Envelope uses the independently normalized selector/frame as an analysis upper bound; it still lets the LLM write every candidate.

The common prompt contains no local-fork, APPEND/PREPEND/REBIND, object name, expected patch, or hidden renderer. Retrieved context lists pre-state nodes and references without recommending edits. Every raw response, exact edit, candidate config, verifier result, retry, backend attempt, token count, and latency is retained.

This implementation is a benchmark harness and smoke-tested mechanism, not a formal RQ2 result. The lightweight development behavior adapter covers the current BGP local-preference cases; paper runs require the Batfish/Rela/Kathara subset and frozen prompt/model/backend versions.

