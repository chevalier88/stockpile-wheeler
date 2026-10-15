# Daily Put Credit Spread Recommender — Implementation Plan

**Status:** Draft · **Author:** plan via Claude Code · **Date:** 2026-10-16

A daily, read-only screen that surfaces bull put spread (put credit
spread) candidates across a configurable universe, filtered to a target
return-on-risk and DTE band, persisted so that what was recommended on
any given day can be reviewed later. **It recommends; it never places
an order.**

---

## Goal

Answer one question every morning: *"which put credit spreads are worth
looking at today?"* — given a target of roughly **15% return on risk**
in a **45–60 DTE** window, on SPY and any other liquid underlying that
qualifies.

The output is a ranked table plus a dated snapshot in a local store, so
that over weeks the user accumulates a paper track record of what the
screen said, independent of what they actually traded.

## Non-goals

- **No order placement, ever.** Not behind a confirm gate, not in paper
  mode. This module must not import `trade_actions`, `confirm_gate`, or
  any Schwab order path. See "Non-execution invariant" below.
- Not a backtester. It records forward from the day it's first run; it
  does not replay history.
- Not a position manager. Exits (the 50%-profit / 21-DTE rule) are the
  user's to apply — the screen may *display* those thresholds but does
  not track or alert on open positions. The **Positions** tab owns that.
- Not a new spread engine. `spreads.build_bull_put_spreads` already
  exists and is correct; this is a universe-loop, a filter, a scoring
  layer, and a store around it.

---

## What already exists (and must be reused, not duplicated)

Reading the codebase first, most of the hard part is done:

| Capability | Where | Notes |
|---|---|---|
| Put credit spread construction | `spreads.build_bull_put_spreads()` | Strikes, credit, max_profit/max_loss, breakeven, POP, Greeks, earnings flag |
| Multi-strategy orchestration + filters | `spreads.scan_spreads()` | `min_dte`, `max_dte`, `min_width`, `max_width`, `min_oi`, `min_pop`, `max_pop`, `max_abs_delta`, `hide_earnings`, `sort_by` |
| **Return on risk** | `spreads` → `risk_reward` column | `max_profit / max_loss` — this *is* the user's "return on risk" metric. A 15% target maps directly to `risk_reward >= 0.15`. No new math needed. |
| Chain fetch across providers | `chain.fetch_chain(ticker, opt_type, min_dte, max_dte, provider=...)` | Yahoo / Schwab / moomoo |
| Earnings dates | `earnings.fetch_earnings_dates(ticker)` | Feeds `earnings_dates` into the builders |
| IV richness | `iv_surface.compute_iv_excess` | Already surfaced as `short_iv_excess` on each spread row |
| Named universes | `watchlists.load()` / `save()` | Reuse rather than a new ticker-list format |
| **Daily-batch pipeline pattern** | `screener/pipeline.py` | Cheap prefilter over a wide universe → expensive per-ticker chain fetch over the survivors only |
| **Daily persistence pattern** | `screener/store.py` | stdlib sqlite under `options-scanner/cache/`, env-var override for tests, idempotent per-day upsert (delete-then-insert) |

The `screener/` package is the closest structural precedent and the new
module should mirror it deliberately — same two-stage shape, same store
conventions — so there is one pattern in the repo for "scheduled job
over a universe," not two.

---

## Five findings that shape the design

These came out of validating the strategy against live chains before
writing this plan. Each one changes a default or adds a column.

### 1. A strict 45–60 DTE window can return nothing

Standard monthly expiries are the third Friday. From **2026-10-16**:

| Expiry | DTE |
|---|---|
| 2026-11-20 | 35 |
| 2026-12-18 | **63** |
| 2027-01-15 | 91 |

**A 45–60 filter captures zero monthly expiries on this date.** The gap
between consecutive monthlies is ~28–31 days, so any window narrower
than that will be empty on a substantial fraction of days. SPY has
weeklies that fill the gap; most single names do not.

**Design response:** the DTE band is configurable with a **default of
40–70**, not 45–60, and the runner emits an explicit "no expiry in
window" diagnostic per ticker rather than silently returning an empty
table. A `--snap-to-nearest-monthly` option is worth considering as a
follow-up.

### 2. `risk_reward` flatters aggressive spreads — record `credit / width` too

`risk_reward` = `max_profit / max_loss` = C/(W−C). It is the correct
measure of return on capital actually committed, and it maps cleanly to
the user's target. But its denominator shrinks as the trade gets worse:
a $10-wide sold for $9 reports 900%, on a position nearly certain to
lose.

`credit / width` (C/W) has no such pathology, is bounded at 100%, and
has an exact interpretation:

> **C/W = 1 − (breakeven win rate)**

So a spread showing C/W of 15% needs an 85% win rate just to break even.
**Store both**, rank on C/W, and display the implied breakeven win rate
as its own column — it is the single most decision-relevant number on
the row and it is free to compute.

### 3. The existing `expected_value` column is risk-neutral and near-tautological

`spreads._finalise` computes:

```python
df["expected_value"] = df["pop"] * df["max_profit"] - (1 - df["pop"]) * df["max_loss"]
```

where `pop` comes from `prob_above()` → `N(d2)`, a **risk-neutral**
probability. Under risk-neutral pricing, expected value is approximately
zero by construction — that is what "fairly priced" means. Ranking
candidates by this column is close to ranking noise.

It also treats the payoff as **binary** (full win or full loss), ignoring
the partial-loss zone between the two strikes, where the position loses
some but not all of the max.

**Design response:** do not rank on `expected_value`. Leave the existing
column untouched for backward compatibility, and add a separate,
clearly-named real-world EV (below). Label the existing one in any new
UI as "EV (risk-neutral)" so its meaning is not overstated.

### 4. Most of a credit spread's expected value is leveraged equity beta, not option edge

Decomposing a representative SPY spread (sell 746 / buy 736, Dec 2026,
64 DTE) into its sources gave:

| Component | Value | Share |
|---|---|---|
| Equity risk premium (from net delta) | +$30.31 | **~70%** |
| Volatility risk premium (from net vega) | +$17.22 | — |
| Slippage (bid/ask crossing) | −$4.00 | — |
| **Net** | **+$43.53** | on $826 capital |

The position carried ~5 share-equivalents of delta — about **$3,840 of
stock exposure on $826 of capital, roughly 4.6× leverage**. The ERP
component is payment for carrying that, obtainable more simply by owning
the index.

**Design response:** surface **ERP, VRP and slippage as three separate
columns**, never one blended "edge" number. A recommender that blends
them will systematically rank leveraged beta above genuine option edge.

### 5. The option-specific edge peaks around 4–5% OTM, not at maximum return on risk

Sweeping every SPY strike, *total* EV rises monotonically as the short
strike approaches the money. But **VRP net of slippage — the part
attributable to selling options rather than to carrying beta — peaks
around 4% OTM (≈14–18 delta) and declines on both sides**:

| Short strike | %OTM | C/W | Total EV %cap | Alpha only |
|---|---|---|---|---|
| 710 | −7.8% | 7.5% | 3.16% | 1.63% |
| **738** | **−4.1%** | **14.5%** | 4.89% | **1.89%** ← peak |
| 746 | −3.1% | 17.4% | 5.27% | 1.60% |
| 752 | −2.3% | 20.3% | 5.85% | 1.56% |
| 765 | −0.6% | 28.5% | 6.98% | 0.89% |

**A recommender sorted purely on `risk_reward` will walk the user toward
the bottom of that table** — more total EV, less actual edge, more
market risk. This is the central ranking trap and the reason the default
sort is alpha-aware rather than ROC-descending.

Note the curve is choppy strike-to-strike because real quotes move in
discrete increments; treat the peak as a region, not a point.

### 6. Liquidity gating must be on relative spread, not just open interest

`min_oi` is necessary but insufficient. Comparing 20-delta puts at
matched economic risk:

| | %OTM for 20Δ | ROC | Bid-ask spread |
|---|---|---|---|
| SPY | −3.1% | 21.1% | **0.4%** |
| QQQ | −5.7% | 21.2% | 1.0% |
| IWM | −5.5% | 19.3% | 2.1% |
| **ARM** | **−22.3%** | **10.5%** | **11.5%** |

ARM carried 3,000–4,400 open interest at those strikes — it would pass a
naive `min_oi` filter — yet its ROC is *half* SPY's because ~40% of the
theoretical mid-to-mid credit is surrendered crossing the spread. High
implied volatility bought distance, not edge.

**Design response:** a hard `max_rel_spread` filter (default ~8% of mid,
per leg) applied *before* ranking, plus the computed slippage cost shown
on every row.

---

## Architecture

Mirrors `screener/` deliberately.

```
options-scanner/options_scanner/credit_spreads/
├── __init__.py
├── universe.py      # resolve a run's tickers: watchlist name, explicit
│                    # list, or a built-in "liquid ETFs" default set
├── candidates.py    # per ticker: fetch_chain → build_bull_put_spreads
│                    # → apply filters → DataFrame of survivors
├── scoring.py       # real-world EV decomposition + ranking key
├── pipeline.py      # orchestrate universe → candidates → score → store
└── store.py         # sqlite daily snapshots (mirrors screener/store.py)
```

Plus a CLI entry point at `options-scanner/run_credit_spreads.py`, and —
as a later increment, not in the first pass — a **Credit Spreads** tab.

### `scoring.py` — the one genuinely new piece of math

Everything else is plumbing. This module computes, per candidate row:

| Column | Formula | Meaning |
|---|---|---|
| `roc` | `max_profit / max_loss` | Return on risk (existing `risk_reward`, carried through) |
| `credit_over_width` | `net_credit / width` | Bounded comparison metric |
| `breakeven_win_rate` | `1 − credit_over_width` | Win rate needed to break even |
| `erp_dollars` | `net_delta × spot × ERP × T` | Equity-beta contribution |
| `vrp_dollars` | `−net_vega × (short_iv × vrp_haircut)` | Volatility-premium contribution |
| `slippage_dollars` | `(mid_credit − executable_credit) × 100` | Cost of crossing the spread |
| `alpha_dollars` | `vrp_dollars − slippage_dollars` | **Option-specific edge** |
| `ev_realworld` | `erp + vrp − slippage` | Total, for completeness |
| `notional_if_assigned` | `short_strike × 100` | Capital needed on assignment |

`net_delta` and `net_vega` already come off the spread row. `ERP` and
`vrp_haircut` are **configurable assumptions with conservative defaults
(4.5% and 10%)**, stated on every output. They are estimates drawn from
general ranges, not measured values — if either is wrong, every dollar
figure moves proportionally, and the CLI should print them in its header
so no one reads the numbers as observations.

**Default sort: `alpha_dollars` descending**, with `roc` available as an
alternate sort. This is the direct consequence of finding #5.

### `store.py` — schema

One table, mirroring `screener/store.py`'s connect/create/upsert shape
and its `OSC_*_DB` env override:

```sql
CREATE TABLE IF NOT EXISTS recommendations (
    as_of                TEXT NOT NULL,     -- run date
    ticker               TEXT NOT NULL,
    expiration           TEXT NOT NULL,
    short_strike         REAL NOT NULL,
    long_strike          REAL NOT NULL,
    dte                  INTEGER,
    spot                 REAL,
    net_credit           REAL,
    max_profit           REAL,
    max_loss             REAL,
    roc                  REAL,
    credit_over_width    REAL,
    breakeven_win_rate   REAL,
    pop                  REAL,
    net_delta            REAL,
    net_vega             REAL,
    erp_dollars          REAL,
    vrp_dollars          REAL,
    slippage_dollars     REAL,
    alpha_dollars        REAL,
    short_oi             INTEGER,
    rel_spread           REAL,
    earnings_in_window   INTEGER,
    notional_if_assigned REAL,
    rank                 INTEGER,
    PRIMARY KEY (as_of, ticker, expiration, short_strike, long_strike)
);
```

Per-day upsert so a re-run on the same day replaces that day's rows.
The primary key is the full spread identity, because a single ticker can
legitimately appear several times with different strikes.

### Defaults

Chosen from the findings above, all overridable:

| Setting | Default | Why |
|---|---|---|
| `min_dte` / `max_dte` | **40 / 70** | 45–60 is frequently empty (finding #1) |
| `min_roc` | **0.15** | The user's stated target |
| `max_rel_spread` | **0.08** | Finding #6 |
| `min_oi` | 200 | Necessary but not sufficient |
| `hide_earnings` | **True** | Earnings inside the window is the dominant confound in high-ROC names |
| `max_abs_delta` | 0.30 | Keeps the screen off the near-the-money end of finding #5 |
| `erp` | 0.045 | Stated assumption |
| `vrp_haircut` | 0.10 | Stated assumption |
| Universe | SPY, QQQ, IWM + user watchlist | Index ETFs are where the premium survives slippage |

### Annualized yield — suppress or relabel

`ann_yield_pct` is `roc × 365/dte × 100`, which assumes every cycle
wins. During analysis this produced figures like "7196% annualized."
Either omit it from this screen's output or label it explicitly as
**"max annualized if every cycle wins"** — never present it bare.

---

## CLI

```bash
# default: SPY/QQQ/IWM, 40-70 DTE, ROC >= 15%, no earnings in window
uv run options-scanner/run_credit_spreads.py

# a saved watchlist, looser ROC, wider DTE
uv run options-scanner/run_credit_spreads.py \
    --watchlist "liquid-etfs" --min-roc 0.12 --min-dte 30 --max-dte 90

# explicit tickers, sort by raw return on risk instead of alpha
uv run options-scanner/run_credit_spreads.py SPY QQQ --sort roc

# what did the screen say last Tuesday?
uv run options-scanner/run_credit_spreads.py --show 2026-10-13
```

Output is a ranked table plus a header stating the assumptions in force
(ERP, VRP haircut, data provider, as-of timestamp) and a one-line
reminder that nothing was or will be ordered.

---

## Non-execution invariant

This module sits in a codebase that *can* place orders. That makes the
boundary worth stating as a testable rule, not a convention:

1. `credit_spreads/` must not import `trade_actions`, `confirm_gate`,
   `schwab_auth`, or any module exposing an order path.
2. Chain data may be read from Schwab (read-only quotes) — reading the
   chain is not an order path. Reuse `chain.fetch_chain(provider="schwab")`.
3. **A test asserts rule 1 by walking the module's imports.** Mirrors the
   spirit of the existing `confirm_gate` invariants in
   `options-scanner/CLAUDE.md` — the rule is enforced, not just written
   down.
4. Any future hand-off to the Sell Put dialog must re-fetch a live chain
   rather than trusting a stored row, for the same reason
   `tabs/screener.py::_open_sell_dialog` does: a cached recommendation
   can be hours old and an order must price against the current market.

---

## Testing

Following the existing `tests/test_spreads.py` and
`tests/test_screener_store.py` patterns:

| Test | Asserts |
|---|---|
| `test_credit_spreads_filters` | ROC, rel-spread, OI and earnings filters each exclude what they should, on a synthetic chain |
| `test_credit_spreads_scoring` | ERP/VRP/slippage decomposition sums to `ev_realworld`; `breakeven_win_rate == 1 − credit_over_width` exactly |
| `test_credit_spreads_store` | Per-day upsert replaces rather than duplicates; env-var DB override respected |
| `test_credit_spreads_dte_gap` | A 45–60 window over a chain with only 35/63/91 DTE expiries returns empty **with a diagnostic**, not a silent empty frame |
| `test_credit_spreads_no_order_imports` | **The non-execution invariant** — import graph contains no order-placing module |

---

## Increments

Shippable in this order; each is useful alone.

1. **`candidates.py` + CLI over an explicit ticker list.** Reuses
   `build_bull_put_spreads` directly. Prints a ranked table. No store, no
   scoring beyond what `spreads.py` already returns. Immediately answers
   the daily question for SPY.
2. **`scoring.py`.** Adds the ERP/VRP/slippage columns and switches the
   default sort to `alpha_dollars`.
3. **`store.py` + `--show DATE`.** Daily snapshots and historical recall —
   this is what turns the tool into a paper track record.
4. **`universe.py` + watchlist integration**, and the two-stage pipeline
   if the universe grows past ~20 tickers.
5. **A Credit Spreads tab**, if the CLI proves it earns daily use.

## Open questions

- **Scheduling.** Nothing in this repo currently runs on a timer. Out of
  scope here; the CLI is cron/launchd-friendly and that is probably
  sufficient. Worth deciding before adding a tab.
- **Is `ann_yield_pct` worth keeping at all** on a screen whose whole
  point is honest expected value? Leaning toward omitting it.
- **Should the store record the spot price trajectory** after a
  recommendation, so the paper track record can be scored automatically?
  That edges toward backtesting (a stated non-goal) but is the natural
  next question once a few weeks of snapshots exist.
- **Weeklies.** Including them fixes the DTE-gap problem for SPY/QQQ/IWM
  but adds many near-duplicate rows. Probably a flag, default off.
