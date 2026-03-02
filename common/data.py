"""
Common data fetching utilities and shared constants.

Data strategy (transparent to callers):
  1. Check Supabase cache — if fresh, return cached data (zero API calls)
  2. Fetch from yfinance (free, reliable for historical)
  3. If yfinance data is stale (>1 min), fill gap with Upstox real-time
  4. Cache result in Supabase for future calls
  5. Degrade gracefully if Upstox/Supabase unavailable
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

IST = timezone(timedelta(hours=5, minutes=30))

# ── Project paths ─────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent

# Shared / common outputs stay in project root
DB_PATH = PROJECT_ROOT / "scalp_journal.db"

# Module-specific paths — each scanner's outputs live inside its own package
SCALP_DIR = PROJECT_ROOT / "scalp"
SCALP_OUTPUT_DIR = SCALP_DIR / "output"
SCALP_CONFIG_PATH = SCALP_DIR / "scalp_config.yaml"
SCALP_REPORT_DIR = SCALP_DIR / "reports"

BTST_DIR = PROJECT_ROOT / "btst"
BTST_REPORT_DIR = BTST_DIR / "reports"

INTRADAY_DIR = PROJECT_ROOT / "intraday"
INTRADAY_REPORT_DIR = INTRADAY_DIR / "reports"

# Legacy aliases (for backwards compat during transition)
OUTPUT_DIR = SCALP_OUTPUT_DIR
CONFIG_PATH = SCALP_CONFIG_PATH

# ── Configuration ─────────────────────────────────────────────────────────

TICKERS = {
    # ── Financials ──
    "SAILIFE.NS":     {"sector": "^CNXFIN",    "name": "SBI Life Insurance"},
    "KFINTECH.NS":    {"sector": "^CNXFIN",    "name": "KFin Technologies"},
    "CAMS.NS":        {"sector": "^CNXFIN",    "name": "Computer Age Mgmt Services"},
    "IDBI.NS":        {"sector": "^CNXFIN",    "name": "IDBI Bank"},
    "BSE.NS":         {"sector": "^CNXFIN",    "name": "BSE Limited"},
    "PFC.NS":         {"sector": "^CNXFIN",    "name": "Power Finance Corp"},
    "ABCAPITAL.NS":   {"sector": "^CNXFIN",    "name": "Aditya Birla Capital"},
    "INDIANB.NS":     {"sector": "^CNXFIN",    "name": "Indian Bank"},
    # ── Energy ──
    "TATAPOWER.NS":   {"sector": "^CNXENERGY", "name": "Tata Power"},
    "ADANIPOWER.NS":  {"sector": "^CNXENERGY", "name": "Adani Power"},
    "NTPC.NS":        {"sector": "^CNXENERGY", "name": "NTPC"},
    "COALINDIA.NS":   {"sector": "^CNXENERGY", "name": "Coal India"},
    # ── Metals & Commodities ──
    "GPIL.NS":        {"sector": "^CNXMETAL",  "name": "Godawari Power & Ispat"},
    "ADANIENT.NS":    {"sector": "^CNXMETAL",  "name": "Adani Enterprises"},
    "GRAPHITE.NS":    {"sector": "^CNXMETAL",  "name": "Graphite India"},
    # ── PSE / Defence ──
    "BEL.NS":         {"sector": "^CNXPSE",    "name": "Bharat Electronics"},
    "BHEL.NS":        {"sector": "^CNXPSE",    "name": "Bharat Heavy Electricals"},
    "HAL.NS":         {"sector": "^CNXPSE",    "name": "Hindustan Aeronautics"},
    "DATAPATTNS.NS":  {"sector": "^CNXPSE",    "name": "Data Patterns"},
    "MTARTECH.NS":    {"sector": "^CNXPSE",    "name": "MTAR Technologies"},
    "SCI.NS":         {"sector": "^CNXPSE",    "name": "Shipping Corp of India"},
    "RVNL.NS":        {"sector": "^CNXPSE",    "name": "Rail Vikas Nigam"},
    # ── Infra ──
    "ADANIPORTS.NS":  {"sector": "^CNXINFRA",  "name": "Adani Ports"},
    "NBCC.NS":        {"sector": "^CNXINFRA",  "name": "NBCC (India)"},
    "FINCABLES.NS":   {"sector": "^CNXINFRA",  "name": "Finolex Cables"},
    "CUMMINSIND.NS":  {"sector": "^CNXINFRA",  "name": "Cummins India"},
    "HAVELLS.NS":     {"sector": "^CNXINFRA",  "name": "Havells India"},
    # ── IT ──
    "NETWEB.NS":      {"sector": "^CNXIT",     "name": "Netweb Technologies"},
    # ── Auto ──
    "EXIDEIND.NS":    {"sector": "^CNXAUTO",   "name": "Exide Industries"},
    # ── FMCG / Consumer ──
    "VBL.NS":         {"sector": "^CNXFMCG",   "name": "Varun Beverages"},
    "TRENT.NS":       {"sector": "^CNXFMCG",   "name": "Trent"},
    # ── Realty ──
    "ANANTRAJ.NS":    {"sector": "^CNXREALTY",  "name": "Anant Raj"},
    # ── Pharma ──
    "GLENMARK.NS":    {"sector": "^CNXPHARMA", "name": "Glenmark Pharmaceuticals"},
    # ── Industrial / Other ──
    "AEROFLEX.NS":    {"sector": "^CNXMETAL",  "name": "Aeroflex Industries"},
}

BENCHMARK = "^NSEI"  # Nifty 50

IST_WINDOWS = [
    ("09:15", "10:00"),
    ("10:00", "11:30"),
    ("11:30", "12:30"),
    ("12:30", "13:30"),
    ("13:30", "14:30"),
    ("14:30", "15:15"),
]

GAP_THRESHOLDS = {"flat": 0.003, "small": 0.01}
TARGET_PCTS = [0.5, 1.0, 1.5, 2.0]
STOP_PCTS = [0.5, 1.0, 1.5]


# ── Data Fetching ─────────────────────────────────────────────────────────

def _fetch_yfinance(symbol, period, interval, max_retries=3):
    """Raw yfinance download with retries. No caching."""
    for attempt in range(max_retries):
        try:
            df = yf.download(symbol, period=period, interval=interval, progress=False)
            if df.empty:
                return pd.DataFrame()
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel("Ticker")
            return df
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  [WARN] Failed to fetch {symbol} ({period}/{interval}): {e}")
                return pd.DataFrame()


def _fill_realtime_gap(symbol, yf_df, interval):
    """Fill the gap between yfinance's last bar and now using Upstox.

    Returns combined DataFrame, or None if no gap-fill needed/possible.
    """
    from common.upstox import is_upstox_available, fetch_upstox_intraday
    from common.upstox_symbols import yf_to_upstox

    if not is_upstox_available():
        return None

    now_ist = datetime.now(IST)

    # No gap-fill outside market hours
    if now_ist.hour < 9 or (now_ist.hour == 9 and now_ist.minute < 15):
        return None
    if now_ist.hour > 15 or (now_ist.hour == 15 and now_ist.minute > 30):
        return None

    # Determine last bar time
    last_bar = yf_df.index[-1]
    if hasattr(last_bar, 'tz') and last_bar.tz is not None:
        last_bar_ist = last_bar.astimezone(IST)
    else:
        last_bar_ist = last_bar.tz_localize(IST) if hasattr(last_bar, 'tz_localize') else last_bar

    gap_seconds = (now_ist - last_bar_ist).total_seconds()
    if gap_seconds < 120:  # yfinance is fresh enough (<2 min)
        return None

    # Map symbol to Upstox instrument key
    upstox_key = yf_to_upstox(symbol)
    if not upstox_key:
        return None

    # Parse interval minutes
    interval_map = {"1m": 1, "2m": 2, "5m": 5, "15m": 15}
    interval_min = interval_map.get(interval, 5)

    # Fetch Upstox intraday candles for today
    upstox_df = fetch_upstox_intraday(upstox_key, interval_min)
    if upstox_df.empty:
        return None

    # Filter to bars AFTER yfinance's last bar
    # Align timezone for comparison
    if upstox_df.index.tz is not None and (not hasattr(last_bar, 'tz') or last_bar.tz is None):
        last_bar_compare = last_bar_ist
    else:
        last_bar_compare = last_bar

    gap_bars = upstox_df[upstox_df.index > last_bar_compare]
    if gap_bars.empty:
        return None

    # Make timezone-naive to match yfinance if needed
    if yf_df.index.tz is None and gap_bars.index.tz is not None:
        gap_bars = gap_bars.copy()
        gap_bars.index = gap_bars.index.tz_localize(None)
    elif yf_df.index.tz is not None and gap_bars.index.tz is None:
        gap_bars = gap_bars.copy()
        gap_bars.index = gap_bars.index.tz_localize(IST)

    combined = pd.concat([yf_df, gap_bars])
    combined = combined[~combined.index.duplicated(keep='last')]
    return combined.sort_index()


def _try_upstox_full(symbol, period, interval):
    """Full Upstox fallback when yfinance returns nothing.

    Returns DataFrame or None.
    """
    from common.upstox import is_upstox_available, fetch_upstox_intraday, fetch_upstox_historical
    from common.upstox_symbols import yf_to_upstox

    if not is_upstox_available():
        return None

    upstox_key = yf_to_upstox(symbol)
    if not upstox_key:
        return None

    today = datetime.now(IST).strftime("%Y-%m-%d")

    # Map (period, interval) → Upstox API calls
    if interval in ("1m", "2m", "5m", "15m"):
        interval_map = {"1m": 1, "2m": 2, "5m": 5, "15m": 15}
        return fetch_upstox_intraday(upstox_key, interval_map.get(interval, 5))

    elif interval == "1d":
        # Map period to date range
        period_days = {
            "5d": 5, "1mo": 30, "2mo": 60, "3mo": 90,
            "6mo": 180, "1y": 365, "2y": 730,
        }
        days = period_days.get(period, 180)
        from_date = (datetime.now(IST) - timedelta(days=days)).strftime("%Y-%m-%d")
        return fetch_upstox_historical(upstox_key, from_date, today, unit="day", interval=1)

    return None


def fetch_yf(symbol, period, interval, max_retries=3):
    """Download OHLCV data with cache → yfinance → Upstox gap-fill.

    Transparent to callers — same signature, same return type.
    """
    from common.data_cache import is_cache_fresh, get_cached_bars, cache_bars

    # 1. Check Supabase cache
    if is_cache_fresh(symbol, interval):
        cached = get_cached_bars(symbol, interval)
        if not cached.empty:
            return cached

    # 2. Fetch from yfinance
    yf_df = _fetch_yfinance(symbol, period, interval, max_retries)

    # 3. For intraday intervals, fill real-time gap with Upstox
    if interval in ("1m", "2m", "5m", "15m") and not yf_df.empty:
        filled = _fill_realtime_gap(symbol, yf_df, interval)
        if filled is not None:
            cache_bars(symbol, interval, filled)
            return filled

    # 4. If yfinance empty, try Upstox as full fallback
    if yf_df.empty:
        upstox_df = _try_upstox_full(symbol, period, interval)
        if upstox_df is not None and not upstox_df.empty:
            cache_bars(symbol, interval, upstox_df)
            return upstox_df

    # 5. Cache whatever we got and return
    if not yf_df.empty:
        cache_bars(symbol, interval, yf_df)
    return yf_df


def fetch_live_ltp(symbols: list[str]) -> dict[str, float]:
    """Batch LTP via Upstox. Falls back to yfinance last close.

    Returns {yf_symbol: price} dict.
    """
    from common.upstox import is_upstox_available, fetch_upstox_ltp
    from common.upstox_symbols import yf_to_upstox, upstox_to_yf

    result = {}

    # Try Upstox batch LTP
    if is_upstox_available():
        upstox_keys = {}
        for sym in symbols:
            key = yf_to_upstox(sym)
            if key:
                upstox_keys[sym] = key

        if upstox_keys:
            ltp_data = fetch_upstox_ltp(list(upstox_keys.values()))
            for sym, key in upstox_keys.items():
                if key in ltp_data:
                    result[sym] = ltp_data[key]

    # Fall back to yfinance for missing symbols
    missing = [s for s in symbols if s not in result]
    for sym in missing:
        try:
            info = yf.Ticker(sym).fast_info
            if hasattr(info, 'last_price') and info.last_price:
                result[sym] = float(info.last_price)
        except Exception:
            pass

    return result


def fetch_ticker_info(symbol):
    """Fetch ticker info dict, return empty dict on failure."""
    try:
        return yf.Ticker(symbol).info or {}
    except Exception:
        return {}
