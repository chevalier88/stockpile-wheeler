# The options scanner, explained for a beginner (ELI20)

This explains how `options-scanner/` actually works and what strategy
it's built around, assuming you know almost nothing about options yet.
It defines every acronym and Greek letter the tool shows you, in the
order you'd need them. For more depth later, see
[`docs/glossary.md`](glossary.md),
[`options-scanner/INTERPRETING_IV.md`](../options-scanner/INTERPRETING_IV.md),
and [`options-scanner/SPREADS.md`](../options-scanner/SPREADS.md) — this
doc is the on-ramp to those, not a replacement.

## 1. What this tool is, in one paragraph

You give it a stock ticker (and optionally say whether you own 100+
shares of it). It downloads every option contract currently traded on
that stock, does some math on all of them, and shows you a chart plus
two tables ranking which specific contract looks most worth a second
look — either to **sell** (the default — you collect money up front)
or to **buy**. It is not predicting where the stock will go. It is
comparing options on the *same stock* against each other and flagging
the ones priced unusually rich or unusually cheap relative to their
neighbors.

## 2. Options 101 — the absolute basics

An **option** is a contract tied to 100 shares of a stock. Two kinds:

- A **call** gives its owner the right to *buy* 100 shares at a fixed
  price (the **strike**) any time before a cutoff date (the
  **expiration**). **DTE** (days to expiration) is just how many
  calendar days are left until that cutoff.
- A **put** gives its owner the right to *sell* 100 shares at the
  strike before expiration.

Every option has a market price, called the **premium**. If you *sell*
an option, you receive the premium up front, in exchange for taking on
an obligation (you might have to deliver shares, or buy them, later).
If you *buy* one, you pay the premium for the right (never the
obligation) to do something later.

Worked example: say a stock trades at $100 and you own 100 shares.
You could sell someone a call with a $110 strike expiring in 3 months,
collecting (say) $200 in premium today. If the stock is below $110 at
expiration, nothing happens — you keep the $200 and your shares. If
it's above $110, you're obligated to sell your 100 shares for $110
each (you still keep the $200). This is called a **covered call** —
"covered" because you already own the shares you might have to hand
over. The put mirror image is a **cash-secured put (CSP)**: you sell a
put and set aside enough cash to buy 100 shares at the strike if the
stock falls that low, collecting premium for taking on that chance.

A strike **below** the current stock price (for a call) or **above**
it (for a put) is **ITM** (in the money) — it has real, intrinsic
value right now. A strike on the other side is **OTM** (out of the
money) — purely a bet on future movement. A strike right at the
current price is **ATM** (at the money).

If the stock ends up past your strike at expiration, you may get
**assigned** — forced to deliver or buy shares per the contract. The
option holder choosing to use their right is called **exercising** it.

## 3. Implied volatility (IV) — why it's the whole game

Every option's price can be translated into a single number called
**implied volatility (IV)**: roughly, "how much the market expects this
stock to wiggle around, annualized, expressed as a percentage." Higher
IV means the market expects bigger swings, which means the option is
priced higher (more premium for the seller, more cost for the buyer).
IV isn't guessed directly — it's reverse-engineered from the option's
market price using a pricing formula called **Black-Scholes** (more on
that below).

**The single most confusing idea in this tool, so pay attention:**
IV is itself a percentage (e.g. 45%). When comparing two IVs, you have
to distinguish **percentage points (pp)** from **percent**. Going from
45% to 48% is **+3 percentage points**, NOT "+3%" — the actual relative
increase is closer to +6.7%. The scanner always reports gaps in
percentage points, specifically because pp is an apples-to-apples unit
that works the same way at every strike and every expiration, whereas
"percent change" would mean something different depending on the
starting IV.

## 4. The core strategy: comparing an option to its neighbors ("IV+pp")

Picture every option on a given expiration date, plotted by strike on
one axis and IV on the other. In a healthy, liquid market, these points
trace a smooth curve — traders call this the **volatility smile** (or,
across strikes and expirations together, the **volatility surface**).
Market makers (the firms that quote prices) try to keep it smooth,
because a smooth curve is harder for anyone to arbitrage.

The scanner fits a smooth mathematical curve to that chain — its best
guess at what each strike's IV "should" be, given its neighbors — and
calls that the **fitted surface**. Then, for every real option, it
computes:

```
IV+pp = (that option's actual IV) − (the fitted surface's IV at that strike)
```

- **Positive IV+pp** ("IV-rich"): this specific option is priced
  richer than its neighbors suggest it should be. In **sell mode**
  (the default), these are the candidates worth considering selling —
  more premium for a similar amount of underlying risk.
- **Negative IV+pp** ("IV-cheap"): priced cheaper than its neighbors.
  In **buy mode**, flip everything — these are the candidates worth
  considering buying.

**Rules of thumb the tool uses:** a gap under ~3pp is treated as noise
— the chain is basically uniform, nothing stands out. A gap of 5pp or
more is flagged as a meaningfully stronger signal worth a closer look.

**Important — this is a screening heuristic, not proof of a mispricing.**
There are three honest reasons a strike can sit above the fitted line,
and the tool cannot tell you which one you're looking at:

1. **It's noise** — a stale quote, a thin market, a one-off order
   imbalance. If so, you really are being paid extra for ordinary risk.
2. **It's real information** — e.g. an earnings announcement, a
   lawsuit, an FDA decision baked into that strike/expiration. The
   premium looks rich, but you're being fairly paid (or underpaid) for
   genuinely elevated risk.
3. **It's structural skew** — e.g. downside puts often trade
   persistently rich because people pay up for crash insurance. The
   smooth curve the tool fits doesn't fully capture that, so the "gap"
   is real but isn't an edge — it's a known, permanent shape in the
   market that a 2-D curve can't perfectly trace.

The tool's own project notes are explicit that this ranking is **not a
mispricing claim, not arbitrage, and not a recommendation** — treat a
flagged option as a prompt to go look closer (check the live bid/ask
on your actual broker, check the earnings calendar, ask "does this
make sense?"), not as an instruction to trade.

## 5. The Greeks, and how they chain together

The **Greeks** are a handful of numbers that describe how an option's
price reacts to different things changing. They're computed from the
Black-Scholes formula. Imagine one call option on a $100 stock to see
how they connect:

- **Delta (Δ)** — how much the option's price moves for every $1 the
  stock moves. A delta of 0.30 means roughly "the price moves about
  $0.30 for a $1 stock move." Delta is also used as a rough shortcut
  for "probability this option finishes in the money" — a delta of 30
  is loosely read as "~30% chance this ends up assigned." Delta is
  near 0 deep out of the money, near 1.0 deep in the money, and about
  0.5 right at the money.
- **Gamma (Γ)** — how fast *delta itself* changes as the stock moves.
  Think of delta as speed and gamma as acceleration. Gamma is highest
  for options right at the money (small stock moves there swing the
  odds of finishing ITM a lot) and fades out deep ITM or deep OTM.
- **Theta (θ)** — how much value the option loses purely from time
  passing, with everything else held constant ("time decay"). This is
  almost always working against an option buyer and in favor of an
  option seller — the whole reason selling options is called an
  "income" strategy: every day that passes without the stock moving
  against you, you're a little closer to keeping the full premium.
- **Vega (ν)** — how much the option's price moves if IV itself rises
  or falls by one percentage point, with the stock price held
  constant. Positive for both calls and puts: rising IV makes options
  more expensive, falling IV makes them cheaper, independent of which
  way the stock moves.

**How they interact:** gamma tells you how quickly delta (your rough
assignment-odds number) can shift as the stock moves — a high-gamma
option's "probability" estimate is more volatile than a low-gamma
one's. Theta is the reward a seller is paid for carrying that gamma/
vega risk day by day. Vega is the risk that the market's *fear level*
changes even if the stock doesn't move at all — e.g. this is why a
long straddle (buying both a call and a put, explained below) can
profit purely from IV rising, with the stock going nowhere.

## 6. Other acronyms you'll see on screen

- **OI (open interest)** — total contracts of that exact strike/
  expiration currently open across all traders (not yet closed or
  expired). Higher OI generally means it's easier to trade in and out
  without moving the price much.
- **Vol (volume)** — contracts traded *today* specifically, resets
  daily. Different from OI, which is cumulative.
- **DTE** — days to expiration (defined above).
- **LEAPS** — "Long-term Equity AnticiPation Securities," i.e. options
  expiring roughly 9+ months out. They tend to trade thinly, so their
  IV (and anything derived from it, like delta) is especially prone to
  being stale.
- **CSP** — cash-secured put (defined above).
- **POP (probability of profit)** — the model's estimate of the
  chance a position is profitable at expiration, derived mathematically
  from Black-Scholes. Important caveat: this is a *risk-neutral model
  probability*, not a measured real-world win rate — it assumes stock
  returns follow a specific bell-curve-like distribution, which real
  markets don't perfectly obey (real markets have "fatter tails,"
  i.e. more extreme moves than the model expects).
- **EV (expected value)** — `POP × max profit − (1 − POP) × max loss`.
  A probability-weighted average outcome; positive EV means the model
  thinks the bet is favorable on average (same model-dependence caveat
  as POP applies).
- **R/R (risk/reward)** — max profit divided by max loss. Higher is
  "more reward per dollar risked" — e.g. R/R of 0.5 means you're
  risking $2 to make $1.
- **BE (breakeven)** — the stock price at expiration where you exactly
  break even, no profit or loss.
- **GEX (gamma exposure)** — an *estimate* of how much net gamma risk
  options-market-makers (dealers) are carrying across a stock's whole
  option chain, aggregated by strike. The usual reading: positive GEX
  (dealers net "long" gamma) tends to dampen price swings — think of
  it as market makers buying dips and selling rips to stay hedged,
  which smooths price action. Negative GEX (dealers net "short"
  gamma) tends to amplify swings in whichever direction the stock is
  already moving. The tool computes this from option open interest
  and gamma, and is upfront in its own captions that **"dealer
  positioning is assumed, not measured"** — real-world flows (e.g. a
  fund writing a lot of covered calls) can flip the actual sign. Treat
  GEX as directional context, not a fact about what will happen.
- **NBBO (National Best Bid and Offer)** — the single best buy and
  sell price available across all exchanges right now, consolidated in
  real time. Schwab data reflects this; Yahoo Finance data can be a
  stale last-trade snapshot instead (see section 8).

## 7. The other tabs, briefly

- **Spreads / Directional / Neutral** — instead of one option leg,
  these build multi-leg combinations: e.g. a **vertical spread**
  (sell one strike, buy another, same expiration, to cap your risk), an
  **iron condor** (betting the stock stays in a range), a **straddle**/
  **strangle** (buying both a call and a put, betting on a big move in
  either direction — a "long volatility" bet, driven mostly by vega).
  Full strategy catalog and entry filters: `options-scanner/SPREADS.md`.
- **GEX tab** — charts the gamma-exposure estimate from section 6 by
  strike, including the "zero gamma" strike where the sign flips.
- **Roll** — lets you close an option you already hold and open a new
  one (different strike and/or expiration) in one move, usually to
  collect an additional **net credit** or avoid paying a **net debit**.
  The tool only does the math and order plumbing here — it doesn't
  tell you *when* you should roll.
- **Portfolio** — reads a brokerage CSV export (Schwab, Robinhood,
  Fidelity, Merrill) and scans every open position found in it. The
  file never leaves your machine; the only outbound network call is to
  Yahoo Finance for quotes.

## 8. What this tool is NOT

- **Not a mispricing or arbitrage claim.** A smooth volatility surface
  is just a convenient modeling assumption — real markets legitimately
  have skew and smiles that don't perfectly fit a smooth curve, and an
  option trading "above the line" is still a real risk you're taking
  on if you sell it, not free money.
- **Not a backtested or proven strategy.** There is no track record or
  historical win-rate data behind the IV+pp ranking anywhere in this
  project — the reasoning is theoretical (see the three scenarios in
  section 4), not empirically validated.
- **Not risk management for you.** The tool checks that you have
  enough cash/collateral before letting an order go out, but it has no
  stop-loss, no position-sizing rule, and no commission/slippage
  modeling in its yield numbers — all of that is left to you.
- **Not immune to stale data.** Yahoo Finance IV can lag, especially on
  thinly traded strikes and LEAPS; since delta (and everything derived
  from it) is calculated *from* that IV, stale IV means stale delta
  too. Schwab data (when configured) is fresher and includes real
  broker-reported Greeks instead of Black-Scholes estimates. Either
  way: always check the live bid/ask on your actual broker before
  acting on anything the scanner shows you.

## 9. Trust summary

The underlying math and data plumbing are solid: the Black-Scholes
Greeks, the IV-surface fitting, and the data-cleaning filters (dropping
wide-spread quotes, excluding earnings-distorted contracts, etc.) are
all reasonable, documented, and — notably — more conservative than the
marketing around the tool suggests, right down to banning words like
"mispriced" in its own UI-copy guidelines in favor of "IV-rich"/
"IV-cheap." Real safety mechanics exist before an order is placed
(collateral checks, a confirm-then-place gate). What you should *not*
trust it for is edge: there's no backtest or track record anywhere
validating that IV+pp actually predicts anything, and the project's
own docs repeatedly and explicitly call it a screening heuristic, not
a signal — "not a mispricing claim... not arbitrage... not a
recommendation." Use it to narrow a huge option chain down to a
handful of contracts worth manually investigating, never as an
instruction to place a trade.
