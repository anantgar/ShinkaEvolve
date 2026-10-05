# Stockfish submission

[Draft PR #7208](https://github.com/official-stockfish/Stockfish/pull/7208)
contains one GCC NEON pointer-materialization change, one commit (`830c5c3`)
and AUTHORS. Lane-dot and accumulator-bank changes were removed after review.
No README is added to Stockfish.

Bench **1714434**, exact replay, ASan/UBSan, clang-format 20 and
[full fork CI](https://github.com/anantgar/Stockfish/actions/runs/37245204905)
pass (55 jobs successful; four publication jobs skipped). [Controlled depth 11/13 comparisons](AWS_PR_VALIDATION_2026-10-05.md)
show roughly +0.8–1.1% on Graviton3 and +0.5% on Graviton4 across two boots.
These use one immutable baseline PGO profile per pair and a fixed corpus/network;
they establish neither Elo nor gains after independently retraining release profiles.
Intel and Clang identity/equivalence controls pass; ordinary NEON passes too.

October 3/4 speed claims are withdrawn because build metadata differed.
Raw history remains in the [October 3](AWS_VALIDATION_2026-10-03.md) and
[October 4](AWS_PR_VALIDATION_2026-10-04.md) audit records.

Native validation and archive/resource cleanup are complete. Upstream master was rechecked after validation.
Fishtest is deferred by the user. The PR stays draft; no game result or exemption
is claimed. A [future test configuration](submission-evidence/2026-10-04/FISHTEST.md)
is prepared for a maintainer/account holder.
