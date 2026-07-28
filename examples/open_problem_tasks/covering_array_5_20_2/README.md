# Binary covering array `CA(N; 5, 20, 2)`

Minimize the rows `N` in a binary, 20-column, strength-5 covering array. The
external evaluator checks all `C(20,5) = 15,504` column subsets and all 32
patterns per subset. The [NIST definition](https://math.nist.gov/coveringarrays/coveringarray.html)
notes that optimal covering-array sizes are generally unknown; its historical
[IPOG-F table](https://math.nist.gov/coveringarrays/ipof/tables/table.5.2.html)
provides a 155-row construction for these parameters as a public calibration
point, not a claim about the current optimum.

The deterministic 400-row seed is self-contained and valid. It intentionally
leaves substantial room for evolved deletion, repair, or construction methods.
