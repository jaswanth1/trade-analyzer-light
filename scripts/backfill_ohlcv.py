"""
One-time backfill: populate TimescaleDB ohlcv_cache with 365 days of clean data.

Fetches 1d and 5m bars for all 122 hardcoded symbols from yfinance,
writing through the existing cache_bars() adapter.

Usage:
    python -m scripts.backfill_ohlcv
    python -m scripts.backfill_ohlcv --workers 5     # fewer threads
    python -m scripts.backfill_ohlcv --symbols RELIANCE.NS SBIN.NS  # specific symbols
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from common.data import _HARDCODED_TICKERS
from common.data_cache import cache_bars, IST

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

WINDOW_DAYS = 59  # yfinance 5m limit ~60 days; use 59 for safety


def _fetch_1d(symbol: str) -> int:
    """Fetch 1 year of daily bars and cache. Returns row count."""
    try:
        df = yf.download(symbol, period="1y", interval="1d", progress=False)
        if df.empty:
            return 0
        # yfinance returns MultiIndex columns when single symbol; flatten
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        cache_bars(symbol, "1d", df)
        return len(df)
    except Exception as e:
        log.warning("1d fetch failed for %s: %s", symbol, e)
        return 0


def _fetch_5m(symbol: str) -> int:
    """Fetch ~365 days of 5m bars in 59-day windows. Returns total row count."""
    total = 0
    now = datetime.now(timezone.utc)

    # Walk backwards in 59-day windows
    for i in range(7):  # 7 × 59 = 413 days coverage
        end = now - timedelta(days=i * WINDOW_DAYS)
        start = end - timedelta(days=WINDOW_DAYS)

        # Don't go beyond 365 days
        cutoff = now - timedelta(days=365)
        if start < cutoff:
            start = cutoff

        if start >= end:
            break

        try:
            df = yf.download(
                symbol,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="5m",
                progress=False,
            )
            if df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            cache_bars(symbol, "5m", df)
            total += len(df)
        except Exception as e:
            log.warning("5m fetch failed for %s (window %d): %s", symbol, i, e)

        # Small delay between windows to be kind to yfinance
        time.sleep(0.5)

    return total


def _backfill_symbol(symbol: str) -> dict:
    """Backfill one symbol (1d + 5m). Returns stats dict."""
    rows_1d = _fetch_1d(symbol)
    rows_5m = _fetch_5m(symbol)
    return {"symbol": symbol, "1d": rows_1d, "5m": rows_5m}


def main():
    parser = argparse.ArgumentParser(description="Backfill ohlcv_cache from yfinance")
    parser.add_argument("--workers", type=int, default=10, help="Thread pool size")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to backfill")
    args = parser.parse_args()

    symbols = args.symbols or list(_HARDCODED_TICKERS.keys())
    log.info("Backfilling %d symbols with %d workers", len(symbols), args.workers)

    start_time = time.monotonic()
    done = 0
    failed = []
    total_1d = 0
    total_5m = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_backfill_symbol, sym): sym for sym in symbols}

        for future in as_completed(futures):
            sym = futures[future]
            done += 1
            try:
                result = future.result()
                total_1d += result["1d"]
                total_5m += result["5m"]
                if result["1d"] == 0 and result["5m"] == 0:
                    failed.append(sym)
                    log.warning("[%d/%d] %s — no data", done, len(symbols), sym)
                else:
                    log.info(
                        "[%d/%d] %s — 1d: %d, 5m: %d",
                        done, len(symbols), sym, result["1d"], result["5m"],
                    )
            except Exception as e:
                failed.append(sym)
                log.error("[%d/%d] %s — error: %s", done, len(symbols), sym, e)

    elapsed = time.monotonic() - start_time
    log.info("=" * 60)
    log.info("Backfill complete in %.1f minutes", elapsed / 60)
    log.info("Total rows: 1d=%d, 5m=%d, combined=%d", total_1d, total_5m, total_1d + total_5m)
    if failed:
        log.warning("Failed symbols (%d): %s", len(failed), ", ".join(failed))

    # Compress old chunks
    log.info("Compressing old chunks...")
    try:
        from common.db import _get_cursor
        cur = _get_cursor()
        cur.execute("""
            SELECT compress_chunk(c, if_not_compressed => true)
            FROM show_chunks('ohlcv_cache', older_than => INTERVAL '7 days') c;
        """)
        log.info("Compression complete")
    except Exception as e:
        log.warning("Chunk compression failed (may need to run manually): %s", e)


if __name__ == "__main__":
    main()
