# Stockfish ARM NNUE submission

Updated October 4, 2026. The standalone branch is
[`anantgar/Stockfish:codex/nnue-neon-inference`](https://github.com/anantgar/Stockfish/tree/codex/nnue-neon-inference).
[Upstream PR #7208](https://github.com/official-stockfish/Stockfish/pull/7208)
remains a draft while review and remaining validation are open.

The tested optimization commit is `233068f32740aa08433bad2c4af89416e9b65bb4`,
on upstream `49ea5ded38315cff8e67f4a677a9e7811612fbf6`. Upstream master was fetched
again October 4 and still matches that base. The PR is now squashed to `97d81e88a303c7fa4fce945026022d2a5585218d`, including
the AUTHORS entry and a clang-format 20.1.8 continuation-indent fix. Ignoring
whitespace, the engine source matches the tested optimization. Shinka infrastructure changes stay
in this repository.

## Change and completed evidence

The patch uses ARM lane dot products instead of broadcasting the input, uses one
accumulator bank rather than three in the sparse layer, and constrains GCC NEON
weight pointers to improve compiler load scheduling. Weights, architecture,
evaluation formulas and search rules are unchanged. Individual contributions
of the three changes have not been measured separately.

| Hardware | Whole-engine speed gain | 95% interval | Status |
|---|---:|---:|---|
| Graviton3, c7g.xlarge | 1.760% | 1.663%–1.857% | 128 blocks; A/A accepted |
| Graviton4, c8g.xlarge | 1.651% | 1.563%–1.738% | 128 blocks; A/A accepted |
| Intel, c7i.xlarge | No complete estimate | — | A/A accepted; candidate stopped at 120/128 blocks |

ARM builds use Ubuntu GCC 13.3.0, `make -j2 profile-build ARCH=armv8-dotprod
COMP=gcc EXTRACXXFLAGS=-DNNUE_EMBEDDING_OFF`, with PGO/LTO and the same external
`nn-252f33942263.nnue` in both binaries. Each measurement uses twelve held-out
positions from a seeded 1,000-game Lichess broadcast sample, one thread, 16 MiB
hash and CPU 2. Sequential fresh processes use balanced ABBA/BAAB order, warmups,
and baseline-only calibrated depth/passes: depth 11 × 10 on Graviton3 and depth
11 × 14 on Graviton4. There is one boot per ARM CPU family, one network and one
compiler timing configuration. These results cover this workload and do not
establish Elo or general performance.

Each hardware preflight passed 22,584,752 integer comparisons, including 224,296
ASan/UBSan comparisons with recovery disabled. GCC/Clang, ordinary NEON on ARM,
and scalar on Intel were checked. Production bench signatures match 1,714,434
nodes. Completed ARM searches matched score, PV, best/ponder moves, nodes and
fingerprints exactly. Native Apple/Clang portability smoke checks also matched.

[The dated run record](AWS_VALIDATION_2026-10-03.md) gives corpus/network hashes,
coverage and attempt history. [Public timing summaries](submission-evidence/2026-10-03/)
include complete ARM A/A and candidate statistics and block log ratios, plus the
final Intel A/A result. They omit private corpus positions and operator access
information. Raw logs/checkpoints remain in private archives. All owned AWS
workers, SSH keys and security-group resources are cleaned up.

## Remaining work before ready for review

- [x] Fix the Actions formatting finding with clang-format 20.1.8; verify the
      formatting step passed, not just the overall job. Squash into one commit.
- [ ] Address any subsequent compiler/reviewer findings.
- [ ] Confirm ARM performance on broader position groups/depths and independent
      boots. The current twelve-position sample is a coverage limit; use new
      declared inputs and preserve these completed results.
- [ ] Measure Clang production search speed and ordinary NEON performance. Exact
      compiler/ISA checks passed, but timing currently covers GCC dot-product only.
- [ ] Review generated assembly and measure the lane/bank and GCC-pointer changes
      separately; split the patch if benefits or maintainers' scope guidance warrant it.
- [ ] Complete an unaffected x86 timing comparison with a sufficient fixed budget
      and whole-core isolation. Intel's partial checkpoint supplies no final estimate;
      its SMT sibling was not reserved. This checks regression, not the ARM benefit.
- [ ] Recheck master before marking ready; rerun affected checks if source or net changes.
- [ ] Resolve whether maintainers accept direct speed evidence or require ARM-capable
      Fishtest. No game test is claimed or submitted. Add test links if requested.

[Stockfish guidance](https://official-stockfish.github.io/docs/fishtest-wiki/Creating-my-first-test.html#speedups)
generally recommends Fishtest for speedups. Its direct-PR exception concerns gains
too small to verify there but independently verifiable, for example in assembly.
This draft presents the evidence and ARM coverage limits for that decision;
it does not assume an exemption. Exact-output tests validate identical computations;
equal-time games would test playing strength.

Additional networks, production peak-memory measurements and fresh private evaluator
slowdown/wrong-output controls would broaden qualification. The latter are necessary
before admitting new Shinka fitness; the standalone operator results do not do that.
Building the agent image and rerunning its provider canary concern future evolution,
and do not block submitting this saved Stockfish patch.

The [October 4 expanded validation](AWS_PR_VALIDATION_2026-10-04.md) is running
on fresh AWS Spot workers. Its fixed component/ISA/depth comparisons supersede
the remaining timing tasks only when their complete receipts are verified.
