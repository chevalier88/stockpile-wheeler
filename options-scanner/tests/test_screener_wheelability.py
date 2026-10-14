"""Tests for screener/wheelability.py against fabricated chain rows —
no network."""

import numpy as np
import pandas as pd
import pytest

from options_scanner.screener import wheelability


def _chain_row(strike, dte, delta, bid, ask, mid, otype="put"):
    return {
        "type": otype, "strike": strike, "expiration": "2026-03-20",
        "dte": dte, "delta": delta, "bid": bid, "ask": ask, "mid": mid,
        "ann_yield_pct": 10.0, "open_interest": 100, "iv_excess": 1.0,
    }


def test_pick_wheel_put_selects_nearest_target_delta():
    df = pd.DataFrame([
        _chain_row(90, 35, -0.10, 1.0, 1.2, 1.1),
        _chain_row(95, 35, -0.30, 1.5, 1.7, 1.6),   # exact target
        _chain_row(100, 35, -0.50, 2.0, 2.2, 2.1),
    ])
    row = wheelability.pick_wheel_put(df)
    assert row["strike"] == 95


def test_pick_wheel_put_respects_dte_window():
    df = pd.DataFrame([
        _chain_row(95, 10, -0.30, 1.5, 1.7, 1.6),   # outside window (too short)
        _chain_row(95, 60, -0.30, 1.5, 1.7, 1.6),   # outside window (too long)
    ])
    assert wheelability.pick_wheel_put(df) is None


def test_pick_wheel_put_ignores_calls():
    df = pd.DataFrame([_chain_row(95, 35, 0.30, 1.5, 1.7, 1.6, otype="call")])
    assert wheelability.pick_wheel_put(df) is None


def test_pick_wheel_put_empty_chain_returns_none():
    assert wheelability.pick_wheel_put(pd.DataFrame()) is None
    assert wheelability.pick_wheel_put(None) is None


def test_spread_pct_computes_fraction_of_mid():
    row = pd.Series({"bid": 1.0, "ask": 1.2, "mid": 1.1})
    assert wheelability.spread_pct(row) == pytest.approx(0.2 / 1.1)


def test_spread_pct_inf_when_mid_zero():
    row = pd.Series({"bid": 0.0, "ask": 0.0, "mid": 0.0})
    assert wheelability.spread_pct(row) == float("inf")


def test_score_candidates_ranks_better_yield_and_liquidity_higher():
    rows = [
        {"ticker": "GOOD", "strike": 95, "expiration": "x", "dte": 35,
         "delta": -0.3, "ann_yield_pct": 18.0, "open_interest": 1000,
         "spread_pct": 0.01, "iv_excess": 1.0, "has_tradeable_chain": True},
        {"ticker": "BAD", "strike": 95, "expiration": "x", "dte": 35,
         "delta": -0.3, "ann_yield_pct": 4.0, "open_interest": 5,
         "spread_pct": 0.5, "iv_excess": 1.0, "has_tradeable_chain": True},
    ]
    scored = {r["ticker"]: r for r in wheelability.score_candidates(rows)}
    assert scored["GOOD"]["wheelability_score"] > scored["BAD"]["wheelability_score"]


def test_score_candidates_no_chain_scores_zero():
    rows = [
        {"ticker": "LIQUID", "strike": 95, "expiration": "x", "dte": 35,
         "delta": -0.3, "ann_yield_pct": 10.0, "open_interest": 200,
         "spread_pct": 0.05, "iv_excess": 1.0, "has_tradeable_chain": True},
        {"ticker": "NONE", "strike": None, "expiration": None, "dte": None,
         "delta": None, "ann_yield_pct": None, "open_interest": None,
         "spread_pct": None, "iv_excess": None, "has_tradeable_chain": False},
    ]
    scored = {r["ticker"]: r for r in wheelability.score_candidates(rows)}
    assert scored["NONE"]["wheelability_score"] == 0.0
    assert scored["LIQUID"]["wheelability_score"] > 0.0


def test_score_candidates_empty_list_returns_empty():
    assert wheelability.score_candidates([]) == []
