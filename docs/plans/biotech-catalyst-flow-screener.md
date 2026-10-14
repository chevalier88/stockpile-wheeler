# Build plan — Biotech catalyst + options-flow screener

Status: **design, not started.** Target: `options-scanner/options_scanner/catalyst/`
and `options-scanner/options_scanner/flow/`, plus a new
`tabs/catalysts.py`.

This document is the complete specification. Work through the phases in
order. Each phase has explicit acceptance criteria — do not start a phase
until the previous one's criteria pass.

---

## 0. Why this exists (read before writing code)

This screens for a specific, falsifiable pattern observed on Alnylam
(ALNY) ahead of its HELIOS-B Phase 3 readout on 2024-06-24, when the
stock gapped from $165.70 to open at $219.37 (+32%).

On 2024-06-14 12:44:36 and 2024-06-17 10:36:48, five option prints
landed in two groups. Within each group every leg shared an **identical
timestamp to the second** and an **identical size** (1.45K and 1.5K
contracts respectively):

| Date | Contract | Price | Size | Premium | IV | Underlying |
|---|---|---|---|---|---|---|
| 06/17 10:36:48 | ALNY 240920 250.00C | 7.00 | 1.5K | $1.05M | 78.14% | 163.61 |
| 06/17 10:36:48 | ALNY 240920 150.00C | 39.00 | 1.5K | $5.85M | 95.48% | 163.61 |
| 06/14 12:44:36 | ALNY 240719 150.00P | 21.70 | 1.45K | $3.15M | 133.48% | 161.64 |
| 06/14 12:44:36 | ALNY 240816 200.00C | 16.80 | 1.45K | $2.44M | 104.36% | 161.64 |
| 06/14 12:44:36 | ALNY 240816 220.00C | 11.40 | 1.45K | $1.65M | 97.72% | 161.64 |

**The hypothesis to test:**

> A **packaged**, **directionally-skewed**, **repeated** options position
> opened in a biotech whose **dated binary catalyst** falls inside that
> position's expiry window precedes outsized moves at a rate above the
> base rate for that catalyst class.

Four components, each separately screenable. What makes this tractable in
biotech and nowhere else: the catalysts are **dated in advance** and
publicly registered.

### What is NOT the signal

Read this carefully — most of the work is avoiding false positives.

- **Premium size is not the signal.** $13.1M on ALNY's ~$20.7B cap is
  ~6.3bp. Ordinary event positioning. Thresholds must scale to market cap.
- **High IV is not the signal.** That 133.48% on the July expiry is the
  *market already knowing* a readout was due. It is a **control** that
  confirms a catalyst exists, never a ranking input.
- **The side label is not reliable.** All five rows above were tagged
  "Active Buys" by the feed. Legs do not coincide to the second at
  matched size by accident — these were multi-leg packages, and at least
  one leg in each is a sale. The feed applies side inference per-leg.
- **Two-sided volatility bets carry no directional information.** Most
  pre-catalyst institutional flow is straddle/strangle vol buying. That
  is the noise. Discard it.

### What the hypothesis says the signal IS

1. Multi-leg package (shared timestamp + matched size, or an explicit
   `STRATEGY` classification from the feed).
2. Net package delta materially non-zero (directional, not a vol bet).
3. Repeated — same size ±5% across ≥2 sessions within 10 trading days.
   ALNY ran 1.45K and 1.5K three days apart. This is likely the
   strongest single discriminator; coincidence does not reproduce it.
4. A dated catalyst inside the expiry window.

### Known weakness, stated up front

Strike-distance-versus-implied-move looks attractive but is weak. ALNY's
July IV of 133.48% over ~35 days implies 1σ ≈ 41%. The 220/250 strikes
sat at +36%/+53% from spot — roughly 1σ. **This metric would not have
flagged ALNY as unusual.** Compute and record it, but do not rank on it
and do not tune it. Directional skew in a large repeated package is the
real candidate.

---

## 1. Hard constraints — do not violate these

These are not suggestions. A lesser agent's most likely failure modes
are all listed here.

1. **This feature places no orders.** It is read-only end to end. Do not
   import `confirm_gate`, do not touch `trade_actions`, do not add a
   Place path. If a hand-off to the existing Sell dialog is ever wanted,
   that is a separate task with its own review.
2. **moomoo's event feed has no history.** `get_option_event` takes
   `(option_market, count=200, page=None, filter_list=None, sort=None)`.
   There is **no date-range parameter**. You cannot backtest from it. Any
   claim of a historical hit rate built from this source is false. The
   only free route to a sample is **forward collection** (Phase 3).
3. **Do not build the point-in-time universe from current constituents.**
   Biotech's failure mode is going to zero and delisting. A universe
   built from today's listings has silently deleted every catastrophe.
   `screener/universe.py`'s Wikipedia pattern **must not** be reused for
   any backtest universe. For live screening it is fine.
4. **Do not tune thresholds on the sample you measure on.** Every
   numeric threshold in this document (3bp, ±5%, 10 days, 0.25 delta) is
   **pre-registered**. Write them as named constants with a comment
   saying they are pre-registered. If you change one, you must record
   the change and date in `docs/plans/threshold-changes.md`, and the
   measurement sample resets from that date.
5. **Language.** Follow `options-scanner/CLAUDE.md`. This is a
   **screening heuristic**. Never write "mispriced", "overpriced",
   "anomaly", "unusual" in a way that asserts a market error. Acceptable:
   "IV-rich", "outlier", "stands above the surface", "flagged package".
6. **Never claim this detects insider trading.** The structures in the
   ALNY example capped upside at 220 and 250, which is inconsistent with
   certain foreknowledge. User-facing copy says "institutional
   positioning", never "insider".
7. **Keep axes independent.** Follow the Crash Candidates precedent:
   catalyst score and flow score are reported separately and never
   collapsed into one number.

---

## 2. Data sources — endpoints and auth

Verify every schema against live docs before coding. Field paths below
are a starting point, not gospel.

### 2.1 ClinicalTrials.gov API v2 — primary catalyst source (free, no auth)

Base: `https://clinicaltrials.gov/api/v2/studies`

The single most valuable field is **`primaryCompletionDate`**. Topline
data typically follows primary completion by 2–4 months; that lag is
itself an empirical distribution you should fit from resolved history
rather than hardcode.

Useful query params (confirm against `https://clinicaltrials.gov/data-api/api`):
- `query.term` — free text
- `filter.overallStatus` — `RECRUITING`, `ACTIVE_NOT_RECRUITING`, `COMPLETED`
- `fields` — restrict payload
- `pageSize` (max 1000), `pageToken` — pagination

Fields to extract:
- `protocolSection.identificationModule.nctId`
- `protocolSection.statusModule.primaryCompletionDateStruct.date` and `.type`
  (`ACTUAL` vs `ESTIMATED` — **this is your confidence signal**)
- `protocolSection.designModule.phases[]` — keep `PHASE2`, `PHASE3`
- `protocolSection.sponsorModule.leadSponsor.name`
- `protocolSection.conditionsModule.conditions[]`

Sponsor name → ticker mapping is the hard part. Build a curated
`catalyst/sponsor_map.json` seeded from SEC company names, with manual
overrides. Do not attempt fuzzy matching without a review step — a wrong
map silently attributes a catalyst to the wrong ticker.

Rate limit: be polite, ≤10 req/sec, cache aggressively.

### 2.2 SEC EDGAR — PDUFA dates and guidance language (free, UA required)

SEC **requires** a `User-Agent` header of the form
`Company Name contact@example.com` or requests are refused. Rate limit
10 req/sec, hard.

- Ticker→CIK: `https://www.sec.gov/files/company_tickers.json`
- Company metadata incl. **SIC code**:
  `https://data.sec.gov/submissions/CIK##########.json` (CIK zero-padded
  to 10 digits)
- Full-text search: `https://efts.sec.gov/LATEST/search-index?q=...`
  Search 8-K and 10-Q text for `"PDUFA"`,
  `"target action date"`, `"topline"`, `"data expected"`.

SIC codes defining the universe:
- `2834` Pharmaceutical Preparations
- `2836` Biological Products
- `2835` In Vitro & In Vivo Diagnostic Substances
- `8731` Commercial Physical & Biological Research

### 2.3 Conference calendars — manual, annual

ASCO, AACR, ESC, AHA, ASH publish presentation dates ~12 months ahead.
Abstract titles often drop weeks before the data. No API; maintain
`catalyst/conferences.json` by hand, refreshed yearly. Low effort, high
value — several of the biggest biotech moves are conference-timed.

### 2.4 moomoo — live flow (entitlement unconfirmed, see Phase 0)

SDK 10.09.6908, already installed. Gateway: OpenD must be running and
logged in; config in `[moomoo]` of `options-scanner/config.toml`.

`get_option_event(option_market, count=200, page=None, filter_list=None, sort=None)`

Filters are built with:
```python
OptionEventFilter(indicator_type, value_list=None, interval_min=None,
                  interval_max=None, min_inclusive=True, max_inclusive=True,
                  string_value_list=None, security_list=None)
```

`EventIndicatorType` members confirmed present in this SDK build:

| Indicator | Use in this plan |
|---|---|
| `STRATEGY` | **Server-side multi-leg classification — see below** |
| `TURNOVER` | Premium threshold (Gate 3) |
| `VO_RATIO` | Volume ÷ open interest — the canonical unusual metric |
| `ORDER_TYPE` | Normal=0, Sweep=1, Cross=2, Floor=4 |
| `TICKER_TYPE` | BUY / SELL / NEUTRAL — **do not trust alone** |
| `DELTA` `GAMMA` `VEGA` `THETA` `RHO` `IV` | Net-delta computation (Gate 4) |
| `INDUSTRY_PLATE` `CONCEPT_PLATE` | Possible server-side sector filter |
| `MARKET_CAP` | Universe sizing |
| `OWNER_LIST` + `security_list` | Restrict to our ticker list |
| `EXPIRY_DAYS` `STRIKE_PRICE` `OTM` `MONEY_TYPE` | Strike geometry |
| `VOLUME` `TOTAL_VOLUME` `TOTAL_OI` | Liquidity context |
| `TIME` `MAX_DAY_NUM` | **Unknown semantics — probe these** |

**`OptionStrategyType` is the significant find.** Members:
`SINGLE`, `SPREAD`, `STRADDLE`, `STRANGLE`, `CALENDAR_SPREAD`,
`DIAGONAL_SPREAD`, `BUTTERFLY`, `CONDOR`, `IRON_BUTTERFLY`,
`IRON_CONDOR`, `COLLAR`, `COVERED`, `CUSTOM`.

If the feed populates this, it does Gate 4's structure classification
server-side. Filter **in** `SPREAD`, `CALENDAR_SPREAD`,
`DIAGONAL_SPREAD`, `CUSTOM`, `COLLAR`; filter **out** `STRADDLE`,
`STRANGLE` (non-directional), `COVERED` (institutional overwriting),
`SINGLE` (no package). Confirm it is actually populated in Phase 0 —
do not assume.

### 2.5 Polygon.io — optional paid history

Only if you decide the question justifies paying. Trade-level options
history at `https://api.polygon.io/v3/trades/{optionsTicker}`, options
flat files on the Starter tier and up. This is the **only** way to get a
retrospective base rate. Treat as out of scope unless explicitly asked.

---

## Phase 0 — Probe moomoo entitlement (BLOCKING)

Everything downstream of Phase 2 depends on what comes back here.

`options-scanner/probe_option_flow.py` already exists and is read-only.
**Extend it**, do not rewrite it, to answer these specific questions:

1. Does `get_option_event` return rows at all on this account tier, or
   does it fail with a permissions/subscription error?
2. **What columns does the returned `event_list` DataFrame actually
   have?** Print `list(df.columns)` and one full row. Everything
   downstream is written against these names.
3. Is `STRATEGY` populated with real values, or always `NONE`/`SINGLE`?
   Filter for `STRATEGY in (SPREAD, STRADDLE, STRANGLE)` and see if
   anything returns.
4. Does the timestamp have **second** resolution? The package detector
   needs it. Print raw values.
5. What do `TIME` and `MAX_DAY_NUM` filter on? Try `MAX_DAY_NUM` with
   `interval_min=1 / 5 / 30` and see whether older rows appear. **If
   `MAX_DAY_NUM` yields a lookback window of any length, say so loudly
   in the output** — it partially relaxes constraint #2 and changes the
   plan.
6. Does `page` actually paginate past `count`? Pull page 1 and 2 and
   compare.
7. Does `INDUSTRY_PLATE` / `CONCEPT_PLATE` accept a biotech value?
   Try `string_value_list` with plausible sector names and report what
   is rejected vs accepted.

**Acceptance:** a written summary at the top of the probe output
answering all seven, pasted back to the user. **Do not write any other
module until this is answered.** If the entitlement is absent, Phases
1–2 still proceed (they need no flow data); Phases 3–6 are blocked.

---

## Phase 1 — Catalyst calendar (independent, build this first)

Useful standalone regardless of whether flow data ever arrives. Knowing
which biotechs have a dated binary inside a listed expiry is a tradeable
input on its own.

### Files

```
options-scanner/options_scanner/catalyst/
    __init__.py
    sources.py        # ClinicalTrials.gov + SEC fetchers
    calendar.py       # normalize into CatalystEvent records
    sponsor_map.json  # sponsor name -> ticker, curated
    conferences.json  # hand-maintained conference dates
    store.py          # sqlite persistence
```

### Data model

```python
@dataclass(frozen=True)
class CatalystEvent:
    ticker: str
    catalyst_date: date        # best estimate
    date_type: str             # "ACTUAL" | "ESTIMATED" | "GUIDED" | "CONFERENCE"
    confidence: float          # 0.0-1.0, see below
    kind: str                  # "READOUT" | "PDUFA" | "ADCOMM" | "CONFERENCE"
    phase: str | None          # "PHASE2" | "PHASE3" | None
    source: str                # "CTGOV:NCT01234567" | "SEC:0001234-25-000123"
    source_url: str
    indication: str | None
    fetched_at: datetime
```

Confidence rubric — pre-registered:
- `1.0` PDUFA date stated in an 8-K (a legal deadline)
- `0.8` conference presentation date, company confirmed
- `0.6` `primaryCompletionDateStruct.type == "ACTUAL"`
- `0.4` company guidance naming a quarter ("topline expected 2H26")
- `0.2` `primaryCompletionDateStruct.type == "ESTIMATED"`

### Storage

sqlite at `options-scanner/cache/catalysts.db`. Mirror the
connect/create-table/upsert shape in `iv_history.py` and
`screener/store.py` exactly — do not invent a new persistence pattern.
Primary key `(ticker, source, catalyst_date)`.

### Acceptance criteria

- `uv run python -c "from options_scanner.catalyst import calendar; print(len(calendar.build()))"`
  returns >100 events.
- ALNY appears with a Phase 3 event for HELIOS-B (NCT04153149) when
  queried historically. **This is your regression fixture.**
- Tests in `options-scanner/tests/test_catalyst_calendar.py`:
  - sponsor→ticker mapping is deterministic and rejects ambiguity
  - confidence rubric maps correctly for all five cases
  - a malformed/missing date does not raise, it yields `None` and is
    skipped with a logged reason
  - network calls are mocked; no test hits the live API

---

## Phase 2 — Universe + options-liquidity gate

### File

`options-scanner/options_scanner/catalyst/universe.py`

Do **not** reuse `screener/universe.py` — different source, different
semantics.

### Rules

1. Pull ticker→CIK from SEC `company_tickers.json`.
2. Fetch SIC per CIK from the submissions endpoint; keep
   `{2834, 2835, 2836, 8731}`.
3. **Gate on options tradeability, not market cap:**
   - ≥3 expiries listed
   - ≥500 total open interest across the chain
   - median bid-ask spread ≤15% of mid
   Reuse `chain.fetch_chain` for this — do not write a new fetcher.
4. Exclude tickers with an announced merger (flow there is deal arb with
   a different base rate). Detect via SEC full-text search for
   `"Agreement and Plan of Merger"` in the last 180 days.

Cache to `options-scanner/cache/biotech_universe.json` with a 30-day
staleness window, same shape as `screener/universe.py`'s cache. Send a
browser `User-Agent`; SEC refuses the urllib default.

### Acceptance

- Returns 200–600 tickers. If it returns <100 or >1500, the SIC filter
  or liquidity gate is wrong — stop and report.
- ALNY is present.
- A known illiquid microcap biotech is absent.

---

## Phase 3 — Flow collector (starts the forward sample)

**Blocked on Phase 0.** Only build once the probe confirms the feed
works and you know the real column names.

### Files

```
options-scanner/options_scanner/flow/
    __init__.py
    moomoo_feed.py    # thin wrapper over get_option_event
    collector.py      # polling loop -> sqlite
    store.py          # sqlite persistence
```

### Behaviour

Poll `get_option_event` on a schedule during market hours, filtered to:
- `OWNER_LIST` = the Phase 2 universe (chunk if there is a size limit)
- `TURNOVER` `interval_min` = a low floor (collect broadly, filter later
  — you cannot re-query the past, so **over-collect**)
- paginate via `page` until exhausted or a sane cap

Write every row raw to `options-scanner/cache/flow.db`, table
`flow_events`, with a `collected_at` column. **Store the raw feed
columns verbatim in addition to any normalized ones.** You cannot go
back and re-fetch, so never discard a field because it looks unused.

Dedupe on the feed's own event id if one exists; otherwise on
`(ticker, option_symbol, timestamp, size, price)`.

### Acceptance

- Runs for one full session without crashing; row count > 0.
- Restarting mid-session does not duplicate rows.
- A deliberately killed process leaves the DB readable (use WAL).
- Tests cover the dedupe key and the raw-column passthrough against a
  recorded fixture payload. No test hits the live gateway.

---

## Phase 4 — Package detection and scoring

### File

`options-scanner/options_scanner/flow/packages.py`

### Step 1 — group into packages

```python
def detect_packages(events: list[FlowEvent]) -> list[Package]:
    """Group legs executed as one order.

    Primary key: identical (ticker, timestamp_to_second, size).
    If the feed populates STRATEGY, prefer its grouping and use the
    timestamp+size rule as a fallback/cross-check.
    Require >= 2 legs. Single-leg events are not packages.
    """
```

### Step 2 — gates (all pre-registered constants)

```python
MIN_PREMIUM_BPS_OF_MKTCAP = 3.0   # ALNY was ~6.3bp. Scales to size.
MIN_ABS_NET_DELTA = 0.25          # per-contract-equivalent; below = vol bet
REPEAT_SIZE_TOLERANCE = 0.05      # +/-5%
REPEAT_WINDOW_DAYS = 10
```

- **Premium gate:** `total_premium / market_cap >= 3bp`. Absolute dollar
  thresholds are wrong — they over-flag megacaps and miss small caps.
- **Directionality gate:** compute net package delta from the feed's
  per-leg `DELTA`, signed by inferred side. Discard `|net delta|` below
  threshold. **This is the most important filter** — it removes the
  two-sided vol buying that dominates pre-catalyst flow.
- **Repeat gate:** flag when the same size ±5% recurs across ≥2 distinct
  sessions within 10 trading days. Record as a boolean plus the match
  dates; do not fold into a score.

### Step 3 — side inference, since the label lies

Do not trust `TICKER_TYPE` per leg. Infer from geometry:

| Geometry | Reading |
|---|---|
| Same expiry, 2 calls, diff strikes, equal size | vertical — **lower strike bought** |
| Same expiry, 2 puts, diff strikes, equal size | vertical — higher strike bought |
| Same expiry, call+put, **same** strike | straddle → **discard, non-directional** |
| Same expiry, call+put, diff strikes | strangle or risk reversal → classify by net delta |
| Different expiries, same strike | calendar → **discard** |
| Call spread + opposing put, diff expiry | financed risk reversal → **keep** |

Record both the feed's label and your inference, and a boolean for
whether they agree. Disagreement rate is itself a diagnostic worth
watching.

### Step 4 — catalyst join

Keep a package only if a `CatalystEvent` falls **strictly inside** the
expiry window of at least one leg. Record the matched event, the days
from trade to catalyst, and the catalyst confidence.

### Step 5 — record the implied target (output only, do not rank)

```python
implied_target_move = short_call_strike / spot - 1
market_implied_move = atm_straddle_price / spot   # catalyst expiry
conviction_ratio    = implied_target_move / market_implied_move
```

Store all three. **Do not rank on `conviction_ratio`** — see §0, it
would not have flagged ALNY. It is recorded so the eventual base-rate
study can test whether it has any content.

### Acceptance — the ALNY fixture

Build `options-scanner/tests/fixtures/alny_2024_06.json` from the five
prints in §0. Then `test_packages.py` must assert:

- exactly **two** packages detected (not five singles, not one)
- the 06/17 pair classifies as a vertical call spread with the 150 leg
  bought and the 250 leg sold
- the 06/14 group is flagged directional (positive net delta), **not**
  discarded as a straddle
- the repeat gate fires: 1.45K and 1.5K are within ±5%, three sessions apart
- a synthetic 150-strike straddle added to the fixture **is** discarded
- premium gate: $13.1M against a $20.7B cap passes 3bp; the same
  premium against a $500B cap fails

That last pair of cases is what proves the scaling is right.

---

## Phase 5 — Open-interest confirmation

### File

`options-scanner/options_scanner/flow/oi_confirm.py`

The only reliable way to tell an **opening** trade from a closing one,
and it is free — OI is published daily and `chain.fetch_chain` already
returns it.

Next session, for each leg of each flagged package, compare OI at that
strike against the prior day:

- OI rose by ≈ trade size → **opening**. Keep.
- OI fell → **closing**. A closing trade carries no forward information.
  Mark and exclude from the study sample.
- Ambiguous (other activity at the strike) → mark `UNKNOWN`, keep but
  flag.

Run as a daily job; write `oi_status` back to the package record.

**Acceptance:** a package whose legs' OI fell is marked `closing`. A
synthetic case with OI rising by exactly the trade size is `opening`.
Tests use fixtures, not live chains.

---

## Phase 6 — Outcome labelling and base rate

### File

`options-scanner/options_scanner/flow/outcomes.py`

When a catalyst date passes, label every flagged package:

- realized move: close before catalyst → close after (use the **open**
  after, too — ALNY gapped, and a strategy that assumes you can react to
  the news is wrong)
- did the underlying reach the implied target strike?
- payoff of the inferred structure, held to expiry
- the catalyst's binary outcome (hit/miss) where determinable from the 8-K

### Statistical guardrails — enforce these in the reporting code

1. **Report the denominator.** Every hit rate shows `n`. A rate without
   its sample size is not a result.
2. **Report confidence intervals**, Wilson score for proportions. Expect
   15–30 qualifying packages per year; three years gives 50–90. At that
   `n` you likely **cannot** distinguish a 55% hit rate from 45%. The
   reporting must make that visible rather than hide it behind a point
   estimate.
3. **Benchmark against the naive alternatives**, always shown alongside:
   - buy a straddle on *every* dated catalyst in the universe
   - buy the underlying on every dated catalyst
   - do nothing
   A strategy that does not beat "straddle everything" is not a strategy.
4. **Hold out by time.** Thresholds are pre-registered (§1.4). If any is
   ever retuned, the sample resets from that date and the report says so.
5. **Never report a backtest from moomoo data.** It has no history. The
   sample is forward-collected and the report must state the collection
   start date.

---

## Phase 7 — UI tab (last, and only if Phase 6 shows something)

`options-scanner/options_scanner/tabs/catalysts.py`, registered in
`run_app.py`'s `TAB_NAMES`.

Two independently-scored columns, never combined (Crash Candidates
precedent): **Catalyst** (date, kind, confidence, days out) and **Flow**
(packages flagged, premium in bp, net delta, repeat flag, OI status).

Not a broker tab — do **not** add to `BROKER_TABS`.

Caption must state plainly: screening heuristic, forward-collected
sample from <date>, n=<count>, no backtest. Same register as the GEX
tab's "assumed, not measured" and the Crash Candidates caveat.

---

## Execution order summary

| Phase | Depends on | Blocked by entitlement? | Standalone value |
|---|---|---|---|
| 0 Probe | — | n/a | Answers everything else |
| 1 Catalyst calendar | — | No | **High** — useful alone |
| 2 Universe | 1 | No | Medium |
| 3 Flow collector | 0, 2 | **Yes** | Starts the sample clock |
| 4 Packages | 3 | Yes | Low until 6 |
| 5 OI confirm | 4 | Partly (needs chains only) | Low |
| 6 Outcomes | 4, 5 | Yes | **The actual answer** |
| 7 UI | 6 | Yes | Only if 6 is positive |

Start Phase 0 and Phase 1 in parallel — Phase 1 needs no flow
entitlement, and it is the piece with value regardless of how the
hypothesis resolves.

---

## Honest expected outcome

The most likely result is that Phase 6 returns a wide confidence
interval straddling the benchmark, and the correct conclusion is "not
demonstrated at this sample size." Build it so that answer is
publishable rather than embarrassing: the catalyst calendar from Phase 1
remains useful either way, and a clean negative on a pre-registered
hypothesis is worth more than a tuned positive on 20 observations.
