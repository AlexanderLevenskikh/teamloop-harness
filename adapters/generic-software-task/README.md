# Generic software-task boundary adapter

This reference adapter measures required files/directories directly, reads structured validation evidence, groups findings by `rootPatternId`, and exposes the common payoff model. It is intentionally domain-neutral: migration tools, dependency updaters, codemods, documentation generators, and CI repair workflows provide contracts rather than changing the runtime.


For current-input validation, set `bindToPrimaryArtifacts: true` on validation evidence and write the packet's primary-artifact fingerprint to the configured `inputFingerprintField`. A copied PASS result then becomes `STALE` after artifact drift. Evidence-only changes cannot count as improvement; the selected bounded cycle must change primary artifacts and improve authoritative metrics.
