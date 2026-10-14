"""Read-only probe for moomoo's unusual-options-activity / flow endpoints.

Purpose
-------
Before building an "Unusual Activity" tab on top of `get_option_event`,
`get_option_underlying_rank`, and `get_option_seller_screener`, this
checks which of them your moomoo account's market-data entitlement
actually unlocks — these are typically gated behind a subscription tier
above the basic US Level 2 quotes that `moomoo_chain.py` already uses,
and that's not something I can verify without your live account.

Run this once, read the output, and we'll build the tab against
whatever actually comes back — no UI work happens before this.

Prerequisites
-------------
1. moomoo OpenD gateway running and logged into your account
   (download: https://www.moomoo.com/download/openD).
2. `[moomoo]` host/port in `options-scanner/config.toml` if you're not
   using the default 127.0.0.1:11111 (see config.toml.example).

Usage
-----
    uv run options-scanner/probe_option_flow.py
    uv run options-scanner/probe_option_flow.py --tickers AAPL ALNY NVDA

Every call is read-only (no orders, no alert subscriptions created).

If OpenD isn't actually running/logged in, `OpenQuoteContext` blocks and
retries internally rather than failing fast — the script will just hang
with no output. Ctrl+C, confirm OpenD is up and logged in, and rerun.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "options_scanner"))


def _load_moomoo_cfg() -> dict:
    sys.path.insert(0, str(Path(__file__).parent))
    from options_scanner.config import get_moomoo_config, load_config
    return get_moomoo_config(load_config())


def _report(name: str, ret, data, ft) -> None:
    print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
    if ret != ft.RET_OK:
        print(f"FAILED — ret={ret!r}")
        print(f"Error detail: {data}")
        print("(If this mentions permissions/subscription/quota, your "
              "account's market-data tier doesn't include this endpoint.)")
        return
    try:
        import pandas as pd
        if isinstance(data, pd.DataFrame):
            print(f"OK — {len(data)} rows, columns: {list(data.columns)}")
            if not data.empty:
                print(data.head(5).to_string())
            return
        if isinstance(data, dict):
            df = data.get("event_list") if "event_list" in data else None
            if df is not None:
                print(f"OK — {len(df)} rows (all_count={data.get('all_count')}), "
                      f"columns: {list(df.columns)}")
                if not df.empty:
                    print(df.head(5).to_string())
                return
    except Exception as exc:  # noqa: BLE001 — diagnostic script, show everything
        print(f"OK but couldn't pretty-print: {exc}")
    print(f"OK — raw: {data!r}"[:1000])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", nargs="+", default=["AAPL", "ALNY", "NVDA"],
                        help="US tickers to test underlying-rank/seller-screener "
                             "filters against (default: AAPL ALNY NVDA)")
    args = parser.parse_args()

    import moomoo as ft

    cfg = _load_moomoo_cfg()
    print(f"Connecting to moomoo OpenD at {cfg['host']}:{cfg['port']} ...")
    ctx = ft.OpenQuoteContext(host=cfg["host"], port=cfg["port"])
    try:
        # ── 1. Unusual options activity (sweeps etc.), unfiltered ──────────
        ret, data = ctx.get_option_event(
            option_market=ft.OptionMarket.US_SECURITY, count=10,
        )
        _report("get_option_event (unfiltered, latest 10)", ret, data, ft)

        # ── 2. Same, filtered to large BUY sweeps (>$50k premium) ──────────
        # OptionOrderType has no named enum in this SDK build; raw values
        # per the proto: Normal=0, Sweep=1, Cross=2, Floor=4.
        filters = [
            ft.OptionEventFilter(ft.EventIndicatorType.ORDER_TYPE, value_list=[1]),
            ft.OptionEventFilter(ft.EventIndicatorType.TICKER_TYPE,
                                 value_list=[ft.EventTickerType.BUY]),
            ft.OptionEventFilter(ft.EventIndicatorType.TURNOVER,
                                 interval_min=50000.0),
        ]
        ret, data = ctx.get_option_event(
            option_market=ft.OptionMarket.US_SECURITY, count=10,
            filter_list=filters,
        )
        _report("get_option_event (BUY sweeps, premium > $50k)", ret, data, ft)

        # ── 3. Which underlyings have the most options action right now ───
        ret, data, next_page, all_count = ctx.get_option_underlying_rank(
            option_market=ft.OptionMarket.US_SECURITY,
            sort_type=ft.UnderlyingRankSortType.VOLUME,
            count=10,
        )
        _report("get_option_underlying_rank (top 10 by volume, market-wide)",
               ret, data, ft)

        # ── 4. Same, restricted to your own ticker list ────────────────────
        owner_filter = [ft.UnderlyingRankFilter(
            ft.UnderlyingRankIndicatorType.OWNER_LIST,
            security_list=[f"US.{t}" for t in args.tickers],
        )]
        ret, data, next_page, all_count = ctx.get_option_underlying_rank(
            option_market=ft.OptionMarket.US_SECURITY,
            sort_type=ft.UnderlyingRankSortType.IV_RANK,
            filter_list=owner_filter,
        )
        _report(f"get_option_underlying_rank (restricted to {args.tickers})",
               ret, data, ft)

        # ── 5. Wheel-native seller screener (CSP), restricted to your list ─
        seller_filter = [ft.SellerFilter(
            ft.SellerIndicatorType.OWNER_LIST,
            security_list=[f"US.{t}" for t in args.tickers],
        )]
        ret, data = ctx.get_option_seller_screener(
            market=ft.OptionMarket.US_SECURITY,
            seller_type=ft.SellerType.CASH_SECURED_PUT,
            sort_type=ft.SellerSortType.ANNUALIZED_RETURN,
            filter_list=seller_filter,
        )
        _report(f"get_option_seller_screener (CSP, restricted to {args.tickers})",
               ret, data, ft)
    finally:
        ctx.close()

    print("\nDone. Share this output and I'll design the Unusual Activity "
          "tab against whatever actually came back.")


if __name__ == "__main__":
    main()
