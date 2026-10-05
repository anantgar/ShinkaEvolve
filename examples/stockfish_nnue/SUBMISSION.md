# Stockfish submission

[Draft PR #7208](https://github.com/official-stockfish/Stockfish/pull/7208)
materializes GCC NEON weight pointers to enable paired vector loads. It contains
one optimization, one commit (`830c5c3`) and AUTHORS. Lane-dot and accumulator-bank
changes were removed after review.

Bench remains **1714434**. Pointer replay passed **237,420 exact integer
comparisons per ARM family**. clang-format 20 and all **59 full fork CI jobs**
[passed](https://github.com/anantgar/Stockfish/actions/runs/37245204905).
[Assembly evidence](submission-evidence/2026-10-04/ASSEMBLY.md) explains the load
changes; static instruction counts alone do not establish speed.

**No qualified speed result yet.** October 3/4 claims are withdrawn because
baseline and candidate version metadata differed. Equal metadata subsequently
exposed separate-PGO code differences on unaffected x86 before timing. The
[corrected protocol](AWS_PR_VALIDATION_2026-10-05.md) uses the same frozen baseline
profile for both builds and requires excluded-path code identity.

Remaining work: complete pointer-only workload/depth, compiler/NEON, x86 and
independent-boot comparisons; recheck master; Fishtest is deferred by the user.
Rejected measurements remain inconclusive. The PR stays draft.

The user has no Fishtest account. A [prepared configuration](submission-evidence/2026-10-04/FISHTEST.md)
pins the revisions, benches and affected ARM/GCC scope. A future game test needs
a maintainer/account holder or an agreed direct-evidence exception. No game
result, exemption or Elo gain is claimed.

Historical observations and exact-output receipts are retained in the
[October 3](AWS_VALIDATION_2026-10-03.md) and
[October 4](AWS_PR_VALIDATION_2026-10-04.md) audit records.
