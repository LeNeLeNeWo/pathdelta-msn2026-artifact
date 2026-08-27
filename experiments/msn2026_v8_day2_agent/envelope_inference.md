# Change Envelope v2 inference

## Contract

`derive_change_envelope_v2` consumes a partial, typed Day-2 intent, the immutable pre-change configurations, an independently extracted behavior universe, and a typed dependency graph. It returns target relational obligations, the complement frame, protected dependency closure, hard footprint authorization, soft conformance evidence, coverage, and provenance. It never returns commands, an object to edit, a patch, or an implementation strategy.

The partial intent keeps the natural-language request for audit, but the safety boundary is not silently inferred from unconstrained prose. A normalizer supplies zero or more entity selectors and typed semantic change atoms. Missing entities are grounded only through literal matches against the observed device/subject/FEC catalog. Ambiguous or empty grounding fails closed and is recorded as an inference failure rather than guessed by a patch planner.

## General algorithm

1. Enumerate current behavior as records `(device, subject, FEC, attributes)` using Batfish, FRR/Kathara observations, or a declared development evaluator. Record whether enumeration is complete.
2. Ground the intent selector against this catalog. This stage is generic over entity strings and behavior attribute names.
3. Cross the selected behavior records with typed change atoms to form `TargetDelta` obligations.
4. Form `SemanticFrame` by preserving every observed `(behavior, attribute)` pair not selected by `TargetDelta`. Attributes of a target FEC that were not requested also remain in the frame.
5. Traverse the policy dependency graph from target subjects. Independently traverse from non-target subjects. Their intersection is the protected shared dependency set.
6. Derive hard footprint authorization from target-device cardinality, subject cardinality, target closure density, protected object kinds, and effect annotations on pre-state graph nodes. The budget has no command values or object names to create.
7. Infer naming families, sequence spacing, parameter grids, reusable objects, and structural idioms from the existing configuration. These are soft preferences and never enter semantic acceptance.

## Freedom and failure behavior

The envelope accepts any candidate whose post-state satisfies all target relations and frame preserves, leaves protected dependencies intact, and stays within hard authorization. Reuse, a local fork, different route-map sequences, or any other implementation may pass. Conformance scores can rank two accepted candidates but cannot turn a semantically valid patch into a safety failure.

If FEC enumeration is sampled, the result is `bounded_verified` over the recorded universe, not universally safe. An empty target, an unknown relation, or missing behavior dimension causes explicit inference failure. Backend `N/A`, parser warning, and verifier `FAIL` remain distinct downstream states.

## Handwritten special-case accounting

| Component | Current count | Interpretation |
|---|---:|---|
| Intent-family branches in envelope inference | 0 | No `if family == ...` preservation logic. |
| Expected-patch templates | 0 | No patch is available to the inference path. |
| Strategy labels emitted | 0 | No APPEND/PREPEND/REBIND/local-fork instruction. |
| Protocol dependency adapters | 1 | FRR route-map/prefix-list/community/call/binding extraction is domain parsing, not an intent-family answer. |
| Relational operators | 6 | A fixed algebra: preserve, replace, add, remove, preferred-exit change, metric-order change. |

The count is returned by `handwritten_special_case_audit()` and asserted in tests. This does not prove generality: the current behavior adapter evidence is still BGP-policy focused, and natural-language normalization remains an evaluated component rather than trusted magic.

