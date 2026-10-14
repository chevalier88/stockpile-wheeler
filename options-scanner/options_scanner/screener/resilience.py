"""Crash-resilience factors, computed purely from price history.

Every factor here answers "how did this stock move relative to a
benchmark", never "is this a good company" — there is no fundamentals
input (see the module docstring in `__init__.py` and
`options-scanner/CLAUDE.md` conventions: this is a measured exposure
profile, explicitly not a claim about surviving every crash. In a
real, broad enough crash, correlations move toward 1 regardless of
what this screen says.

Two benchmarks:

- **SPY** — broad market. General black-swan / drawdown exposure.
- **SMH** — semiconductors, the cleanest liquid single-ticker proxy
  for "AI narrative" risk available without building and maintaining
  an arbitrary custom basket of individual AI names.

Two named stress-test dates, chosen because each is a clean natural
experiment for a specific kind of shock:

- `DEEPSEEK_DAY` (2025-01-27) — the DeepSeek R1 release, which hit
  AI-capex-linked names (chips, power, infrastructure) hardest and
  the broad market only mildly. An AI-narrative-specific stress test.
- `TARIFF_SHOCK_DAYS` (2025-04-03/04) — the broad "Liberation Day"
  tariff selloff, a market-wide shock not specific to AI. A general
  black-swan stress test.

All factors are computed from the *same* aligned date range per
ticker so a short history (recent IPO) doesn't silently get compared
against a window of benchmark data it never actually traded through.
"""

from __future__ import annotations

from datetime import date

import numpy as np

DEEPSEEK_DAY = date(2025, 1, 27)
TARIFF_SHOCK_DAYS = (date(2025, 4, 3), date(2025, 4, 4))


def _align(returns: "pd.Series", benchmark_returns: "pd.Series"):
    """Inner-join two return series on their date index, as numpy arrays."""
    import pandas as pd

    df = pd.concat({"r": returns, "b": benchmark_returns}, axis=1).dropna()
    return df["r"].to_numpy(dtype=float), df["b"].to_numpy(dtype=float)


def beta(returns: "pd.Series", benchmark_returns: "pd.Series") -> float:
    """OLS slope of returns on benchmark_returns (closed-form — no
    scipy dependency). NaN if fewer than 20 overlapping observations
    or the benchmark has zero variance over the window."""
    r, b = _align(returns, benchmark_returns)
    if r.size < 20:
        return float("nan")
    var_b = np.var(b, ddof=1)
    if var_b <= 0:
        return float("nan")
    cov = np.cov(r, b, ddof=1)[0, 1]
    return float(cov / var_b)


def downside_beta(returns: "pd.Series", benchmark_returns: "pd.Series") -> float:
    """Beta restricted to days the benchmark fell — "how bad is my
    bad". More relevant to crash survival than full-sample beta, since
    a stock can have low overall beta while still falling hard in
    lockstep specifically on the benchmark's worst days."""
    import pandas as pd

    df = pd.concat({"r": returns, "b": benchmark_returns}, axis=1).dropna()
    down = df[df["b"] < 0]
    if len(down) < 10:
        return float("nan")
    return beta(down["r"], down["b"])


def event_excess_return(returns: "pd.Series", benchmark_returns: "pd.Series",
                         on: date) -> float:
    """Ticker's return minus the benchmark's return on a single
    calendar day. Positive means the ticker held up better than the
    benchmark that day; negative means it got hit harder. NaN if
    either series is missing that date (holiday, no trade, delisted
    by then, IPO'd after)."""
    import pandas as pd

    ts = pd.Timestamp(on)
    try:
        r = returns.loc[returns.index.normalize() == ts]
        b = benchmark_returns.loc[benchmark_returns.index.normalize() == ts]
    except (KeyError, TypeError):
        return float("nan")
    if r.empty or b.empty:
        return float("nan")
    return float(r.iloc[0] - b.iloc[0])


def event_excess_return_multi(returns: "pd.Series", benchmark_returns: "pd.Series",
                               on_days: tuple[date, ...]) -> float:
    """Sum of excess returns across a short multi-day event window
    (e.g. a two-day shock) — NaN components are treated as 0 so a
    partial-week holiday gap doesn't null out the whole window."""
    vals = [event_excess_return(returns, benchmark_returns, d) for d in on_days]
    vals = [v for v in vals if np.isfinite(v)]
    return float(sum(vals)) if vals else float("nan")


def max_drawdown_and_recovery(close: "pd.Series") -> tuple[float, bool]:
    """Max peak-to-trough drawdown (negative fraction, e.g. -0.35 for
    a 35% drawdown) over the given price history, and whether the
    most recent close has since reclaimed the prior running peak.

    Returns (nan, False) for fewer than 20 observations.
    """
    if close is None or len(close) < 20:
        return float("nan"), False
    running_peak = close.cummax()
    drawdown = (close / running_peak) - 1.0
    max_dd = float(drawdown.min())
    recovered = bool(close.iloc[-1] >= running_peak.iloc[-1])
    return max_dd, recovered


# ── Scoring ──────────────────────────────────────────────────────────────

# Weights are a single, documented place to tune — not buried in the
# formula below. They sum to 1.0; each factor is percentile-ranked
# within the scanned universe first (0-100), so the weights combine
# comparable units regardless of each factor's raw scale.
WEIGHTS = {
    "downside_beta_smh": 0.30,   # primary: AI-narrative crash exposure
    "downside_beta_spy": 0.20,   # general market crash exposure
    "deepseek_excess":   0.15,   # held up relative to SMH on the AI shock day
    "tariff_excess":     0.15,   # held up relative to SPY on the broad shock
    "max_drawdown":      0.10,   # smaller historical drawdown
    "realized_vol":      0.10,   # lower day-to-day vol
}


def _percentile_rank(values: "pd.Series") -> "pd.Series":
    """Rank ascending, 0-100, NaN-safe (NaNs stay NaN, excluded from
    the ranking denominator)."""
    return values.rank(pct=True, na_option="keep") * 100.0


def resilience_score(factors: "pd.DataFrame") -> "pd.Series":
    """Combine per-ticker resilience factors into a 0-100 score.

    `factors` must have columns: downside_beta_smh, downside_beta_spy,
    deepseek_excess, tariff_excess, max_drawdown, realized_vol — one
    row per ticker. Lower raw values are *better* for every factor
    (less downside beta, smaller/less-negative drawdown, lower vol,
    higher excess return already written so "more positive is better"
    holds for the excess-return columns too) — so each factor's
    percentile rank is inverted for the beta/drawdown/vol columns
    before weighting, and the excess-return columns use the rank as-is.

    A ticker missing a factor (e.g. too short a history for max
    drawdown) has that factor's weight redistributed proportionally
    across the factors it does have, rather than being penalized for
    missing data it was never positioned to provide.
    """
    import pandas as pd

    pct = pd.DataFrame(index=factors.index)
    # Lower-is-better factors: invert the rank (100 - rank) so a low
    # beta/drawdown/vol scores high.
    for col in ("downside_beta_smh", "downside_beta_spy", "max_drawdown",
                "realized_vol"):
        pct[col] = 100.0 - _percentile_rank(factors[col])
    # Higher-is-better factors: rank as-is.
    for col in ("deepseek_excess", "tariff_excess"):
        pct[col] = _percentile_rank(factors[col])

    weight_matrix = pd.DataFrame(
        {col: pct[col].notna() * WEIGHTS[col] for col in WEIGHTS}
    )
    weight_total = weight_matrix.sum(axis=1)
    weighted = (pct.fillna(0) * weight_matrix).sum(axis=1)
    score = weighted / weight_total.replace(0, np.nan)
    return score.round(1)
