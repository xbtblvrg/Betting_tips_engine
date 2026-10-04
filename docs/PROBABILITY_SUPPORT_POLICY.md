# Versioned probability support

`probability_support_policy=validated_mass_v1` admits supported exact contracts beyond the historical 0.12–0.90 win-probability band. It preserves the model's finite valid win/push mass instead of applying the legacy 0.01/0.99 clip. Existing live market anchoring remains independently reproduced by EvidenceGuard. The scenario stress floor cannot exceed the actual win probability. Zero-win contracts remain non-executable because no finite minimum odds exist.

This is candidate eligibility, not automatic funding or evidence of calibrated tail accuracy. Existing model-family quality, exact scope, source provenance, freshness, funding tiers, CLV gates, sizing and exposure checks remain mandatory. Unknown policy versions fail closed. The candidate and guard carry a sealed policy receipt; publication/current execution reject a receipt reviewed under a different active policy.

Omitted settings retain `legacy_band_v1` and the historical seal shape and numerics. Root integration must expose the setting in the API and carry the new receipt through dashboard projection before explicit activation. No production database or model is modified by this change.

The regression fixture contains unmodified raw WNBA and NFL overtime contracts from the catalog archive named in the fixture, including source hashes. Test score weights are synthetic boundary probes, not estimated probabilities for the archived fixtures; archived odds are not claimed current executable prices. Main-market guard tests show both numeric reproduction and ordinary USD1–5 funding when the existing stressed-value threshold qualifies, while bad offered prices remain unfunded.
