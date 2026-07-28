# ShinkaEvolve circle packing

This isolated repo-mode task reproduces the 26-circle unit-square evolution
problem. The default is the paper's relaxed `1e-6` validation; pass
`--tolerance 0` for its separately reported exact condition. The evaluator
recomputes the objective and never trusts the candidate's reported sum.
