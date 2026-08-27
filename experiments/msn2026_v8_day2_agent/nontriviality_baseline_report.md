# Strong-baseline audit (development evidence)

This audit asks a hostile-reviewer question before extending the experiment:
does the current Full Change Envelope reject anything that the much simpler
conjunction **preserve every observed non-target behavior + obey a static write
scope** does not reject?

The script `nontriviality_baselines.py` compares Goal-only, write-scope,
dependency-only, preserve-observed, preserve-observed-plus-scope, and Full
Envelope on exactly the same already-generated candidates.  It deliberately
does not produce new candidates or make a paper claim.

If the composite and Full Envelope are identical, the correct interpretation is
not that the mechanism has been validated.  It means the existing benchmark
does not isolate dependency protection.  The next frozen benchmark must include
independently generated cases with incomplete observations (latent/dormant FECs)
and legitimate target-exclusive in-place edits.  Those cases test both sides of
the claimed contract:

1. dependency reasoning rejects latent shared-policy collateral that an
   observed-behavior baseline cannot see; and
2. intent-relative permission accepts a safe local edit that a conservative
   "do not edit dependencies" blacklist rejects.

Results are written under `results/msn2026_v8_nontriviality_dev/` and remain
development-only until an independently frozen challenge set is evaluated.
