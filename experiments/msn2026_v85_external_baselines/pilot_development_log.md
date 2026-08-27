# Pilot-only development log

This log records all mechanism changes made after inspecting the eight-case
pilot and before freezing the disjoint forty-case confirmatory split. Pilot
outcomes are development evidence and are never pooled with confirmatory
results.

## Smoke calibration

The first one-case smoke exposed a task-interface artifact: legacy object names
suggested the opposite direction from the actual neighbor-policy binding.
Before the pilot, every method was given the same candidate-independent parsed
binding, ordered terms, resolved referenced objects, and pre-change target
trace. INTA release was also required to pass the visible target semantics
rather than trusting a hallucinated LLM audit. Cornetto malformed JSON became a
recoverable tool observation. Common budgets and oracle scoring were unchanged.

## Pilot v1 (all methods)

PathDelta achieved 1/8 verified completions and 0 unsafe releases. Its verifier
blocked unsafe candidates, but repair frequently exhausted. The adapted
baselines achieved: LLM-NetCFG 2/8 VC with 4 unsafe releases; INTA 0/8 VC with
1 unsafe release; CoSynth 1/8 VC with 1 unsafe release; Cornetto 5/8 VC with
1 unsafe release.

Trace audit found three controller/interface failures: missing device keys
produced only a non-actionable KeyError; target-specific terms were often
placed after an already-matching terminal term; and earlier target terms could
drop pre-existing target attributes.

## Pilot v2 (PathDelta only)

We added actionable common transaction diagnostics and general first-match and
attribute-preservation instructions. Acceptance rules and witnesses were
unchanged. PathDelta improved to 4/8 VC and 0 unsafe releases. The remaining
failures either referenced an undefined new match object, repeated an
unreachable term, or did not act on a long counterexample trace.

## Pilot v3 (PathDelta only)

We changed the verifier-to-agent interface, not the acceptance contract. Each
counterexample now carries a compact candidate execution trace: evaluated
terms, match-object outcomes, applied terminal action, and result. The request
also explicitly lists pre-change target attributes protected by the Envelope.
The evidence does not contain a correct patch, new object name, sequence
position, or command.

The final development replay achieved 8/8 VC, 0 unsafe releases, and 23 logical
LLM calls. This prompt/adapter version is frozen before opening or running the
confirmatory split.

