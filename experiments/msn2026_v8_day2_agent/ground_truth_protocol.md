# Change Envelope benchmark ground-truth protocol

The benchmark unit is one immutable brownfield pre-state, one typed Day-2 intent, and one candidate post-state. Labels are assigned from independently generated pre/post behavior observations plus configuration/dependency audit; the envelope implementation is not imported by the dataset generator.

For every candidate, auditors answer in order: (1) does every target relation hold; (2) did any observed target-complement behavior atom change; (3) was an existing shared dependency definition or outgoing reference changed; (4) did the candidate leave the explicitly authorized device/object/binding/command footprint; and (5) is the candidate semantically acceptable under all hard dimensions. Brownfield conformance is a separate label and never changes semantic acceptability.

Development labels are generated deterministically and receive one author audit. Before a formal freeze, two auditors must independently inspect anonymized pre-state, intent, candidate, and pre/post observations. Disagreements are adjudicated with an explanation retained in `adjudication.jsonl`; Cohen's kappa and raw agreement are reported. Any oracle or parser uncertainty is `N/A`, not coerced to PASS or FAIL.

Candidate A–I cover safe local, shared collateral, over-broad rewrite, non-target path change, session/binding regression, hard-footprint violation, semantically equivalent alternative, conformant safe, and style-nonconformant safe implementations. Safe alternatives G/H/I are essential negative controls against a hidden single-answer planner.

