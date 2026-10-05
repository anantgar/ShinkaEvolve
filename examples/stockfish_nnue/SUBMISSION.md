# Stockfish ARM NNUE submission

[Draft PR #7208](https://github.com/official-stockfish/Stockfish/pull/7208)
contains **GCC NEON weight-pointer materialization**: one commit
`830c5c301ad1f5698f7aa3821a23c4156f0dfff1`, plus AUTHORS and explanatory comments.
Lane-dot and accumulator-bank changes are removed after review questioned their
individual benefit and noted Apple silicon's benefit from accumulator splitting.
The PR body describes the narrowed scope and claims no qualified speed result.

Base: `49ea5ded38315cff8e67f4a677a9e7811612fbf6`, fetched again October 5 UTC.
Bench: **1,714,434 nodes**, matching baseline. clang-format 20.1.8 and
[full fork CI](https://github.com/anantgar/Stockfish/actions/runs/37245204905)
pass, including 59 compiler, sanitizer, Valgrind, platform, Android and universal
jobs. [The CI receipt](submission-evidence/2026-10-04/CI.json) pins the commit.
Shinka's Ruff, Mypy and non-secret CI suite also passes on the docs branch.

## Timing claims withdrawn; corrected validation running

The October 3 and first October 4 timing builds did not control Stockfish's build
metadata. Baseline compilation omitted `GIT_DIFFINDEX`; patched working trees
set it. This changes version code and binary layout even on x86, where the engine
edits are excluded. Accepted A/A controls alone did not detect this build mismatch.
**The earlier ARM speed claims are withdrawn.** Completed statistics and partial
attempts remain archived as observations, not isolated optimization effects.

The completed October 4 Intel deeper comparison measured −0.136%
(95% interval −0.210% to −0.061%) with an accepted A/A. The shallow control failed.
The generated binaries differ, so neither observation qualifies an assertion
that the source patch changes x86 speed. See the
[build-identity receipt](submission-evidence/2026-10-04/x86-assembly-identity.json).
All first October 4 workers and their owned key/security-group resources are
cleaned up; interrupted and deliberately stopped checkpoints are preserved.

[The corrected October 5 protocol](AWS_PR_VALIDATION_2026-10-05.md) repeats fixed
comparisons with equal `GIT_SHA`, `GIT_DATE` and empty `GIT_DIFFINDEX`, verified in
actual compile commands. Production PGO uses normal network embedding; its
external EvalFile is the same checksum-pinned network. A pre-timing x86 assembly
identity check must pass. The corpus, 128-block budgets and rejection rules are
unchanged. Pointer-only gets both depth groups as well as the main component
comparisons. Final conclusions await complete corrected receipts.

## Completed correctness and review work

The original full-corpus preflight passed 22,584,752 integer comparisons per
hardware family, including 224,296 ASan/UBSan comparisons with recovery disabled.
Those exact-output results remain valid; they do not isolate speed. The first
expanded run's pointer component passed 237,420 comparisons on each ARM family.
GCC/Clang, ordinary NEON, scalar and production bench checks also passed.

[Diagnostic assembly review](submission-evidence/2026-10-04/ASSEMBLY.md) records
more paired loads and fewer address additions in GCC's accumulator bodies.
Static code counts explain the proposed optimization; corrected builds and
whole-engine timing must establish its benefit.

## Work before ready for review

- [x] Fix the Actions formatting finding and verify the actual step.
- [x] Reduce the PR to one idea, one commit and AUTHORS; address current scope findings.
- [x] Complete the current commit's full compiler/platform CI and assembly review.
- [ ] Complete metadata-controlled broader workloads/depths and repeat boots.
- [ ] Complete corrected Clang and ordinary-NEON timing.
- [ ] Complete corrected component isolation and x86 regression checks.
- [ ] Recheck current master after validation; rerun affected checks if needed.
- [ ] Resolve the Fishtest requirement or direct-evidence exception with maintainers.

[Stockfish's speedup guidance](https://official-stockfish.github.io/docs/fishtest-wiki/Creating-my-first-test.html#speedups)
generally calls for Fishtest. The direct-PR exception concerns benefits too small
to verify there but otherwise verifiable, for example in assembly. No exemption,
STC/LTC result or Elo claim is assumed. [A prepared test configuration](submission-evidence/2026-10-04/FISHTEST.md)
pins the narrowed commit, base, benches and affected ARM/GCC worker scope. The
user has no Fishtest account; no game test is submitted. Account access and
compatible worker capacity are external prerequisites.

Additional networks and Apple/GCC performance would broaden coverage. Resource
and slowdown/wrong-output controls are necessary before admitting new Shinka
fitness; standalone operator measurements do not perform that admission.
