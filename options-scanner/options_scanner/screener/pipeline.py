"""Two-stage scan orchestration.

Stage 1 (cheap, whole universe): one batched price-history fetch
covers ~1000 tickers in a handful of HTTP requests, so computing
resilience factors for the whole universe is fast.

Stage 2 (expensive, filtered subset): an option-chain fetch is one
network round trip *per ticker* with no batch API, so it only runs
against the top-K tickers by resilience score — using the cheap stage
as a filter in front of the expensive one, not running both over the
full universe independently.

Both stages write to `store.py` as they go (not just at the end), so
an interrupted run (closed tab, network drop partway through stage 2)
still leaves whatever completed in the cache rather than losing the
whole pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

DEFAULT_WHEELABILITY_TOP_K = 150


@dataclass
class StageProgress:
    stage: str        # "resilience" | "wheelability"
    done: int
    total: int
    ticker: str


ProgressCallback = Callable[[StageProgress], None]


def run_resilience_stage(tickers_with_meta: list[dict],
                          on_progress: ProgressCallback | None = None) -> list[dict]:
    """Fetch batched price history for `tickers_with_meta` (each a dict
    with at least `ticker`, `name`, `sector`, `index`) plus SPY/SMH,
    compute resilience factors + score per ticker, write to the cache,
    and return the rows written.

    Tickers whose price history didn't come back (delisted, bad
    symbol, too-recent IPO for a full window) are skipped — not an
    error, just absent from the result.
    """
    import pandas as pd

    from options_scanner.screener import prices, resilience, store

    tickers = [m["ticker"] for m in tickers_with_meta]
    benchmarks = prices.fetch_benchmarks()
    if prices.SPY not in benchmarks or prices.SMH not in benchmarks:
        return []
    spy_ret = prices.daily_returns(benchmarks[prices.SPY])
    smh_ret = prices.daily_returns(benchmarks[prices.SMH])

    closes = prices.fetch_closes(tickers)

    factor_rows: dict[str, dict] = {}
    total = len(tickers_with_meta)
    for i, meta in enumerate(tickers_with_meta):
        t = meta["ticker"]
        close = closes.get(t)
        if on_progress:
            on_progress(StageProgress("resilience", i + 1, total, t))
        if close is None or close.empty:
            continue
        ret = prices.daily_returns(close)
        max_dd, recovered = resilience.max_drawdown_and_recovery(close)
        factor_rows[t] = {
            "ticker": t,
            "name": meta.get("name", ""),
            "sector": meta.get("sector", ""),
            "index_membership": meta.get("index", ""),
            "spot": float(close.iloc[-1]),
            "downside_beta_smh": resilience.downside_beta(ret, smh_ret),
            "downside_beta_spy": resilience.downside_beta(ret, spy_ret),
            "deepseek_excess": resilience.event_excess_return(
                ret, smh_ret, resilience.DEEPSEEK_DAY),
            "tariff_excess": resilience.event_excess_return_multi(
                ret, spy_ret, resilience.TARIFF_SHOCK_DAYS),
            "max_drawdown": max_dd,
            "drawdown_recovered": int(recovered),
            "realized_vol": _realized_vol_from_returns(ret),
        }

    if not factor_rows:
        return []

    factors_df = pd.DataFrame.from_dict(factor_rows, orient="index")
    factors_df["resilience_score"] = resilience.resilience_score(factors_df)
    rows = factors_df.to_dict("records")
    store.write_resilience_rows(rows)
    return rows


def _realized_vol_from_returns(ret: "pd.Series", window: int = 20) -> float:
    """Annualized realized vol from an already-computed return series
    (avoids a second history fetch — `stocks_shared.yahoo.realized_vol`
    fetches its own history, which we already have batched here)."""
    import numpy as np

    if len(ret) < window:
        return float("nan")
    return float(ret.tail(window).std() * np.sqrt(252))


def run_wheelability_stage(resilience_rows: list[dict],
                            top_k: int = DEFAULT_WHEELABILITY_TOP_K,
                            on_progress: ProgressCallback | None = None) -> list[dict]:
    """Run the option-chain pass against the top-K tickers by
    resilience score, score liquidity/yield, write to the cache, and
    return the rows written."""
    from options_scanner.screener import store, wheelability

    ranked = sorted(
        (r for r in resilience_rows if r.get("resilience_score") is not None),
        key=lambda r: r["resilience_score"], reverse=True,
    )[:top_k]

    rows = []
    total = len(ranked)
    for i, r in enumerate(ranked):
        t = r["ticker"]
        if on_progress:
            on_progress(StageProgress("wheelability", i + 1, total, t))
        row = _score_one_ticker(t)
        rows.append(row)

    scored = wheelability.score_candidates(rows)
    store.write_wheelability_rows(scored)
    return scored


def _score_one_ticker(ticker: str) -> dict:
    """Fetch and score one ticker's wheel put. Never raises — any
    fetch/parse failure degrades to a `has_tradeable_chain: False` row
    so one bad ticker doesn't abort the whole stage-2 pass."""
    from options_scanner.screener import wheelability

    base = {
        "ticker": ticker, "strike": None, "expiration": None, "dte": None,
        "delta": None, "ann_yield_pct": None, "open_interest": None,
        "spread_pct": None, "iv_excess": None, "has_tradeable_chain": False,
    }
    try:
        from options_scanner.chain import fetch_chain
        from options_scanner.iv_surface import compute_iv_excess

        df = fetch_chain(ticker, opt_type="puts",
                         min_dte=wheelability.MIN_DTE,
                         max_dte=wheelability.MAX_DTE)
        if df is None or df.empty:
            return base
        df = compute_iv_excess(df)
        row = wheelability.pick_wheel_put(df)
        if row is None:
            return base
        return {
            "ticker": ticker,
            "strike": float(row["strike"]),
            "expiration": str(row["expiration"]),
            "dte": int(row["dte"]),
            "delta": float(row["delta"]),
            "ann_yield_pct": float(row["ann_yield_pct"]),
            "open_interest": int(row["open_interest"]),
            "spread_pct": wheelability.spread_pct(row),
            "iv_excess": float(row.get("iv_excess", float("nan"))),
            "has_tradeable_chain": True,
        }
    except Exception:
        return base


def run(tickers_with_meta: list[dict],
        top_k: int = DEFAULT_WHEELABILITY_TOP_K,
        on_progress: ProgressCallback | None = None) -> tuple[list[dict], list[dict]]:
    """Run both stages in sequence. Returns (resilience_rows,
    wheelability_rows). Stage 2 is skipped (returns []) if stage 1
    produced nothing to filter on."""
    resilience_rows = run_resilience_stage(tickers_with_meta, on_progress)
    if not resilience_rows:
        return [], []
    wheel_rows = run_wheelability_stage(resilience_rows, top_k, on_progress)
    return resilience_rows, wheel_rows
