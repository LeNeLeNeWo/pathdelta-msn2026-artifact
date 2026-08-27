# Prompt 12 disposition: formal experiment not run

The formal admission protocol was applied before a dataset/model/result freeze. The data-quality prerequisite and Gates 2, 3, 4, and 6 failed; Gates 1 and 5 were only partial. Therefore Prompt 12's condition was not satisfied and no full LLM experiment, formal test-set classification, confidence interval, or paper result was produced.

This stop prevents three invalid moves: treating a template-heavy development benchmark as a test set, resampling LLM candidates until Full feedback appears better, and claiming Batfish/Rela/Kathara scale from one integration smoke. Existing outputs remain explicitly named `dev`, `pilot`, or `smoke`.

When the listed gates are satisfied in a future iteration, create a new immutable freeze ID and manifest containing dataset/source commits, candidate hashes, independent labels, splits, envelope code hash, agent prompts, model/backend, budgets, verifier versions, and statistics. Any later semantic bug requires a new freeze and rerun of all affected methods/cases.

