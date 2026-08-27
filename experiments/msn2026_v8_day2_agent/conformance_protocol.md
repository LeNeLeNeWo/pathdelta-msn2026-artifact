# RQ3 brownfield conformance protocol

Conformance is a secondary outcome measured only among candidates that already satisfy the same semantic safety contract. It is never part of V5 acceptance and is not evidence that operators prefer a patch.

Automatic proxies report existing-object reuse rate, new and structurally duplicate new objects, naming-family match, sequence-spacing deviation, parameter-grid deviation, dependency-motif similarity, normalized config-AST edit distance, and devices/objects/lines touched. Metrics are inferred from the pre-state and score the model's output; no deterministic code chooses the patch. “Unnecessary object,” structural similarity, and AST distance are explicitly proxy labels.

RQ3 compares Direct, Context/RAG, strong Iterative, and PathDelta on paired cases with the same model and budgets. Unsafe candidates are reported separately and excluded from preference ranking, preventing a very conformant but dangerous shared-object edit from winning. Per-source and per-pattern distributions are shown rather than one composite score.

If network-experienced evaluators can be recruited, the package in `human_eval_package/` supports blinded pairwise judgments. Each item presents immutable current config, intent, anonymized A/B patches, verifier evidence hidden until after judgment, and four questions: semantic acceptability, fit with local idiom, maintainability, and preference/tie. Patch order and method identity are randomized. Rater experience, confidence, agreement, and adjudication are retained. Without completed human responses, the paper must say “automatic conformance proxy,” never “operator preference.”

