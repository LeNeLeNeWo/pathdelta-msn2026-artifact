# Semantic minimality and blast radius

PathDelta does not equate a short diff with a safe or desirable Day-2 change. Every candidate is reported as three separate footprints and an acceptance contract.

## Footprints

**Textual footprint** counts inserted, deleted, and replaced lines using sequence alignment. A replacement counts as one modified line; unmatched excess lines count as additions or removals.

**Structural footprint** reports devices touched, policy objects touched, neighbor bindings changed, new objects, and newly introduced references to existing objects (reuse). It is computed from pre/post typed dependency graphs.

**Semantic footprint** reports changed target behavior atoms, changed non-target atoms, session/path-relation changes, missing post-state behaviors, and protected dependency violations. A behavior atom is an independently observed `(behavior_id, dimension)` pair, such as one neighbor/FEC's `local_pref` or `path`.

`semantic_delta_size` is the cardinality of the union of those changed/protection atoms. It intentionally has no hidden severity weights; papers and tables must also show its target, non-target, session/path, and dependency components. Comparisons are valid only under the same behavior-universe extraction and coverage.

## Acceptance terms

- **Goal Success:** every `TargetDelta` relation holds in the post-state.
- **Collateral Change:** at least one observed non-target behavior atom changed or disappeared.
- **Envelope Compliance:** Goal Success AND Semantic Frame preserved AND protected dependency frame preserved AND hard footprint authorization preserved.
- **Conformance:** a separate soft score; it cannot cause an otherwise safe candidate to fail Envelope Compliance.

The result is described as bounded verification when behavior coverage is incomplete. A zero measured semantic delta outside the target is not an end-to-end proof.

## Motivating counterexample

The artifacts in `example_artifacts/semantic_minimality/` use one immutable brownfield pre-state in which two neighbors share an inbound route-map.

- Patch A inserts a target-specific clause into that shared object. Its textual diff is shorter and its target local preference becomes 250, but the same non-target prefix/neighbor behavior changes and protected shared dependencies are edited.
- Patch B creates a target-local policy and changes only the target binding. Its configuration diff is longer, but the independently evaluated non-target behavior remains unchanged.

This example motivates the metric and RQ1; it is not included in the fresh evaluation benchmark and is not a statistical result.

