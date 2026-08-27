# Data quality incident 002: formatting lines counted as commands

Date: 2026-08-14

The first blinded external-boundary replay rejected 28 of 58 semantically safe
candidates on `changed_line_count`. All 28 were Cisco IOS configurations from
the Cornetto public corpus. Inspection after unblinding showed that each patch
changed four operational configuration commands, but the source files contained
long blank-line runs inside route-map blocks. The generic text `SequenceMatcher`
counted movement of those blank lines, reporting 15-22 touched lines and
systematically underestimating usability.

This is a measurement defect, not a threshold adjustment. The footprint budget
is defined over configuration commands. The correction excludes empty lines and
pure comment/separator lines before computing added, removed, and modified
commands. Candidate files, red-team prompts, oracle labels, Envelope inference,
dependency protection, semantic-frame obligations, and all budget parameter
values remain unchanged.

The original blinded verdict and receipt are retained under
`results/msn2026_v83_external/external_boundaries_raw_text/`. The corrected
analysis replays the exact same public candidate corpus whose pre-evaluation
tree hash is recorded in `redteam_corpus_receipt.json`. The incident and
corrected metric implementation are included in the final artifact manifest.

