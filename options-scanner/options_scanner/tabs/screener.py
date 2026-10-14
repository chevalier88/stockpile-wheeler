"""Crash Candidates tab — stock selection for the manually-run wheel.

Screens the S&P 500 + S&P 400 universe on two independent axes:

- **Resilience** — how a stock has behaved relative to the broad
  market (SPY) and an AI/semiconductor proxy (SMH), on average and on
  two specific past stress days, from price history alone
  (`screener.resilience`).
- **Wheelability** — whether its options are actually liquid and
  well-paying near the wheel's usual ~0.30-delta / 30-45 DTE target
  (`screener.wheelability`), reusing the scanner's existing chain-fetch
  pipeline.

The two scores are shown as a scatter, not combined — the safest
stocks usually have the lowest IV and the worst premium, so the
trade-off is left to the user rather than hidden inside one number.

This tab only ever reads/writes the local sqlite cache
(`screener/store.py`) and Yahoo Finance; it places no orders itself.
Selling a candidate's put hands off to the same Sell Put dialog the
Watchlist tab uses, against a freshly fetched (not cached) chain.
"""

from __future__ import annotations

import streamlit as st

from options_scanner.display.leaderboard import (
    contract_from_row,
    open_investigate,
)
from options_scanner.screener import pipeline, store, wheelability
from options_scanner.ui_theme import badge, metric_card, section_header

_CAVEAT = (
    "This screens measured exposure to past AI-narrative and "
    "broad-market stress days — it is not crash immunity. In a real, "
    "broad enough crash, correlations move toward 1 regardless of what "
    "this score says. Treat it as a starting point for your own "
    "research, same as every other ranking in this app."
)


def _load_merged() -> "pd.DataFrame | None":
    import pandas as pd

    res = store.latest_resilience()
    if not res:
        return None
    res_df = pd.DataFrame(res)
    wheel = store.latest_wheelability()
    if wheel:
        wheel_df = pd.DataFrame(wheel).drop(columns=["as_of"], errors="ignore")
        merged = res_df.merge(wheel_df, on="ticker", how="left")
    else:
        merged = res_df
        merged["wheelability_score"] = float("nan")
        merged["has_tradeable_chain"] = False
    return merged


def _run_scan() -> None:
    from options_scanner.screener import universe

    progress = st.progress(0.0, text="Loading universe…")
    entries = universe.load_universe()
    if not entries:
        progress.empty()
        st.error("Couldn't load the S&P 500 / S&P 400 constituent list. "
                 "Try again in a moment.")
        return
    tickers_with_meta = [
        {"ticker": e.ticker, "name": e.name, "sector": e.sector, "index": e.index}
        for e in entries
    ]

    def _on_progress(p: pipeline.StageProgress) -> None:
        if p.stage == "resilience":
            frac = 0.05 + 0.45 * (p.done / max(p.total, 1))
            label = f"Resilience: {p.ticker} ({p.done}/{p.total})"
        else:
            frac = 0.50 + 0.50 * (p.done / max(p.total, 1))
            label = f"Wheelability: {p.ticker} ({p.done}/{p.total})"
        progress.progress(min(frac, 1.0), text=label)

    with st.spinner("Scanning — this can take a couple of minutes on a cold cache…"):
        pipeline.run(tickers_with_meta, on_progress=_on_progress)
    progress.empty()
    st.success("Scan complete.")


def _scatter(df: "pd.DataFrame") -> None:
    import altair as alt

    plot_df = df.dropna(subset=["resilience_score"]).copy()
    plot_df["wheelability_score"] = plot_df["wheelability_score"].fillna(0)
    chart = (
        alt.Chart(plot_df)
        .mark_circle(opacity=0.75)
        .encode(
            x=alt.X("resilience_score:Q", title="Resilience score",
                    scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("wheelability_score:Q", title="Wheelability score",
                    scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("sector:N", title="Sector"),
            size=alt.Size("open_interest:Q", title="Put OI", legend=None),
            tooltip=["ticker", "name", "sector", "resilience_score",
                     "wheelability_score", "ann_yield_pct", "open_interest",
                     "strike", "expiration"],
        )
        .properties(height=420)
        .interactive()
    )
    st.altair_chart(chart, use_container_width=True)


def tab_screener() -> None:
    section_header(
        title="Crash Candidates",
        subtitle=(
            "Stocks across the S&P 500 + S&P 400 screened for AI-crash / "
            "black-swan resilience, plotted against how well their "
            "options actually pay for wheeling."
        ),
        eyebrow="SCREENER",
    )
    st.caption(_CAVEAT)

    last_scan = store.last_scan_date()
    cols = st.columns([3, 1])
    with cols[0]:
        if last_scan:
            st.markdown(f"Last scan: **{last_scan}**")
        else:
            st.markdown("_No scan yet — build the universe scan below._")
    with cols[1]:
        if st.button("Build / refresh scan", key="screener_refresh",
                     use_container_width=True):
            _run_scan()
            st.rerun()

    merged = _load_merged()
    if merged is None:
        st.info("Click **Build / refresh scan** to run the first pass. "
                 "A cold scan fetches price history for roughly 1,000 "
                 "tickers plus option chains for the most resilient "
                 "subset, and can take a couple of minutes.")
        return

    with st.expander("Filters", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            min_resilience = st.slider("Min resilience score", 0, 100, 50,
                                       key="screener_min_res")
        with c2:
            min_wheelability = st.slider("Min wheelability score", 0, 100, 0,
                                         key="screener_min_wheel")
        with c3:
            max_price = st.number_input(
                "Max share price ($)", min_value=0.0, value=0.0, step=10.0,
                help="0 = no limit. Caps candidates at what your account "
                     "can hold 100 shares of if assigned.",
                key="screener_max_price")

    filtered = merged[merged["resilience_score"] >= min_resilience]
    if min_wheelability > 0:
        filtered = filtered[filtered["wheelability_score"].fillna(0) >= min_wheelability]
    if max_price > 0:
        filtered = filtered[filtered["spot"] <= max_price]

    m1, m2, m3 = st.columns(3)
    with m1:
        metric_card("Universe scanned", str(len(merged)))
    with m2:
        metric_card("Passing filters", str(len(filtered)))
    with m3:
        tradeable = int(filtered["has_tradeable_chain"].fillna(False).astype(bool).sum())
        metric_card("With a wheel-able chain", str(tradeable))

    if filtered.empty:
        st.warning("No candidates pass the current filters — widen them above.")
        return

    _scatter(filtered)

    display_cols = [
        "ticker", "name", "sector", "spot", "resilience_score",
        "wheelability_score", "ann_yield_pct", "open_interest",
        "spread_pct", "strike", "expiration", "dte",
    ]
    table = filtered[[c for c in display_cols if c in filtered.columns]].sort_values(
        ["resilience_score", "wheelability_score"], ascending=False)

    st.markdown("#### Candidates")
    event = st.dataframe(
        table, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row", key="screener_table",
    )
    sel_rows = event.selection.rows if event and event.selection else []
    if not sel_rows:
        return

    picked = table.iloc[sel_rows[0]]
    ticker = str(picked["ticker"])
    st.markdown(f"##### {ticker} — {picked.get('name', '')}")
    if not bool(picked.get("has_tradeable_chain", False)):
        badge("No tradeable chain in the cached scan", variant="warn")
        return

    if st.button(f"Find current {ticker} put to sell", key=f"screener_sell_{ticker}"):
        _open_sell_dialog(ticker)


def _open_sell_dialog(ticker: str) -> None:
    """Re-fetch a LIVE chain for `ticker` rather than trusting the
    cached scan's strike/price — the cache can be hours or days old,
    and an order must price against the market as it is now."""
    from options_scanner.fetch import fetch_and_enrich

    with st.spinner(f"Fetching current {ticker} chain…"):
        df, _earnings, err = fetch_and_enrich(
            ticker, "puts", wheelability.MIN_DTE, wheelability.MAX_DTE)
    if err:
        st.error(err)
        return
    row = wheelability.pick_wheel_put(df)
    if row is None:
        st.warning(f"No put near {wheelability.TARGET_DELTA:.0%} delta in the "
                   f"{wheelability.MIN_DTE}-{wheelability.MAX_DTE} DTE window "
                   f"right now.")
        return
    contract = contract_from_row(row, "put", ticker)
    open_investigate(
        contract, ticker_df=df, min_oi=0, top_n=10, min_vol=0,
        provider="yahoo", guard_key="screener_dialog_guard",
    )
