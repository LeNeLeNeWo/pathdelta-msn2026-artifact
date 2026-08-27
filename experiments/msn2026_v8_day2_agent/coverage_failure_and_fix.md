# Coverage failure, retained negative result, and mechanism fix

The first public-source paired pilot exposed two data-label errors and one real
contract failure. The label errors assigned local preference 150 to routes that
the baseline configuration evaluates as 100. They were corrected in the fresh
dataset manifest; original result artifacts are retained.

The real failure occurred when a candidate replaced a target-exclusive policy
with a target-only policy. All supplied visible records were preserved, and no
shared dependency was modified, yet an unobserved 10/8 customer route from the
same neighbor was denied. This demonstrates that dependency protection cannot
replace semantic-universe coverage.

The revised mechanism actively derives FEC witnesses from configured
prefix-list boundaries and crosses known FECs with known subjects before
generating the envelope. Candidate patches are not used in this query plan. A
formal implementation should use Batfish symbolic route-policy equivalence
classes; the current FRR-subset evaluator is explicitly a development adapter.
All original LLM submissions are replayed byte-for-byte after this fix, with no
new API calls, so the correction cannot benefit from resampling.
