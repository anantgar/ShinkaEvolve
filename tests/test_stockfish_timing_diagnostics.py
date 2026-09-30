"""Independent process variation must survive within-process averaging."""

import math

import pytest

from examples.stockfish_nnue.diagnose import summarize_blocks


def test_diagnostic_interval_uses_independent_blocks():
    weights = {"incremental": 0.6, "refresh": 0.25, "hot": 0.15}
    # Four internally noiseless process pairs have opposite persistent offsets.
    # Pooling their repeated requests as independent would hide this uncertainty.
    blocks = [
        {
            "measurement": {
                "geometric_speedup": math.exp(offset),
                "workload_speedups": {name: math.exp(offset) for name in weights},
                "log_standard_error": 0,
                "pair_count": 1000,
            }
        }
        for offset in [-0.01, 0.01, -0.01, 0.01]
    ]
    summary = summarize_blocks(blocks, weights)
    expected_se = 0.01 / math.sqrt(3)
    expected_half_width = 3.182446305284263 * expected_se  # two-sided t, df=3
    assert summary["fitness"] is None
    assert summary["block_count"] == 4
    for value in [summary["aggregate"], *summary["workloads"].values()]:
        assert value["geometric_speedup"] == pytest.approx(1)
        assert value["between_block_log_se"] == pytest.approx(expected_se)
        assert value["interval_95"] == pytest.approx(
            [math.exp(-expected_half_width), math.exp(expected_half_width)]
        )


def test_one_process_pair_cannot_estimate_between_process_uncertainty():
    with pytest.raises(ValueError, match="independent process blocks"):
        summarize_blocks([{}], {"incremental": 0.6, "refresh": 0.25, "hot": 0.15})
