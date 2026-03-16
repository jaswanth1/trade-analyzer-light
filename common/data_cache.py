"""
Supabase-backed OHLCV cache layer.

Caches fetched candle data to avoid repeated API calls across scanners.
Degrades gracefully — if Supabase is unreachable, returns empty and lets
callers fall through to direct API calls.

Table: ohlcv_cache
  (symbol, interval, bar_time) PRIMARY KEY
  open, high, low, close, volume, fetched_at
"""

from datetime import datetime, timedelta, timezone

import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
BATCH_SIZE = 500


# ── Helpers ──────────────────────────────────────────────────────────────

_supa_ok_cache: tuple[float, bool] | None = None

def _supa_ok() -> bool:
    """Check if Supabase is reachable (cached for 60s on failure, 300s on success)."""
    global _supa_ok_cache
    import time as _time
    if _supa_ok_cache is not None:
        ts, ok = _supa_ok_cache
        ttl = 300 if ok else 60
        if _time.monotonic() - ts < ttl:
            return ok
    try:
        from common.db import _supabase_available
        ok = _supabase_available()
    except Exception:
        ok = False
    _supa_ok_cache = (_time.monotonic(), ok)
    return ok


def _now_ist() -> datetime:
    return datetime.now(IST)


def _market_closed() -> bool:
    """True if current IST time is outside 9:15–15:30."""
    now = _now_ist()
    t = now.time()
    from datetime import time as dtime
    return t < dtime(9, 15) or t > dtime(15, 30)


# ── Cache Read ───────────────────────────────────────────────────────────

def get_cached_bars(
    symbol: str, interval: str,
    from_time: datetime | None = None,
    to_time: datetime | None = None,
) -> pd.DataFrame:
    """Fetch cached OHLCV bars from Supabase.

    Returns DataFrame with standard columns + DatetimeIndex, or empty DataFrame.
    """
    if not _supa_ok():
        return pd.DataFrame()

    try:
        from common.db import _get_cursor

        conditions = ["symbol = %s", "interval = %s"]
        params = [symbol, interval]

        if from_time:
            conditions.append("bar_time >= %s")
            params.append(from_time.isoformat())
        if to_time:
            conditions.append("bar_time <= %s")
            params.append(to_time.isoformat())

        where = " AND ".join(conditions)
        sql = f"SELECT bar_time, open, high, low, close, volume FROM ohlcv_cache WHERE {where} ORDER BY bar_time"

        cur = _get_cursor()
        cur.execute(sql, params)

        if not cur.description:
            return pd.DataFrame()

        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=cols)
        df["bar_time"] = pd.to_datetime(df["bar_time"], utc=True)
        df = df.set_index("bar_time").sort_index()
        df.index = df.index.tz_convert(IST)
        df.columns = ["Open", "High", "Low", "Close", "Volume"]
        return df

    except Exception as e:
        print(f"  [WARN] Cache read failed for {symbol}/{interval}: {e}")
        return pd.DataFrame()


# ── Cache Write ──────────────────────────────────────────────────────────

def cache_bars(symbol: str, interval: str, df: pd.DataFrame):
    """Upsert OHLCV bars into Supabase cache. Silent on failure."""
    if df.empty or not _supa_ok():
        return

    try:
        from common.db import _get_cursor

        # Normalize column names
        col_map = {c.lower(): c for c in df.columns}
        open_col = col_map.get("open", "Open")
        high_col = col_map.get("high", "High")
        low_col = col_map.get("low", "Low")
        close_col = col_map.get("close", "Close")
        vol_col = col_map.get("volume", "Volume")

        rows = []
        for idx, row in df.iterrows():
            ts = idx
            if hasattr(ts, 'isoformat'):
                ts_str = ts.isoformat()
            else:
                ts_str = str(ts)
            rows.append((
                symbol, interval, ts_str,
                float(row[open_col]), float(row[high_col]),
                float(row[low_col]), float(row[close_col]),
                int(row[vol_col]),
            ))

        cur = _get_cursor()

        # Batch upsert
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            args = ",".join(
                cur.mogrify(
                    "(%s, %s, %s, %s, %s, %s, %s, %s)", r
                ).decode() for r in batch
            )
            cur.execute(
                f"INSERT INTO ohlcv_cache (symbol, interval, bar_time, open, high, low, close, volume) "
                f"VALUES {args} "
                f"ON CONFLICT (symbol, interval, bar_time) "
                f"DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, "
                f"low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, "
                f"fetched_at=NOW()"
            )

    except Exception as e:
        print(f"  [WARN] Cache write failed for {symbol}/{interval}: {e}")


# ── Cache Freshness ──────────────────────────────────────────────────────

def get_cache_freshness(symbol: str, interval: str) -> datetime | None:
    """Return the latest bar_time in cache for this symbol+interval."""
    if not _supa_ok():
        return None

    try:
        from common.db import _get_cursor
        cur = _get_cursor()
        cur.execute(
            "SELECT MAX(bar_time) FROM ohlcv_cache WHERE symbol = %s AND interval = %s",
            [symbol, interval],
        )
        row = cur.fetchone()
        if row and row[0]:
            ts = row[0]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        return None
    except Exception:
        return None


def is_cache_fresh(symbol: str, interval: str, max_age_seconds: int = 60) -> bool:
    """True if latest cached bar is within max_age_seconds of now.

    Cache TTL logic:
    - Intraday (5m, 1m): fresh if last bar < max_age_seconds old (during market hours)
    - Daily (1d): fresh if we have today's bar and market is closed
    - Historical bars older than today: always fresh (history doesn't change)
    """
    latest = get_cache_freshness(symbol, interval)
    if latest is None:
        return False

    now = _now_ist()
    latest_ist = latest.astimezone(IST) if latest.tzinfo else latest.replace(tzinfo=IST)

    if interval in ("1m", "2m", "5m", "15m", "30m"):
        # Intraday: fresh if within max_age_seconds AND market is open
        if _market_closed():
            # After market close, today's intraday data is complete
            return latest_ist.date() == now.date()
        age = (now - latest_ist).total_seconds()
        return age < max_age_seconds

    elif interval in ("1d", "1wk", "1mo"):
        # Daily: fresh if we have today's bar and market is closed
        if latest_ist.date() == now.date() and _market_closed():
            return True
        # If we have yesterday's bar and market hasn't opened yet
        if latest_ist.date() == (now - timedelta(days=1)).date() and now.time() < datetime.strptime("09:15", "%H:%M").time():
            return True
        return False

    return False
