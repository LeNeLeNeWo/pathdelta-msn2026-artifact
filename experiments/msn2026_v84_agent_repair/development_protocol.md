# Agent Repair v2 development protocol

Status: post-freeze mechanism development; the v8.3 Agent128 result remains
immutable and confirmatory claims are not reassigned to this experiment.

The 79 v8.3 Full Envelope exhaustions are used only to diagnose and develop an
impact-aware repair controller.  The verifier remains unchanged.  Repair v2
adds four agent-side capabilities:

1. patch-free counterexamples carry behavior dimension and pre/candidate values;
2. the prompt includes a read-only ordered policy slice extracted before any
   candidate is observed;
3. each repair restarts from a clean context to avoid anchoring on rejected
   patch text, while retaining only semantic failure signatures;
4. the LLM performs an explicit impact check and receives up to eight total
   submissions.

The 12-case disabled-thinking pilot recovered 5/12.  A pre-freeze four-case
Cisco A/B then recovered 4/4 with the same model's thinking mode.  The selected
replay configuration is therefore thinking enabled, temperature 0.1, maximum
8 submissions, and maximum 8,000 completion tokens.  The selection and all
implementation hashes are frozen before the remaining development replay.

Feedback is rejected if it contains an expected/correct patch, replacement
text, recommended object, required strategy, deterministic renderer output, or
the strategy tokens forbidden by the v8 feedback sanitizer.  The LLM still
chooses objects, names, ordering, edit shape, exact commands, and replacement
text.

Development target: recover at least 41 of the 79 old exhaustions, which would
correspond to at least 90/128 completions when combined with the 49 immutable
v8.3 completions.  This target is an engineering goal, not a statistical gate.
A mechanism version is frozen only after a diverse pilot; any paper-level
generalization must then use a fresh candidate holdout.
