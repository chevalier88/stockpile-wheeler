"""Batched daily-close history for the screener's resilience pass.

`stocks_shared.yahoo.fetch_history` is one network round trip per
ticker — fine for a single-ticker tab, far too slow (and quick to get
Yahoo-throttled) for a ~1000-ticker universe scan. `yfinance.download`
batches many tickers into one HTTP request, so the whole universe plus
benchmarks fetches in a handful of calls instead of a thousand.

Chunking (default 150 tickers/request) keeps any one request small
enough that a single bad symbol or transient error doesn't blow away
an entire 1000-ticker batch — each chunk fails independently.
"""

from __future__ import annotations

from stocks_shared.yahoo import normalize_ticker

SPY = "SPY"   # broad-market benchmark
SMH = "SMH"   # AI / semiconductor proxy — liquid, single-ticker, no
              # arbitrary custom-basket weighting to defend or maintain

DEFAULT_PERIOD = "3y"
_CHUNK_SIZE = 150


def fetch_closes(tickers: list[str], period: str = DEFAULT_PERIOD,
                  chunk_size: int = _CHUNK_SIZE) -> dict[str, "pd.Series"]:
    """Return {ticker: daily-close pandas Series} for every ticker that
    returned usable data. Tickers yfinance couldn't resolve are simply
    absent from the result — callers filter on membership, not on an
    error list, since a 1000-ticker universe always has a few dead
    symbols (delisted, renamed, recent IPO).
    """
    import pandas as pd
    import yfinance as yf

    normalized = [normalize_ticker(t) for t in tickers]
    out: dict[str, pd.Series] = {}

    for i in range(0, len(normalized), chunk_size):
        chunk = normalized[i:i + chunk_size]
        try:
            data = yf.download(
                tickers=chunk, period=period, group_by="ticker",
                auto_adjust=True, threads=True, progress=False,
            )
        except Exception:
            continue
        if data is None or data.empty:
            continue

        if len(chunk) == 1:
            # yf.download flattens to a single-level column index when
            # only one ticker is requested — normalize to the same
            # {ticker: Series} shape as the multi-ticker case.
            if "Close" in data.columns:
                s = data["Close"].dropna()
                if not s.empty:
                    out[chunk[0]] = s
            continue

        for t in chunk:
            try:
                s = data[t]["Close"].dropna()
            except (KeyError, TypeError):
                continue
            if not s.empty:
                out[t] = s

    return out


def fetch_benchmarks(period: str = DEFAULT_PERIOD) -> dict[str, "pd.Series"]:
    """Fetch SPY + SMH closes in one call. Raises nothing — an empty
    dict signals the caller to skip the resilience pass entirely."""
    return fetch_closes([SPY, SMH], period=period, chunk_size=2)


def daily_returns(close: "pd.Series") -> "pd.Series":
    """Simple (not log) daily returns — used throughout resilience.py
    since beta and event-day comparisons work on simple returns, and
    simple returns are what a trader means by "the stock moved X%"."""
    return close.pct_change().dropna()
