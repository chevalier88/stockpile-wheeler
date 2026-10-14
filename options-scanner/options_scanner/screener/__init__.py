"""Crash-resilience + wheelability stock screener.

Finds candidates for the manually-run "wheel" strategy (sell cash-secured
puts, take assignment, sell covered calls, repeat) across a broad
universe, scored on two independent axes:

- **resilience** — how a stock has behaved relative to the broader
  market and an AI/semiconductor proxy on average and on specific past
  stress days, derived purely from price history (`resilience.py`).
- **wheelability** — whether its options are actually liquid and
  well-paying near the wheel's usual ~0.30-delta / 30-45 DTE target
  (`wheelability.py`), reusing the scanner's existing chain-fetch and
  IV-surface pipeline.

The two scores are deliberately not combined into one number — see
`options-scanner/tabs/screener.py`, which plots them against each other
instead of ranking by a composite. Resilience is a measured exposure
profile, not crash immunity: correlations move toward 1 in a real
market-wide event, and nothing here changes that.
"""
