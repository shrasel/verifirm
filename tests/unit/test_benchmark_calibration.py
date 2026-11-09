import pytest
from benchmarks.evaluate import run_benchmark


@pytest.mark.asyncio
async def test_golden_benchmark_and_calibration():
    summary = await run_benchmark()

    assert summary["total_cases"] == 8
    assert summary["decision_accuracy"] == 1.0
    assert summary["auto_match_precision"] == 1.0
    assert summary["auto_match_recall"] == 1.0
    assert summary["field_level_accuracy"] == 1.0
    # Well-calibrated probabilities should yield a low Brier score (< 0.05)
    assert summary["brier_calibration_score"] < 0.05
    for case in summary["cases"]:
        assert case["passed"] is True, f"Benchmark case failed: {case}"

