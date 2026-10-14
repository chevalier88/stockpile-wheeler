# CLAUDE.md — options-scanner

## Purpose

Scan an option chain and rank each option by IV excess — how far its
implied volatility sits above or below a fitted 2-D surface — to
surface IV-rich candidates for covered calls, cash-secured puts, and
roll setups (or IV-cheap candidates in buy mode).

The output is a **screening heuristic, not a mispricing or arbitrage
claim**. Vol smiles and skew are legitimate, the no-arbitrage
principle does not require the surface to be smooth, and IV+pp
deviations can reflect demand pressure, event risk, or stale prints
as easily as a tradeable signal. Phrase user-facing copy accordingly
— "mispriced", "overpriced", "underpriced", "anomaly" are out;
"IV-rich", "IV-cheap", "outlier", "stands above/below the surface"
are in. "Rich premium" / "cheap premium" are conventional trader
vernacular and remain fine.

## How it works

1. Fetch all expirations with DTE >= min_dte from Yahoo Finance
2. Annotate earnings events within each expiration window (elevated IV
   around earnings is expected, not a signal)
3. Fit an IV surface and score each option, via a three-stage
   **pluggable pipeline**:
   - **Filter** (`iv_filters.py`) — which options feed the regression.
     Defaults: OTM-only, spread ≤ 50% of mid, delta 0.10–0.95, and
     *short-dated* (≤ 60 DTE) earnings-spanning options excluded — long-
     dated contracts stay in the fit, since one earnings is a negligible
     share of their variance, and a guard keeps the filter from emptying
     the fit; an always-on sanity stage (IV noise floor/ceiling, DTE > 0)
     is prepended via `with_sanity`. Opt-in: min-OI and `fresh_quotes`
     (drop known-stale Yahoo quotes)
   - **Algorithm** (`iv_algorithms.py`) — `global_poly` (default),
     `per_expiration`, or `earnings_segmented`; produces `iv_fitted`
     (`svi` is registered as a seam but not implemented). All accept `weights`
     (`oi` / `inv_spread` / `vega`) and `robust` (`huber` / `tukey`
     IRLS so outliers can't drag the surface toward themselves).
     `global_poly` drops its m²·√T curvature term below 3 expirations
   - **Score** (`iv_scores.py`) — the ranking key `signal_score`;
     defaults to `raw_pp` (= IV excess), with z-score, relative,
     execution-cost composite, VRP, and historical-percentile options
4. Compute IV excess = actual IV − fitted IV (positive = IV-rich,
   sits above the fitted surface)
5. Display ranked table including delta, annualized yield, and OI

The Single Ticker tab exposes a **Global / Per-expiry** preset toggle
(the "Fit:" radio) plus an **Advanced surface fit** expander to mix the
three stages; the CLI mirrors this via `--preset {current,v2}` /
`--algorithm` / `--fit-weights` / `--robust` / `--score` (the CLI
preset names differ from the UI labels). The
`percentile` score persists scans to a gitignored SQLite store
(`options-scanner/cache/`) and is blank until history accumulates.

## Crash Candidates tab — stock screening, not order automation

`options_scanner/screener/` + `tabs/screener.py`. Screens the S&P 500 +
S&P 400 (~900 tickers) for the manually-run wheel strategy (sell CSPs,
take assignment, sell CCs, repeat — this tab finds candidates for that;
it places no orders of its own beyond the existing Sell Put hand-off).

Two **independently scored**, never-combined axes, because the safest
stocks and the best-paying options pull in opposite directions:

- **Resilience** (`screener/resilience.py`) — pure price-history math,
  no fundamentals. Downside beta (beta computed only on the
  benchmark's down days — the relevant "how bad is my bad" measure)
  against **SMH** (a single liquid semiconductor ETF standing in for
  "AI narrative," chosen over a hand-weighted custom basket of AI
  names) and against **SPY** (broad market), plus excess return on two
  named past stress days — `DEEPSEEK_DAY` (2025-01-27, AI-specific) and
  `TARIFF_SHOCK_DAYS` (2025-04-03/04, broad market) — plus max drawdown/
  recovery and realized vol. Deliberately does NOT trust GICS sector
  labels: "Utilities" now includes AI-datacenter power names (VST, CEG,
  NRG) that behave like semis, which is exactly the case this
  empirical measure is built to catch instead of a sector filter.
- **Wheelability** (`screener/wheelability.py`) — reuses
  `chain.fetch_chain` + `iv_surface.compute_iv_excess` (the same
  pipeline every other tab uses) to find each candidate's ~0.30-delta
  put in the 30-45 DTE window and scores it on annualized yield, open
  interest, and bid-ask spread. A high-IV name with no real open
  interest (one contract, a huge quoted yield nobody could actually
  collect) scores low here even though resilience.py never sees IV at
  all — the two modules don't share inputs on purpose.

**Two-stage pipeline** (`screener/pipeline.py`): batched price history
(`screener/prices.py`, one `yfinance.download()` call per ~150-ticker
chunk) scores resilience for the *whole* universe cheaply, then only
the top-K (default 150) by resilience score go through the expensive
per-ticker option-chain fetch for wheelability. Running wheelability
against all ~900 tickers would be ~900 individual chain fetches and
get rate-limited; this reuses the cheap stage as a filter in front of
the expensive one.

Results cache to `options-scanner/cache/screener.db` (sqlite, same
connect/create-table/upsert shape as `iv_history.py`) so the tab reads
instantly after the first build. The universe ticker list itself caches
separately to `options-scanner/cache/universe.json` (30-day staleness
window) — `screener/universe.py` fetches it from Wikipedia's S&P 500 /
S&P 400 constituent tables via `requests` + `pandas.read_html`,
**with a browser User-Agent header** — Wikipedia 403s the bare
urllib/pandas default.

Selling a candidate's put does NOT reuse the cached strike/price —
`tabs/screener.py::_open_sell_dialog` re-fetches a live chain first,
since the cache can be hours old and an order must price against the
market as it is now. Same reasoning as `leg_iv_pp` needing a live
chain fetch elsewhere in this file.

This screens *measured exposure*, not crash immunity — correlations
move toward 1 in a real broad-enough crash regardless of what the
score says. The tab says so in its own caption, same as the GEX tab's
"assumed, not measured" caveat.

## Running the tool

Always run from the **repo root** using `uv run`:

```bash
# Both calls and puts (default)
uv run options-scanner/run_scanner.py AAPL

# Covered call selection only
uv run options-scanner/run_scanner.py AAPL --calls

# Cash-secured put selection only
uv run options-scanner/run_scanner.py AAPL --puts

# Roll an existing short call
uv run options-scanner/run_scanner.py AAPL --roll \
    --type call --strike 185 --expiration 2026-01-16

# Adjust filters
uv run options-scanner/run_scanner.py AAPL --calls \
    --min-dte 400 --min-oi 50 --top 20
```

Never use `python` directly — dependencies won't be resolved.
Run `uv sync` from repo root after any `pyproject.toml` change.

## Order entry: the Confirm → Place gate

Every screen that can send an order (Sell Put/Call dialog, tracked-trade
close, live-position close, roll, unwind) goes through `confirm_gate.py`.
The invariants, which any new order screen must keep:

- **Place** renders only when Confirm was pressed *on the values now on
  screen* and those values still validate.
- Editing the limit or the contract count **disarms** — back to Confirm.
  The confirm step attests to specific numbers, not to a general
  intention to trade. Switching a selected position between **Close**
  **Roll** and **Unwind** on the Positions tab disarms for the same
  reason: those are different orders, so an arm made against one must
  not survive into the other.
- Confirm is disabled while armed; **Cancel** is the way back. The two
  buttons are never both live.
- Confirm stays **clickable while the inputs are invalid** — it just
  refuses to arm, and the error stays on screen. Never disable it for a
  bad value: Streamlit commits a number box only on blur, so a disabled
  button forces "click away, wait for it to re-enable, then click
  Confirm". Pass `validate=` to `confirm_gate.arm()` instead; the
  callback sees the values as of the click. Only blocks the user can't
  fix by editing (paper mode, market hours) disable the button.
- An emptied number box returns `None` from `st.number_input` — check
  `confirm_gate.valid_values()` before casting, or `int(None)` raises.
- **Never put `min_value`/`max_value` on an order-entry number input.**
  Streamlit refuses to *commit* an out-of-range entry: it shows its own
  "must be ≤ N" message and keeps serving the last valid value. Typing 5
  contracts against a 4-contract cover therefore left the app holding 1
  — valid, so the order built and Place armed for a size nobody typed.
  Leave the widget unbounded and validate in our code
  (`build_option_sell_order`, `build_roll_order`,
  `close_input_error`), which sees the real number and explains the
  rejection. Keep the cap in the label ("Contracts (Max 4)") as
  guidance. Scan-filter inputs may keep their bounds — they gate
  nothing.

Arm with the `on_click` callbacks (`arm` / `disarm`), never an inline
`if st.button(...)`: a callback runs before the rerun renders, so the
button states are consistent within a single frame.

**Place must be drawn with `confirm_gate.place_button()`** — a context
manager, never a bare `st.button`. Sending an order is a couple of Schwab
round trips, seconds long, and the bare button went on looking unpressed
for every one of them; it got pressed a second time, and the two runs
racing each other surfaced as a bogus "Couldn't resolve the target
account" on an order that had gone out. `place_button` draws into an
`st.empty()` slot and, on the click, immediately overwrites itself with a
disabled **⏳ Sending order…** — enqueued before the caller's submit
blocks, so the frontend shows a dead button for the whole trip. Wrap the
submit in `st.spinner(confirm_gate.SENDING_SPINNER)` to say the same
thing a second way. Two properties come with it:

- It **disarms on the click**, ahead of the caller's own disarm. Any
  element call after the submit (the spinner closing, an error) can raise
  the rerun exception and end the run early, and the next run must not
  find an armed Place with a queued second click waiting to fire it.
- A body that returns without rerunning means the order **failed**, so
  the slot goes back to the original label, still disabled. Retrying is
  Cancel → Confirm → Place on purpose: "rejected" and "sent, then the
  connection dropped" are indistinguishable from here, and re-attesting
  the numbers is the cheap way to be sure. Leave **Cancel** enabled —
  it's the only way out of that state.

Also: don't write a live default straight into a keyed input on every
rerun. Use `confirm_gate.reseed_on_change()`, which re-seeds only when
the *basis* changes — an unconditional write clobbers the number the
user is mid-correction, so the error describing it never renders.

**After placing**, every Place path must:

1. disarm the gate (`place_button` already did it on the click; the
   explicit write stays because it's the step a reader looks for),
2. on success, queue the center banner (`st.session_state["_osc_toast"]`)
   **and** drop the stored result so it can't also render inline,
3. `st.rerun()` — a **full** rerun, not `scope="fragment"`: `run_app`
   renders the banner.

The rerun is not optional. Disarming only takes effect on the *next*
run, so without it the panel the click came from stays on screen with
Place still live (which is exactly what the paper close used to do).
Dialogs are the one exception: `st.rerun()` closes a dialog, so the Sell
and Roll dialogs rerun on success only and keep a failure visible inline.

## The tab bar's blue tint

`ui_theme.mark_broker_tabs(TAB_NAMES, BROKER_TABS)` fills the chips for
tabs that read a live broker account — **Positions** and **Trades** — in
Schwab blue, so the dependency is visible before you click into an empty
table. Add a tab that needs Schwab and it belongs in `BROKER_TABS`
(`run_app.py`); indices are derived from `TAB_NAMES` at call time, so
reordering can't shift the fill onto a neighbour (the pair has already
been swapped once). The colors are the four constants above the
function.

**The selected chip is opaque, and that's load-bearing.** Streamlit
draws the selected label in white — it normally sits on a filled dark
background — and an `!important` background override leaves that white
text in place. A *translucent* fill then resolves against whatever
canvas is behind it: `rgba(0,160,223,0.62)` landed on `#5EC2EA` under
the light theme, putting white text at **2.0:1** (unreadable), while
the same value landed on `#056B9A` under the dark theme at a fine
5.8:1. `_BROKER_TAB_ACTIVE_BG` is therefore a solid `#0077A8` with an
explicit `_BROKER_TAB_ACTIVE_FG` — one opaque pair renders identically
on both themes and clears 4.5:1. A test pins that ratio. The *idle*
chip stays translucent on purpose: it keeps the theme's own label
color, which adapts with the canvas.

Set the label color on the child elements too (`button p`, `button
span`), not just the button — Streamlit wraps the label, so a color on
the button alone never reaches the text. Same trap as the roll dialog's
red Cancel button.

**Watch the selectors.** The first version scoped to
`[data-testid="stSegmentedControl"]` — a test id Streamlit doesn't
define — and silently matched nothing through two rounds of "why is
there no tint". The real ids are `stButtonGroup` for the row and
`stBaseButton-segmented_control` / `…_controlActive` for the chips.
Scoping now runs through `st-key-osc_tabbar`, which comes from our own
`st.container(key=...)` and can't be renamed by a version bump. A
Markdown `:blue-background[…]` label via `format_func` also works and is
a firmer contract, but it tints only the text run, not the chip.

Note that **several existing rules still target that dead test id** —
the tab-bar underline styling in `inject_theme` and the data-source
pill's border in `styles.css`. They have no effect today; see the
`stButtonGroup` note above before rewriting them, and expect the tab bar
to change appearance when they start applying.

Live Charts is deliberately **not** tinted: its panes take Yahoo and
Hyperliquid too, so Schwab is one option there rather than a
requirement. Trades is tinted despite still working without a broker
(it lists locally-tracked trades and closes paper ones) because
everything broker-side on it — cost-to-close, P/L, order status, closing
a live position — is Schwab-gated.

## Stock coverage: the Positions tab's second table

`trade_actions.classify_coverage(shares, short_calls)` is pure arithmetic
on two numbers (so it tests without a broker) and owns the four states:
`over_written`, `uncovered`, `partial`, `covered`. `equity_positions`
pairs shares with the calls written on them from **one** positions fetch
— two reads of a moving account could disagree about what's covered.

Two rules worth keeping straight:

- **"Covered" means no writable lot remains**, not that every share is
  written. 450 shares against 4 calls leaves a 50-share stub, and there
  is nothing you can do with 50 shares.
- **`over_written` exists because `calls_coverable` clamps at 0.** With
  150 shares and 2 calls it returns 0, identical to fully covered — so
  a naked call would render as safe. Never collapse the two.

Row shades follow `MONEYNESS_BANDS`' rules: translucent rgba (they
overlay the cell in both themes) and **no green**, since these tables
color P/L green and a green row would read as "profitable".

The stocks table calls `filter_hidden` but must **never** call
`render_hidden_notice` — that keys its checkbox on the scope alone, so a
second call on the same tab is a duplicate-key crash. The option table
renders it; the toggle is session-wide and governs both.

**Only rows with a free 100-lot are selectable**, and that means two
tables: Streamlit's dataframe selection is all-or-nothing per table, so
a checkbox on some rows and not others can only be done by splitting
them (`leaderboard._render_calls_by_coverage` solves the same problem
the same way). The two halves share one column config and one styler so
they can't drift into looking like different data, and they need
distinct widget keys. The selection indexes into the **writable subset**
— indexing the full row list would build a call for whichever position
sat at the same index.

**A selection outlives the list it was made on.** Streamlit hands back a
row *index*, and it survives a rerun that re-populated the table — so
selling a position while its row was selected pointed that index past
the end of the shortened list and took the tab down with `IndexError:
list index out of range`. The quieter version of the same bug is worse:
when the list is still long enough, the index lands on whichever
position slid into the slot and opens an order screen on something
nobody picked.

Both selectable tables on this tab therefore fold
`leaderboard.fingerprint_ids(...)` into their widget key — the stock
table on its tickers, the option tables on `_leg_id` — so the widget is
rebuilt, and the selection cleared, exactly when a row index would start
meaning something else. It's the same guard `rows_fingerprint` gives the
watchlist board, and any new selectable table over live positions needs
one of the two. Fingerprint **identity only**: fold in a share count or
a quantity and every quote refresh throws away a selection mid-decision.
The bounds check behind it is belt-and-braces — an order screen must not
answer a stale index with a traceback.

Selecting a row with a free lot opens the covered-call builder, which
hands off through the leaderboard's own `contract_from_row` and
`open_investigate`. Build the contract dict by hand and it will drift
from what the dialog expects (its `:,d` formats need real ints, and
`open_investigate` also clears stale confirm/result state) — the point
of sharing them is that a call sold from Positions is identical to one
sold from a watchlist scan.

## Leg snapshots: one contract, one description

`format.leg_rows` + `format.kv_table_html` render the leg table on all
three Positions-tab actions (close, roll, unwind) and are the reason a
contract can't read three different ways depending on the verb you
picked. They live in `format.py` — a leaf module — so `tabs/trades`
(close) and `tabs/rolls` (roll, unwind) can both import them; the
trades → rolls direction only exists as a lazy import for the roll
monitor, and adding a module-level one would cycle.

The leg tables pass `pairs=2`, which lays two label/value pairs per line
and **fills column-major** — first half of the rows down the left pair,
the rest down the right. That halves the height (a 10-row leg is 5
lines) and, because `leg_rows` emits prices before analytics, lands
Spot/Bid/Ask/Mid/Last in one column and IV%/Delta/OI/Vol/IV+pp in the
other. Row-major would interleave the two. `pairs` defaults to 1, which
is what the Sell dialog's terms/prices tables still use.

Everything on a leg rides along on the re-quote except **IV+pp**, which
isn't a quote field: it's the leg's IV against a fitted surface, so it
needs a chain fetch (`trades.leg_iv_pp`, ~2s measured, `fetch_and_enrich`
caches 5 min; the fit itself is ~4ms). It's fetched automatically on all
three actions — they only render once a row is selected, so nothing is
paid on tab load. `leg_iv_pp` never raises: IV+pp is context, not a
precondition for an order, so a throttled chain drops that one row and
the panel still prices and places.

The color scale is a **buyback** reading — below the surface is green
(cheap to close), above it red — the opposite of the sell-side scale,
because every screen sharing these rows is buying a leg back.

## Rows that aren't live positions (Trades tab)

`trades.row_alert(store_status, disp_status, exp_age)` is the single
answer to "is there actually a position behind this row?", and the
heading count, the row styling and the row's own stamp all read it —
three places that must not disagree.

Two ways a row stops being a position, and they look identical on screen
if nothing says otherwise:

- **The opening order died.** The everyday one: a sell-to-open placed in
  the afternoon doesn't fill, and at 16:00 ET the broker retires it. Also
  rejected, and canceled broker-side. Nothing was ever bought or sold,
  but the store still says `open` — its lifecycle is driven by *our*
  orders (placed, filled, closed), and none of these endings sends one.
  The row showed a murmured lowercase **expired** among real positions.
- **The contract expired** under a position that did fill
  (`past_expiry_days`) — the same shape, rarer.

Three rules worth keeping straight:

- **The dead-order words only count from the broker.** They arrive
  through `_display_status`, which hands back the broker's word *only*
  while the store says `open`. A record the store itself calls `expired`
  or `canceled` is settled history and must not be stamped — otherwise
  the whole archive lights up, and a cancel you made yourself (which
  writes `status="canceled"`) shouts at you.
- **Expiring today is not an expired contract.** It trades until the
  close and the row is still actionable; flagging it would cry wolf every
  expiration Friday. `working` is likewise never flagged — it may yet
  fill.
- **A dead order outranks an expired contract** when both apply. "You
  never had this position" is the more important of the two.

It stays **display-only** — no record is rewritten. Whether a contract
finished worthless or was assigned is a fact at the broker, and the tab
has no read that distinguishes them; guessing would book a realized P/L
nobody confirmed. The row says what it knows and points at the broker.

**Where it's computed matters.** The dead-order half needs the broker's
order status, so `_alerts` is resolved *between* the status prefetch and
the render loop, and the heading is held in an `st.empty()` until then —
its count comes from that read. The loop reads `_disp_by_id` rather than
recomputing the status word, which is what keeps the four surfaces
honest.

**How loud it has to be — and no louder.** Two attempts came back as
"still doesn't stand out": a 10%-alpha amber tint, then a small
`:red-background[…]` pill. It now reads **⚠️ ORDER EXPIRED — NEVER
FILLED** in red bold caps on a solid light-gray row with a red left
edge. Don't quietly walk that back toward tasteful.

The other half of that verdict: a summary banner above the list was
tried and **cut**. The row carries the message; a yellow box repeating
it just pushed the list down. The only survivor outside the row is the
small red count in the heading. Inside an opened row the notice stays —
it's on demand, and it's what explains the blank cards.

**Working orders get the same idea in a friendlier key.** An order still
out there (broker `cancelable`, which is what the app's Cancel buttons
already gate on — steadier than matching WORKING / QUEUED /
PENDING_ACTIVATION) tints its row **light yellow**, no stamp and no red:
it isn't a problem, it's just a state worth picking out. Its header also
carries `bid … / ask … · limit …` (`_working_order_md`) so "is my price
anywhere near the market?" doesn't need the row opened. The limit is the
recorded `credit` — that *is* the price the order went out with — so it
shows even when the quote doesn't come back.

That readout costs the tab's **one exception to deferred loading**: an
unresolved opening order is quoted while collapsed. The set is small and
self-limiting (only orders still in flight, each leaving the moment it
fills or dies), and the status read that would tell us it's working is
queued in the same batch, so we can't wait for it first.

Two mechanics behind it, in `_stamp_md` / `_alert_row_css`:

- The stamp is **markdown, not HTML** — a `st.button` label renders
  markdown only (see `_day_head_md`). The caps are written into the
  string rather than applied with `text-transform`, so the shout
  survives even if the CSS stops matching, and the `**` does double
  duty: it bolds the text *and* emits the `<strong>` that the CSS hangs
  the forced red on. That matters because the header pins
  `button p {color: … !important}` — only a declaration on the element
  itself beats it, never an inherited one.
- The row rules are **per trade id**, emitted after the shared header
  block whose transparent-background rule they must beat at equal
  specificity, and the gray is **solid** with a separate value under
  `html[data-osc-theme="dark"]` — the label color flips with the theme,
  so one literal light gray would be near-white text on near-white.

## Manual test plan

`TRADING_TEST_PLAN.html` (tracked, open it in a browser) is the
checklist for the trading paths — sell, close, roll, and the
cross-cutting safety gates. It splits into a **Paper pass** (rows marked
Paper/Either, safe any time) and a **Live pass** (real orders, market
hours). Progress is stored per row id in browser localStorage, so
editing a row's text keeps existing checkmarks; adding or renaming a row
id resets that one. Keep it in step with the UI — a behavior change that
makes a row unrunnable is a bug in one of the two.

## Settings: two config layers, kept disjoint

- `config.toml` — machine + secrets layer. Schwab credentials, the
  `paper` live-order gate, default provider. **Hand-edited only; the app
  never writes it.** `tomllib` is read-only, a TOML writer would drop
  the comments that document the file, and `config.py`'s lenient loader
  exists because this file gets hand-edited and breaks.
- `settings/settings.json` — preference layer, written by the ⚙️
  Settings dialog (`settings_store.py` + `settings_ui.py`). Nothing
  security- or safety-critical goes here, so a mis-click can't arm live
  trading. Read per rerun, so edits apply with no restart.

Never add a credential or the `paper` flag to the JSON layer, and never
put a preference in both files — disjoint keys mean there's no
precedence question.

**Hidden positions are display-only.** `position_filters` rules are
applied where the Positions tab *renders* (never inside
`positions_cache` or `trade_actions`), because coverage and sizing must
keep seeing every leg — hiding a short call must not free its shares for
a second covered call. Filtering after the cached read also means a
settings change lands on the next rerun instead of waiting out the 60s
TTL.

**Hiding is per symbol, and covers stock too.** `_held_positions()`
merges `option_positions` with `stock_positions` so a name you hold only
shares of is pickable at all — it wasn't, when the dialog enumerated
option legs. Three things that follow:

- Stock rows are normalized to carry `underlying` (`equity_positions`
  calls it `ticker`), because that's the field `pf.matches` keys on.
- They must reach `split_rules_for_ui` as well as the screen. It decides
  tick-vs-carry from the symbols it's given, so omitting them exiles an
  existing shares-only rule to *Other rules — matches nothing you hold*.
- They must **never** reach `pf.leg_label`, which builds "TICKER
  expiration $strike RIGHT" and renders a share row as `PLNH ? — ?`.
  `_is_stock()` is the discriminator.

The stocks table names its hidden symbols in its own caption rather than
pointing at the option table's *Hidden positions* expander — hide a
symbol you hold no options on and that expander never renders.

**Masked balances are display-only too**, and for the same reason:
`settings_ui.mask_money()` is a formatter over already-rendered text, so
sizing, coverage and affordability checks never see the mask. A test
pins that `trade_actions` and `position_filters` don't mention it. The
⚙️ tick (`mask_balances`) is the persistent default; the 👁 button beside
each masked figure overrides it **for the session only**
(`_osc_reveal_balances`), the same shape as the hidden-position "show
these anyway" toggle — revealing a number to read it must not quietly
turn the preference off.

Masking covers *account* balances only — the buying-power line above the
Positions table and on each of the four order screens (tracked close,
live close, roll, unwind), plus the Sell dialog's account figures. Order
prices, collateral, position values and P/L stay visible: they say what a
trade costs, not what you hold.

## Output columns

| Column  | Meaning                                            |
|---------|----------------------------------------------------|
| Top     | Web UI only. Rank within the top-N list per type (1 = strongest signal); blank if not in top N |
| Strike  | Option strike price                                |
| Expiration | Expiration date                                 |
| DTE     | Days to expiration                                 |
| Bid/Ask/Mid | Market prices                                 |
| IV%     | Implied volatility (annualized %)                  |
| IV+pp   | IV excess above surface fit (positive = rich)      |
| _score_ | Active-score column (z-score, VRP, etc.) shown next to IV+pp when a non-default score drives ranking; header is the score's label |
| Delta   | Black-Scholes delta (call: 0–1, put: −1–0)         |
| Ann%    | Annualized yield: calls vs. spot, puts vs. strike  |
| OI      | Open interest                                      |
| Vol     | Web UI only. Today's trading volume (short-term liquidity) |
| NetCr   | Roll mode only: net credit received if rolled here |

## LT capital gains note

Selling an option and holding the short position for 366+ days
qualifies the premium for long-term capital gains rates. The tool
prints the earliest qualifying close date for a position opened today.

## YouTube production materials (sibling private repo)

Scripts, slide HTML, and image assets for the YouTube tutorials
about this tool live in a separate private repo at
`../stockpile-private/options-scanner/youtube/` (sibling directory
to this one). They are active working material and Claude should
treat them as in-scope when asked.

Layout: one subfolder per episode, under
`../stockpile-private/options-scanner/youtube/`.

Folders are named `epN-slug` (e.g. `ep1-scanner-intro`,
`ep9-place-put-trades`), each holding `script.md`, slide HTMLs
(`*-slide.html`), and an `images/` directory of thumbnails and
screenshots. Episodes to date: `ep1-scanner-intro` (full tool
walkthrough), `ep2-schwab`, `ep3-covered-calls`, `ep4-cli-agent`,
`ep5-puts`, `ep7-3d-surface`, `ep8-watchlists`,
`ep9-place-put-trades`, and `ep10-trading-assistant` — the most
recently edited, so the one likely in active drafting. Unnumbered
`future-*` folders (e.g. `future-rolls`, `future-wheel`) park concepts
that haven't been scheduled.

When code on this branch changes something an in-flight script shows,
the script needs updating too — see "Keeping the two repos in sync" in
the root `CLAUDE.md`.

When the user asks about "the script", "the episode", or "the
YouTube video" without naming one, assume the most recent episode
folder. Read the existing script before making edits — episodes
follow a consistent template (slide cues in `[NN ...]`, on-camera
directions in parens, content blocks separated by `---`).
