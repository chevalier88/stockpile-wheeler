"""The scan universe: S&P 500 + S&P 400 (mid-cap) constituents.

Wikipedia's constituent tables are the simplest zero-new-dependency
source — `pandas.read_html` already pulls in `lxml`, which is an
existing `options-scanner` dependency used for chain parsing. The list
changes rarely (index reconstitutions are quarterly at most), so it's
cached to disk and only re-fetched on an explicit refresh or once the
cache is stale.

This is a *starting* universe, not a guarantee every ticker resolves —
some entries (recent IPOs re-added, class shares, SPAC leftovers) may
fail to fetch a price history or option chain downstream. Those are
dropped silently by the pipeline stages, not here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from stocks_shared.yahoo import normalize_ticker

_CACHE_PATH = Path(__file__).resolve().parents[2] / "cache" / "universe.json"
_STALE_DAYS = 30

_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_SP400_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"


@dataclass(frozen=True)
class UniverseEntry:
    ticker: str
    name: str
    sector: str
    index: str  # "S&P 500" | "S&P 400"


def _cache_path() -> Path:
    import os
    return Path(os.environ.get("OSC_UNIVERSE_CACHE", str(_CACHE_PATH)))


_HEADERS = {
    # Wikipedia 403s the bare urllib user-agent pandas.read_html(url) uses
    # by default; a normal browser UA is enough to get past it.
    "User-Agent": ("Mozilla/5.0 (compatible; stockpile-screener/1.0; "
                  "+https://github.com/)"),
}


def _parse_table(url: str, index_label: str,
                  ticker_col: str, name_col: str, sector_col: str) -> list[UniverseEntry]:
    import io

    import pandas as pd
    import requests

    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    # The constituent table is the first one on both pages that has a
    # ticker-like column; scan defensively rather than hardcode an index,
    # since Wikipedia has reordered tables on these pages before.
    for tbl in tables:
        cols = {str(c).strip(): c for c in tbl.columns}
        if ticker_col in cols and name_col in cols:
            sec_col = cols.get(sector_col)
            out = []
            for _, row in tbl.iterrows():
                raw_ticker = str(row[cols[ticker_col]]).strip()
                if not raw_ticker or raw_ticker.lower() == "nan":
                    continue
                out.append(UniverseEntry(
                    ticker=normalize_ticker(raw_ticker),
                    name=str(row[cols[name_col]]).strip(),
                    sector=str(row[sec_col]).strip() if sec_col else "",
                    index=index_label,
                ))
            return out
    return []


def fetch_sp500() -> list[UniverseEntry]:
    return _parse_table(_SP500_URL, "S&P 500", "Symbol", "Security", "GICS Sector")


def fetch_sp400() -> list[UniverseEntry]:
    return _parse_table(_SP400_URL, "S&P 400", "Symbol", "Security", "GICS Sector")


def _is_stale(fetched_at: str) -> bool:
    try:
        ts = datetime.fromisoformat(fetched_at)
    except (TypeError, ValueError):
        return True
    return date.today() - ts.date() > timedelta(days=_STALE_DAYS)


def load_universe(force_refresh: bool = False) -> list[UniverseEntry]:
    """Return the cached universe, refetching from Wikipedia when the
    cache is missing, corrupt, older than 30 days, or `force_refresh`.

    Never raises on a fetch failure once a prior cache exists — returns
    the (possibly stale) cached list instead, so a flaky Wikipedia fetch
    doesn't break the tab. Raises only on a cold cache with no fallback.
    """
    path = _cache_path()
    cached: dict | None = None
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None

    needs_fetch = force_refresh or cached is None or _is_stale(
        cached.get("fetched_at", ""))
    if not needs_fetch:
        return [UniverseEntry(**e) for e in cached["entries"]]

    try:
        entries = fetch_sp500() + fetch_sp400()
    except Exception:
        if cached is not None:
            return [UniverseEntry(**e) for e in cached["entries"]]
        raise

    if not entries:
        if cached is not None:
            return [UniverseEntry(**e) for e in cached["entries"]]
        return []

    # Dedup by ticker, preferring the first occurrence (S&P 500 entries
    # come first, matters for the rare ticker that moved index).
    seen: dict[str, UniverseEntry] = {}
    for e in entries:
        seen.setdefault(e.ticker, e)
    entries = list(seen.values())

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "entries": [e.__dict__ for e in entries],
    }, indent=2), encoding="utf-8")
    return entries
