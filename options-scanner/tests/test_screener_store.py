"""Tests for the screener sqlite cache, mirroring test_iv_history.py's
throwaway-DB-via-env-var pattern."""

from datetime import date

import pytest

from options_scanner.screener import store


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("OSC_SCREENER_DB", str(tmp_path / "s.db"))


def _resilience_row(ticker="AAPL", score=75.0):
    return {
        "ticker": ticker, "name": "Apple", "sector": "Technology",
        "index_membership": "S&P 500", "spot": 230.0,
        "downside_beta_smh": 0.8, "downside_beta_spy": 1.0,
        "deepseek_excess": 0.01, "tariff_excess": -0.01,
        "max_drawdown": -0.2, "drawdown_recovered": 1,
        "realized_vol": 0.25, "resilience_score": score,
    }


def _wheel_row(ticker="AAPL", score=60.0):
    return {
        "ticker": ticker, "strike": 220.0, "expiration": "2026-03-20",
        "dte": 35, "delta": -0.30, "ann_yield_pct": 12.5,
        "open_interest": 500, "spread_pct": 0.02, "iv_excess": 1.5,
        "wheelability_score": score, "has_tradeable_chain": 1,
    }


def test_write_and_read_resilience_round_trips():
    store.write_resilience_rows([_resilience_row()], as_of=date(2026, 1, 1))
    rows = store.latest_resilience()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["resilience_score"] == 75.0


def test_write_is_idempotent_per_day():
    store.write_resilience_rows([_resilience_row(score=10)], as_of=date(2026, 1, 1))
    store.write_resilience_rows([_resilience_row(score=20)], as_of=date(2026, 1, 1))
    rows = store.latest_resilience()
    assert len(rows) == 1
    assert rows[0]["resilience_score"] == 20.0  # replaced, not appended


def test_latest_resilience_picks_most_recent_day_per_ticker():
    store.write_resilience_rows([_resilience_row(score=10)], as_of=date(2026, 1, 1))
    store.write_resilience_rows([_resilience_row(score=20)], as_of=date(2026, 1, 2))
    rows = store.latest_resilience()
    assert len(rows) == 1
    assert rows[0]["resilience_score"] == 20.0


def test_resilience_and_wheelability_are_independent_tables():
    store.write_resilience_rows([_resilience_row()], as_of=date(2026, 1, 1))
    assert store.latest_wheelability() == []
    store.write_wheelability_rows([_wheel_row()], as_of=date(2026, 1, 1))
    assert len(store.latest_resilience()) == 1
    assert len(store.latest_wheelability()) == 1


def test_empty_rows_is_a_noop():
    store.write_resilience_rows([])
    store.write_wheelability_rows([])
    assert store.latest_resilience() == []
    assert store.latest_wheelability() == []


def test_last_scan_date_tracks_resilience_writes():
    assert store.last_scan_date() is None
    store.write_resilience_rows([_resilience_row()], as_of=date(2026, 2, 14))
    assert store.last_scan_date() == "2026-02-14"
