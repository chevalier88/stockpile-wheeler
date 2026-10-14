"""Tests for screener/resilience.py's factor math against hand-built
synthetic return series with known answers."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from options_scanner.screener import resilience


def _series(values, start="2025-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=idx)


def test_beta_recovers_known_slope():
    rng = np.random.default_rng(0)
    bench = rng.normal(0, 0.01, 200)
    # Construct returns with an exact 1.5x slope plus noise.
    noise = rng.normal(0, 0.0001, 200)
    r = 1.5 * bench + noise
    beta = resilience.beta(_series(r), _series(bench))
    assert beta == pytest.approx(1.5, abs=0.05)


def test_beta_nan_on_short_series():
    assert np.isnan(resilience.beta(_series([0.01] * 5), _series([0.01] * 5)))


def test_beta_nan_on_zero_variance_benchmark():
    r = [0.01, -0.02, 0.03] * 10
    b = [0.0] * 30
    assert np.isnan(resilience.beta(_series(r), _series(b)))


def test_downside_beta_ignores_up_days():
    rng = np.random.default_rng(1)
    bench = rng.normal(0, 0.01, 300)
    # Stock crashes 3x harder on down days, tracks 1:1 on up days.
    r = np.where(bench < 0, 3.0 * bench, 1.0 * bench)
    db = resilience.downside_beta(_series(r), _series(bench))
    full = resilience.beta(_series(r), _series(bench))
    assert db == pytest.approx(3.0, abs=0.1)
    assert db > full


def test_event_excess_return_positive_when_outperforming():
    idx = pd.date_range("2025-01-25", periods=5, freq="D")
    r = pd.Series([0.0, 0.0, -0.01, 0.0, 0.0], index=idx)   # ticker -1% on day 3
    b = pd.Series([0.0, 0.0, -0.05, 0.0, 0.0], index=idx)   # benchmark -5% same day
    excess = resilience.event_excess_return(r, b, date(2025, 1, 27))
    assert excess == pytest.approx(0.04)  # held up 4pp better


def test_event_excess_return_nan_when_date_missing():
    idx = pd.date_range("2025-01-25", periods=3, freq="D")
    r = pd.Series([0.0, 0.0, 0.0], index=idx)
    b = pd.Series([0.0, 0.0, 0.0], index=idx)
    assert np.isnan(resilience.event_excess_return(r, b, date(2030, 1, 1)))


def test_event_excess_return_multi_sums_window():
    idx = pd.date_range("2025-04-01", periods=5, freq="D")
    r = pd.Series([0.0, 0.0, -0.01, -0.02, 0.0], index=idx)
    b = pd.Series([0.0, 0.0, -0.03, -0.03, 0.0], index=idx)
    excess = resilience.event_excess_return_multi(
        r, b, (date(2025, 4, 3), date(2025, 4, 4)))
    assert excess == pytest.approx(0.02 + 0.01)


def test_max_drawdown_and_recovery_detects_trough_and_recovery():
    # Padded to clear the 20-observation floor; the relevant peak/trough/
    # recovery trio sits at the end.
    prices = _series([100.0] * 16 + [110, 60, 70, 115])
    max_dd, recovered = resilience.max_drawdown_and_recovery(prices)
    assert max_dd == pytest.approx((60 - 110) / 110)
    assert recovered is True


def test_max_drawdown_not_recovered_when_below_peak():
    prices = _series([100.0] * 16 + [110, 60, 90])
    _, recovered = resilience.max_drawdown_and_recovery(prices)
    assert recovered is False


def test_max_drawdown_nan_on_short_series():
    max_dd, recovered = resilience.max_drawdown_and_recovery(_series([100, 101]))
    assert np.isnan(max_dd)
    assert recovered is False


def test_resilience_score_ranks_lower_beta_higher():
    factors = pd.DataFrame({
        "downside_beta_smh": [0.5, 2.0],
        "downside_beta_spy": [0.5, 1.5],
        "deepseek_excess":   [0.02, -0.05],
        "tariff_excess":     [0.01, -0.03],
        "max_drawdown":      [-0.10, -0.40],
        "realized_vol":      [0.15, 0.45],
    }, index=["SAFE", "RISKY"])
    score = resilience.resilience_score(factors)
    assert score["SAFE"] > score["RISKY"]
    assert 0 <= score["SAFE"] <= 100
    assert 0 <= score["RISKY"] <= 100


def test_resilience_score_handles_missing_factor():
    factors = pd.DataFrame({
        "downside_beta_smh": [0.5, 2.0],
        "downside_beta_spy": [0.5, 1.5],
        "deepseek_excess":   [np.nan, np.nan],
        "tariff_excess":     [0.01, -0.03],
        "max_drawdown":      [-0.10, -0.40],
        "realized_vol":      [0.15, 0.45],
    }, index=["SAFE", "RISKY"])
    score = resilience.resilience_score(factors)
    assert not score.isna().any()
    assert score["SAFE"] > score["RISKY"]
