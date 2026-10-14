"""Is this ticker's option chain actually worth wheeling?

Reuses the scanner's existing chain-fetch + IV-surface pipeline
(`options_scanner.chain.fetch_chain`, `iv_surface.compute_iv_excess`)
rather than building a second one — the only new logic here is picking
the wheel's target contract (near 0.30 |delta|, 30-45 DTE puts) out of
an already-enriched chain and scoring its liquidity/yield.

Deliberately expensive relative to `resilience.py` (one option-chain
fetch per ticker, vs. one batched price-history call for the whole
universe) — see `pipeline.py` for why this only ever runs against the
resilience-filtered subset, never the full ~1000-ticker universe.
"""

from __future__ import annotations

TARGET_DELTA = 0.30
MIN_DTE = 30
MAX_DTE = 45

# Weights for the 0-100 wheelability score. Annualized yield is the
# wheel's actual income, weighted highest; liquidity (OI, spread)
# determines whether that yield is realistically collectable at all.
WEIGHTS = {
    "ann_yield_pct": 0.50,
    "open_interest": 0.30,
    "spread_pct":    0.20,  # lower is better — inverted before weighting
}


def pick_wheel_put(chain_df: "pd.DataFrame") -> "pd.Series | None":
    """From an enriched chain DataFrame (as returned by
    `chain.fetch_chain` + `iv_surface.compute_iv_excess`), return the
    put row nearest `TARGET_DELTA` within the DTE window, or None if no
    put in that chain falls in the window."""
    import numpy as np

    if chain_df is None or chain_df.empty:
        return None
    puts = chain_df[
        (chain_df["type"] == "put")
        & (chain_df["dte"] >= MIN_DTE)
        & (chain_df["dte"] <= MAX_DTE)
    ]
    if puts.empty:
        return None
    # Delta is negative for puts; compare against -TARGET_DELTA.
    idx = (puts["delta"] - (-TARGET_DELTA)).abs().idxmin()
    return puts.loc[idx]


def spread_pct(row: "pd.Series") -> float:
    """Bid-ask spread as a fraction of mid price. inf if mid is 0."""
    mid = float(row.get("mid", 0) or 0)
    if mid <= 0:
        return float("inf")
    return float(abs(row.get("ask", 0) - row.get("bid", 0))) / mid


def score_candidates(rows: list[dict]) -> list[dict]:
    """Attach a 0-100 `wheelability_score` to each row dict (as
    produced by `pipeline.py`'s wheelability stage), percentile-ranked
    within the batch being scored. Rows with `has_tradeable_chain =
    False` get score 0 rather than being dropped, so "no listed
    options" is visible in the UI instead of silently absent."""
    import pandas as pd

    if not rows:
        return rows
    df = pd.DataFrame(rows)
    tradeable = df["has_tradeable_chain"].astype(bool)

    pct = pd.DataFrame(index=df.index)
    pct["ann_yield_pct"] = df["ann_yield_pct"].rank(pct=True, na_option="keep") * 100.0
    pct["open_interest"] = df["open_interest"].rank(pct=True, na_option="keep") * 100.0
    pct["spread_pct"] = 100.0 - (df["spread_pct"].rank(pct=True, na_option="keep") * 100.0)

    weight_matrix = pd.DataFrame(
        {c: pct[c].notna() * WEIGHTS[c] for c in WEIGHTS}
    )
    weight_total = weight_matrix.sum(axis=1)
    weighted = (pct.fillna(0) * weight_matrix).sum(axis=1)
    score = weighted / weight_total.replace(0, float("nan"))
    score = score.where(tradeable, 0.0).round(1)

    df["wheelability_score"] = score
    return df.to_dict("records")
