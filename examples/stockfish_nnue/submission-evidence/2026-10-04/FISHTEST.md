# Prepared Fishtest configuration

No test is submitted and no game result is claimed. The user has no Fishtest account available. Complete the pointer-only timing review before submission;
recheck master and update both revisions if upstream changes.

| Field | Prepared value |
|---|---|
| Repository | `https://github.com/anantgar/Stockfish` |
| Base | `49ea5ded38315cff8e67f4a677a9e7811612fbf6` |
| Test | `830c5c301ad1f5698f7aa3821a23c4156f0dfff1` |
| Base / test bench | `1714434` / `1714434` |
| First test | Standard STC, `10+0.1`, one thread, `Hash=16`, default current book |
| Stop rule | Standard STC SPRT `[0, 2]`; leave standard stopping behavior enabled |
| Subsequent test | Only after STC passes: standard LTC, `60+0.6`, `Hash=64`, SPRT `[0.5, 2.5]` |
| Relevant worker scope | Linux ARM NEON with actual GNU GCC; confirm compatible workers with moderators |

The patch is guarded to `USE_NEON && __GNUC__ && !__clang__`. A test on unaffected
x86 or Clang builds cannot measure this optimization's benefit. The current
[official test form](https://github.com/official-stockfish/fishtest/blob/master/server/fishtest/templates/tests_run.html.j2)
provides an architecture regex and a compiler pin; the
[supported values](https://github.com/official-stockfish/fishtest/blob/master/server/fishtest/util.py)
include `armv8`, `armv8-dotprod` and `g++`. A focused preparation is
`^armv8(-dotprod)?$` with compiler `g++`; verify the actual compiler and available
worker capacity before submitting. This scope does not qualify Apple silicon.
Use broader affected hardware if maintainers request it; do not substitute an
unapproved non-regression bound to make the test easier to pass.

The [speedup guidance](https://official-stockfish.github.io/docs/fishtest-wiki/Creating-my-first-test.html#speedups)
generally calls for Fishtest, with a narrow exception for benefits too small to
verify there but independently verifiable. Native assembly and scope-bound
operator timings support review; they do not supply an STC/LTC result or establish
that exception. A submitted test also needs moderator approval.
