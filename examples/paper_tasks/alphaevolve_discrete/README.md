# AlphaEvolve analytic and discrete tasks

This is an index of six independent repo-mode tasks. No evolved program chooses
between objectives:

- `autocorrelation_c1_600`
- `autocorrelation_c2_50`
- `autocorrelation_c3_400`
- `uncertainty_hermite`
- `erdos_minimum_overlap_95`
- `sum_difference_set`

Each child ports the corresponding verifier from sections B.1-B.6 of the
[released notebook](https://github.com/google-deepmind/alphaevolve_results/blob/4226acbf237ff9ad10ba7673a2af127a2d8a5971/mathematical_results.ipynb).
The Hermite task uses the notebook's exact SymPy root/sign-change certificate.
The sums/differences evaluator retains explicit candidate-size and coordinate
caps as resource guards; those are engineering limits rather than paper
mathematics.
