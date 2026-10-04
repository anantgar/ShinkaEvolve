# Native GCC assembly review

These static counts come from the frozen Graviton3 GCC PGO/LTO executables,
before timing. Build receipts and full `objdump -d -C` output remain in the
private run archive. This review explains the generated code; timing must
establish whether the changes help the declared workload.

In `Network::evaluate`, lane-only replaces 80 vector SDOT instructions with
lane SDOT instructions and removes the input `ld1r`. Bank-only reduces the
function from 1,191 to 895 static instructions and vector adds from 24 to 8.
Lane+bank yields 887 instructions; the combined patch has the same propagation
counts. The lower dot count reflects loop/unroll structure, not fewer evaluated
network operations. See [propagation counts](propagation-assembly.json); its
generic `accumulator` field is a small wrapper, not the hot accumulator body.

The pointer-only compiler constraints change the hot accumulator bodies:

| Function | Instructions, base → pointer | Paired vector loads | Single vector loads |
|---|---:|---:|---:|
| `update_accumulator_refresh_cache` | 776 → 744 | 19 → 38 | 38 → 0 |
| `apply_combined` | 341 → 284 | 20 → 54 | 72 → 4 |
| `update_accumulator_hybrid` | 728 → 580 | 36 → 67 | 108 → 15 |

Refresh and combined bodies load the same total vector count. Hybrid's static
counts also reflect branch/unroll differences. Empty register constraints emit
no instructions themselves; GCC materializes the column base and uses more
paired loads with fewer address adds. The incremental wrappers are unchanged.
The combined patch's accumulator counts match pointer-only. See the
[function-level counts](accumulator-assembly.json).

These findings apply to this GCC build. They neither measure dynamic instruction
counts nor qualify another compiler, CPU or search workload.
