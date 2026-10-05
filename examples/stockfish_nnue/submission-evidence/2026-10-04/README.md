# Expanded PR validation evidence

**Timing claims withdrawn:** these builds differed in `GIT_DIFFINDEX` between
baseline and patched trees. Even accepted controls do not isolate a source
optimization under that build mismatch. See the [corrected protocol](../../AWS_PR_VALIDATION_2026-10-05.md).
Complete statistics and partial attempt counts remain preserved.

This directory contains the frozen component patches, launch plan and original
operator worker (`worker.py.txt`, preserved byte for byte for audit). Results are
added only after complete receipts are collected. Runtime cutoff changes are
recorded in the [run record](../../AWS_PR_VALIDATION_2026-10-04.md); measurement
budgets and acceptance rules remain unchanged.

[Native GCC assembly review](ASSEMBLY.md) records the propagation and accumulator
code changes separately. Static instruction counts are explanatory evidence,
not speed measurements.

`completed-results.json` lists collected results with the corresponding A/A
status. `scope_qualified` is false throughout this confounded attempt. A corrected
claim requires matched build metadata, a complete measurement and an accepted
control; a rejected control makes the comparison inconclusive.
This flag covers the declared operator workload, not fitness admission or Elo.
Attempt records preserve infrastructure interruptions and partial block counts
without reporting partial estimates.

The worker uses the public `validate_candidate.py` fresh-process search protocol,
compiles native GCC/Clang PGO/LTO pairs, verifies bench/exact outputs, records
assembly and performs matching A/A controls before comparisons. It requires a
prepared source archive, corpus, pinned network and input manifest; it is an
operator script for reviewed binaries, not a general candidate sandbox. It does
not launch AWS workers, submit Fishtest or admit Shinka fitness.

To reproduce the position selection, download and decompress the checksum-pinned
[Lichess September 2025 broadcasts](https://database.lichess.org/broadcast/lichess_db_broadcast_2025-09.pgn.zst).
The compressed source SHA-256, attribution and CC BY-SA 4.0 license are in
`case_identity.json`. Use the Shinka checkout with `python-chess==1.999`:

```bash
PYTHONPATH=. python examples/stockfish_nnue/submission-evidence/2026-10-04/reproduce_corpus.py \
  --pgn /private/lichess_db_broadcast_2025-09.pgn \
  --output /private/reproduced-validation
```

Run from the checkout root. The script verifies the curated PGN digest and a normalized digest of all
48 selected cases. Metadata paths can differ between environments, so the
normalized case digest is separate from the historical complete-corpus byte hash.
Keep generated concrete cases outside future mutation snapshots.

New measurements need their own input manifest and output directories; do not
rewrite the archived plan or substitute new runs for unfavorable results.
