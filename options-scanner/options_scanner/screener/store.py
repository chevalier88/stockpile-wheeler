"""Persistent cache for screener results.

Same shape as `options_scanner.iv_history`: a single stdlib sqlite3
file under `options-scanner/cache/`, an env var override for tests, and
an idempotent per-day upsert (delete-then-insert) so re-running the
same stage on the same day replaces that day's row rather than
duplicating it.

Two tables, written by the two pipeline stages independently —
`resilience` covers the full scanned universe, `wheelability` only the
subset that made it past the resilience filter (see `pipeline.py`), so
a ticker can have a resilience row with no wheelability row yet (or
ever, if its options are too illiquid to bother scoring).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

_DEFAULT_DB = Path(__file__).resolve().parents[2] / "cache" / "screener.db"


def _db_path() -> Path:
    return Path(os.environ.get("OSC_SCREENER_DB", str(_DEFAULT_DB)))


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resilience (
            ticker             TEXT NOT NULL,
            as_of              TEXT NOT NULL,
            name               TEXT,
            sector             TEXT,
            index_membership   TEXT,
            spot               REAL,
            downside_beta_smh  REAL,
            downside_beta_spy  REAL,
            deepseek_excess    REAL,
            tariff_excess      REAL,
            max_drawdown       REAL,
            drawdown_recovered INTEGER,
            realized_vol       REAL,
            resilience_score   REAL,
            PRIMARY KEY (ticker, as_of)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wheelability (
            ticker              TEXT NOT NULL,
            as_of               TEXT NOT NULL,
            strike              REAL,
            expiration          TEXT,
            dte                 INTEGER,
            delta               REAL,
            ann_yield_pct       REAL,
            open_interest       INTEGER,
            spread_pct          REAL,
            iv_excess           REAL,
            wheelability_score  REAL,
            has_tradeable_chain INTEGER,
            PRIMARY KEY (ticker, as_of)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_res_asof ON resilience (as_of)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wheel_asof ON wheelability (as_of)")
    return conn


_RESILIENCE_COLS = (
    "ticker", "as_of", "name", "sector", "index_membership", "spot",
    "downside_beta_smh", "downside_beta_spy", "deepseek_excess",
    "tariff_excess", "max_drawdown", "drawdown_recovered", "realized_vol",
    "resilience_score",
)
_WHEEL_COLS = (
    "ticker", "as_of", "strike", "expiration", "dte", "delta",
    "ann_yield_pct", "open_interest", "spread_pct", "iv_excess",
    "wheelability_score", "has_tradeable_chain",
)


def write_resilience_rows(rows: list[dict], as_of: date | None = None) -> None:
    """Upsert one resilience row per ticker for `as_of` (default today).
    No-op on an empty list; never raises on a sqlite error."""
    if not rows:
        return
    day = (as_of or date.today()).isoformat()
    try:
        with _connect() as conn:
            for r in rows:
                ticker = str(r["ticker"]).upper()
                conn.execute(
                    "DELETE FROM resilience WHERE ticker = ? AND as_of = ?",
                    (ticker, day),
                )
                values = (ticker, day) + tuple(
                    r.get(c) for c in _RESILIENCE_COLS[2:]
                )
                conn.execute(
                    f"INSERT INTO resilience ({', '.join(_RESILIENCE_COLS)}) "
                    f"VALUES ({', '.join('?' * len(_RESILIENCE_COLS))})",
                    values,
                )
    except sqlite3.Error:
        return


def write_wheelability_rows(rows: list[dict], as_of: date | None = None) -> None:
    """Upsert one wheelability row per ticker for `as_of` (default
    today). No-op on an empty list; never raises on a sqlite error."""
    if not rows:
        return
    day = (as_of or date.today()).isoformat()
    try:
        with _connect() as conn:
            for r in rows:
                ticker = str(r["ticker"]).upper()
                conn.execute(
                    "DELETE FROM wheelability WHERE ticker = ? AND as_of = ?",
                    (ticker, day),
                )
                values = (ticker, day) + tuple(
                    r.get(c) for c in _WHEEL_COLS[2:]
                )
                conn.execute(
                    f"INSERT INTO wheelability ({', '.join(_WHEEL_COLS)}) "
                    f"VALUES ({', '.join('?' * len(_WHEEL_COLS))})",
                    values,
                )
    except sqlite3.Error:
        return


def _latest_rows(table: str, cols: tuple[str, ...]) -> list[dict]:
    """Latest row per ticker (by as_of) from `table`."""
    try:
        with _connect() as conn:
            cur = conn.execute(
                f"""
                SELECT {', '.join(cols)} FROM {table} t
                WHERE as_of = (
                    SELECT MAX(as_of) FROM {table} t2 WHERE t2.ticker = t.ticker
                )
                """
            )
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.Error:
        return []


def latest_resilience() -> list[dict]:
    """Latest resilience row per ticker across the whole cache."""
    return _latest_rows("resilience", _RESILIENCE_COLS)


def latest_wheelability() -> list[dict]:
    """Latest wheelability row per ticker across the whole cache."""
    return _latest_rows("wheelability", _WHEEL_COLS)


def last_scan_date() -> str | None:
    """ISO date of the most recent resilience scan, or None if the
    cache has never been built."""
    try:
        with _connect() as conn:
            row = conn.execute("SELECT MAX(as_of) FROM resilience").fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None
