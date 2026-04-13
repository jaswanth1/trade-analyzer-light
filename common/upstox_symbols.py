"""
yfinance ↔ Upstox instrument key mapping.

yfinance uses "RELIANCE.NS", Upstox uses "NSE_EQ|INE002A01018" (exchange|ISIN).
Mapping source: Upstox BOD instruments JSON (refreshed daily at ~6 AM by Upstox).

Cache hierarchy (checked in order):
  1. Supabase `upstox_instruments` table — shared across machines, survives restarts
  2. Local JSON file (~/.upstox_instruments.json) — fallback if Supabase is down
  3. HTTP fetch from Upstox CDN — last resort, downloads ~2 MB gzipped file
"""

import gzip
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path

# ── Index Map (hardcoded — these don't change) ──────────────────────────

INDEX_MAP = {
    "^NSEI": "NSE_INDEX|Nifty 50",
    "^NSEBANK": "NSE_INDEX|Nifty Bank",
    "^INDIAVIX": "NSE_INDEX|India VIX",
    "^CNXFIN": "NSE_INDEX|Nifty Financial Services",
    "^CNXENERGY": "NSE_INDEX|Nifty Energy",
    "^CNXIT": "NSE_INDEX|Nifty IT",
    "^CNXPHARMA": "NSE_INDEX|Nifty Pharma",
    "^CNXAUTO": "NSE_INDEX|Nifty Auto",
    "^CNXFMCG": "NSE_INDEX|Nifty FMCG",
    "^CNXMETAL": "NSE_INDEX|Nifty Metal",
    "^CNXREALTY": "NSE_INDEX|Nifty Realty",
    "^CNXPSE": "NSE_INDEX|Nifty PSE",
    "^CNXINFRA": "NSE_INDEX|Nifty Infrastructure",
}

_INDEX_MAP_REV = {v: k for k, v in INDEX_MAP.items()}

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
LOCAL_CACHE_PATH = Path.home() / ".upstox_instruments.json"


# ── Supabase Cache ───────────────────────────────────────────────────────

def _load_from_supabase() -> dict[str, str] | None:
    """Load today's instruments from Supabase. Returns {trading_symbol: instrument_key} or None."""
    try:
        from common.db import _get_cursor
        cur = _get_cursor()
        cur.execute(
            "SELECT trading_symbol, instrument_key FROM upstox_instruments "
            "WHERE fetched_date = CURRENT_DATE"
        )
        rows = cur.fetchall()
        if not rows:
            return None
        return {r[0]: r[1] for r in rows}
    except Exception:
        return None


def _save_to_supabase(instruments: list[dict]):
    """Bulk upsert BOD instruments into Supabase. Silent on failure."""
    try:
        from common.db import _get_cursor
        cur = _get_cursor()

        # Clear stale data (older than today)
        cur.execute("DELETE FROM upstox_instruments WHERE fetched_date < CURRENT_DATE")

        # Batch upsert in chunks of 500
        batch_size = 500
        for i in range(0, len(instruments), batch_size):
            batch = instruments[i:i + batch_size]
            args = ",".join(
                cur.mogrify(
                    "(%s, %s, %s, %s, %s, %s)",
                    (
                        item["trading_symbol"],
                        item["instrument_key"],
                        item.get("isin", ""),
                        str(item.get("exchange_token", "")),
                        item.get("lot_size", 1),
                        item.get("tick_size", 0.05),
                    ),
                ).decode()
                for item in batch
            )
            cur.execute(
                f"INSERT INTO upstox_instruments "
                f"(trading_symbol, instrument_key, isin, exchange_token, lot_size, tick_size) "
                f"VALUES {args} "
                f"ON CONFLICT (trading_symbol) DO UPDATE SET "
                f"instrument_key = EXCLUDED.instrument_key, "
                f"isin = EXCLUDED.isin, "
                f"exchange_token = EXCLUDED.exchange_token, "
                f"lot_size = EXCLUDED.lot_size, "
                f"tick_size = EXCLUDED.tick_size, "
                f"fetched_date = CURRENT_DATE"
            )
    except Exception as e:
        print(f"  [WARN] Supabase instruments save failed: {e}")


# ── Local File Cache ─────────────────────────────────────────────────────

def _load_from_local() -> dict[str, str] | None:
    """Load cached instrument map from local file. Returns None if stale or missing."""
    if not LOCAL_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(LOCAL_CACHE_PATH.read_text())
        cached_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
        if (datetime.now().date() - cached_date).days > 1:
            return None
        return data["mapping"]
    except Exception:
        return None


def _save_to_local(mapping: dict[str, str]):
    """Save instrument map to local JSON with timestamp."""
    data = {"date": datetime.now().strftime("%Y-%m-%d"), "mapping": mapping}
    LOCAL_CACHE_PATH.write_text(json.dumps(data, indent=2))


# ── HTTP Fetch (last resort) ────────────────────────────────────────────

def _fetch_bod_instruments() -> tuple[dict[str, str], list[dict]]:
    """Download Upstox NSE BOD instruments JSON.

    Returns:
        mapping: {trading_symbol: instrument_key} for NSE_EQ equities
        raw_items: list of dicts with full instrument details (for Supabase storage)
    """
    try:
        with urllib.request.urlopen(INSTRUMENTS_URL, timeout=15) as resp:
            data = json.loads(gzip.decompress(resp.read()))

        mapping = {}
        raw_items = []
        for item in data:
            if item.get("segment") == "NSE_EQ" and item.get("instrument_type") == "EQ":
                symbol = item.get("trading_symbol", "")
                key = item.get("instrument_key", "")
                if symbol and key:
                    mapping[symbol] = key
                    raw_items.append(item)
        return mapping, raw_items
    except Exception as e:
        print(f"  [WARN] Failed to fetch Upstox BOD instruments: {e}")
        return {}, []


# ── Main Loader ─────────────────────────────────────────────────────────

def _load_bod_map() -> dict[str, str]:
    """Load trading_symbol → instrument_key map with 3-tier cache.

    1. Supabase (today's data)
    2. Local JSON file (< 24h old)
    3. HTTP fetch from Upstox CDN → save to both Supabase + local
    """
    # Tier 1: Supabase
    mapping = _load_from_supabase()
    if mapping:
        return mapping

    # Tier 2: Local file
    mapping = _load_from_local()
    if mapping:
        return mapping

    # Tier 3: HTTP fetch
    mapping, raw_items = _fetch_bod_instruments()
    if mapping:
        _save_to_local(mapping)
        if raw_items:
            _save_to_supabase(raw_items)
    return mapping


def build_instrument_map(symbols: list[str]) -> dict[str, str]:
    """Build {yf_symbol: upstox_instrument_key} for a list of equity symbols.

    Uses the BOD instruments (trading_symbol → instrument_key) to map
    yfinance symbols (e.g. "RELIANCE.NS") to Upstox keys (e.g. "NSE_EQ|INE002A01018").
    """
    bod = _load_bod_map()
    if not bod:
        return {}

    mapping = {}
    for sym in symbols:
        if sym.startswith("^"):
            continue  # indices handled by INDEX_MAP
        trading_sym = sym.replace(".NS", "").replace(".BO", "")
        key = bod.get(trading_sym)
        if key:
            mapping[sym] = key
        else:
            print(f"  [WARN] {sym} not found in Upstox BOD instruments")
    return mapping


def load_instrument_map(symbols: list[str] | None = None) -> dict[str, str]:
    """Load instrument map: Supabase → local file → HTTP fetch.

    If symbols is provided, builds map for those symbols.
    Otherwise uses TICKERS from common.data.
    Skips entirely if Upstox is not configured (no API key).
    """
    # Don't bother if Upstox isn't configured
    api_key = os.environ.get("UPSTOX_API_KEY", "")
    api_secret = os.environ.get("UPSTOX_API_SECRET", "")
    if not api_key or not api_secret:
        return {}

    if symbols is None:
        from common.data import TICKERS
        symbols = list(TICKERS.keys())

    return build_instrument_map(symbols)


# ── Lookup Functions ─────────────────────────────────────────────────────

def yf_to_upstox(yf_symbol: str) -> str | None:
    """Convert a yfinance symbol to Upstox instrument key."""
    if yf_symbol in INDEX_MAP:
        return INDEX_MAP[yf_symbol]

    # Direct lookup against BOD map — avoids rebuilding full map + noisy warnings
    bod = _load_bod_map()
    if not bod:
        return None
    trading_sym = yf_symbol.replace(".NS", "").replace(".BO", "")
    return bod.get(trading_sym)


def upstox_to_yf(instrument_key: str) -> str | None:
    """Convert an Upstox instrument key to yfinance symbol."""
    if instrument_key in _INDEX_MAP_REV:
        return _INDEX_MAP_REV[instrument_key]

    # Reverse lookup against BOD map directly
    bod = _load_bod_map()
    rev = {v: f"{k}.NS" for k, v in bod.items()}
    return rev.get(instrument_key)
