# Further task proposals awaiting approval

Research snapshot: 2026-07-16. No evaluator or seed code has been created for
anything on this page. These tasks are distinct from the publicly disclosed
AlphaEvolve and ShinkaEvolve tasks. AlphaEvolve did not publish its full list of
unsuccessful internal trials, so independence from that undisclosed list cannot
be proved.

## Recommended next

### 1. Minimum-size 17-input sorting network

Return a sequence of compare-exchange pairs that sorts all 17 inputs while
minimizing comparator count. A maintained construction list currently gives
the size interval `63..71`; depth 10 is already known to be optimal:
[current networks and bounds](https://bertdobbelaere.github.io/sorting_networks_extended.html),
[optimal-depth paper](https://arxiv.org/abs/1507.01428).

- **Evaluator:** apply the network to all `2^17 = 131,072` binary inputs using
  the zero-one principle; vectorization keeps this fast.
- **Fitness:** invalid-output count first, then comparator count, then depth.
- **Seed:** the public 71-comparator network or a self-contained Batcher network.
- **Why:** exact, deterministic, optimizer-friendly, and directly relevant to
  data-oblivious hardware/software.

### 2. Maximum cap set in `F_3^7`

Return the largest subset of the 2,187 points in `F_3^7` containing no three
distinct collinear points. Exact maxima are known through dimension 6, while
dimension 7 remains open; a 2022 analysis rules out size 289:
[dimension-7 paper](https://arxiv.org/abs/2206.09804).

- **Evaluator:** canonicalize ternary vectors and reject any distinct
  `x,y,z` with `x+y+z=0 mod 3`, using hash lookups in quadratic time.
- **Fitness:** maximize valid set size; line-violation count gives repair signal.
- **Seed:** lift/product a published dimension-6 cap, so evolution starts valid.
- **Why:** cheap exact certificates and a natural mix of algebraic construction
  and local search.

### 3. Constant-weight binary code `A(20,8,9)`

Return as many distinct 20-bit words of Hamming weight 9 as possible, with
pairwise Hamming distance at least 8. A historical reference table lists the
nontrivial interval `160..173`, but explicitly warns that it is no longer
updated, so this candidate needs a current-literature audit before approval:
[constant-weight code bounds](https://codes.se/bounds/cw.html).

- **Evaluator:** bit counts, weight checks, and pairwise XOR popcounts.
- **Fitness:** maximize valid codeword count; distance deficits provide dense
  feedback.
- **Seed:** a public 160-word code or a deterministic greedy constructor.
- **Why:** sub-millisecond verification, compact candidates, and a clear record
  gap. Before implementation, re-audit the table and freeze its source date.

## Existence moonshots

### 4. Costas array of order 32

Return a permutation of `0..31` whose pairwise displacement vectors are all
distinct. Order 32 is the smallest unresolved existence case and exhaustive
search has been estimated far beyond routine computation:
[structural-properties paper](https://www.sfu.ca/~jed/Papers/Jedwab%20Wodlinger.%20Costas%20Structural.%202014.pdf).

- **Evaluator:** permutation check plus exact duplicate-displacement counting.
- **Fitness:** minimize repeated displacement vectors; zero is a discovery.
- **Risk:** no valid seed is known, so this evaluates constraint-violation
  reduction unless the system resolves the existence question.

### 5. Hadamard matrix of order 668

Return a `668 x 668` `{+1,-1}` matrix with `H H^T = 668 I`. A 2025 paper still
describes 668 as the smallest open order and improves only the modular
orthogonality certificate from 32 to 64:
[64-modular construction](https://hal.science/hal-05393934).

- **Evaluator:** exact row norms and pairwise integer dot products.
- **Fitness:** number and magnitude of nonzero off-diagonal Gram entries.
- **Risk:** a full matrix is large and the landscape is extremely sparse. A
  structured generator repo would be preferable to 446,224 free signs.

### 6. Projective plane of order 12

Return the incidence structure of 157 points and 157 lines, with 13 points per
line and exactly one common line for each point pair. Order 12 is the smallest
unresolved finite-projective-plane order:
[current small-plane resource](https://ericmoorhouse.org/pub/planes/),
[recent finite-geometry reference](https://arxiv.org/abs/2510.19804).

- **Evaluator:** exact line sizes, point degrees, and pair-incidence counts.
- **Fitness:** total incidence conflicts, with zero as the existence certificate.
- **Risk:** no valid seed exists and nonexistence cannot be certified by search;
  use only as a long-horizon moonshot.

## Suggested approval

Implement the sorting network and cap-set tasks first. Add the constant-weight
code after its bounds are refreshed. Treat Costas-32, Hadamard-668, and the
order-12 projective plane as explicit moonshots rather than pass/fail headline
benchmarks.
