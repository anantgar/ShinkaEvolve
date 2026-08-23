# Secure n=41 circle-packing experiment

This task evolves a Python implementation that returns 41 circles packed in a
unit square. The evaluator runs outside the candidate snapshot and executes the
candidate through a networkless framed-stdio service. NumPy and SciPy are
available in the pinned secure runtime image.

The objective is the validated sum of radii. Invalid geometry receives zero;
valid candidates are ranked continuously by their recomputed sum.
