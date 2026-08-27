# Counterexample-guided configuration editing

The loop is `LLM candidate → transactional apply → FRR/backend checks → structured counterexample → LLM revision`. Every revision is again a complete baseline-relative model-owned patch. PASS, attempt/token exhaustion, transaction failure, and backend N/A are distinct stop states.

Allowed feedback identifies an unmet target relation, an observed protected FEC/path/session change, a changed shared dependency, a hard footprint measurement, FRR parser diagnostics, or a Batfish/Rela pre/post relation counterexample. It can include obligation IDs and observed values needed to understand the violation.

Feedback cannot contain an expected or corrected patch, `old_text/new_text`, a recommended object, a command value, APPEND/PREPEND/REBIND/local-fork strategy, or deterministic renderer output. `assert_feedback_is_patch_free` enforces both forbidden keys and strategy tokens before the payload reaches the model.

Each run reports first-attempt contract success, counterexample types, whether a later model revision succeeded, logical calls, backend attempts/retries, tokens, latency, and whether any revision introduced a new non-target semantic atom. Raw responses remain in the editing trace. A revision that fixes one violation while adding a new collateral violation is not counted as successful.

