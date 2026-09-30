# September 30 pilot candidate

`pilot_gen3_neon.patch` is the generation-3 candidate from the bounded four-proposal
pilot using `headless/codex@gpt-6-astra?effort=high`. It applies to pinned Stockfish
commit `0a215d6c9e48856ef630013b8ab8312941a59057`.

It changes only three allowed NNUE files: ARM lane dot products, the sparse
layer's accumulator bank count, and GCC/NEON weight-column pointer scheduling.
All other candidate files are byte-identical to the seed. Applying the patch to
the frozen seed was verified to reproduce every candidate source file exactly.
Candidate artifact:
`sha256:6974a34e351f55d2d4bb9bc15abe34df9f5608a320a9c969be265c34aaa76fe6`.

The trusted Graviton pilot evaluator checked 162,048 exact values and all fixed
search fingerprints across 128 process blocks. Geometric fixed-search speedup
was **1.023302**, with a one-sided 95% lower confidence bound of **1.022015**.
This is a pilot-corpus result, not an Elo or general Stockfish speed claim.

ASan/UBSan passed **82,016 exact values and twelve depth-11 searches** on fresh
fixtures. **Independent timing remains pending:** a separate campaign uses twelve
new private search positions, the other Spot worker, unchanged 128-block gates,
and fresh controls. Do not promote the patch based only on the pilot.
See [the pilot report](../../../docs/stockfish_search_pilot_2026-09-30.md).
