"""
Overnight-adapted DOW/month-period statistics for BTST.

Re-uses classify_symbol_regime and classify_month_period from intraday.regime,
but computes overnight returns instead of intraday returns.
"""

import numpy as np
import pandas as pd

from intraday.regime import classify_symbol_regime, classify_month_period, DOW_NAMES


def compute_overnight_dow_month_stats(daily_df):
    """Historical overnight return stats broken down by DOW and month_period.

    Filters to bullish closes (close > open, close in top 30% of range)
    since that's the BTST entry pattern.

    For each matching historical day:
        overnight_return = (next_close - close) / close

    Group by (day_of_week, month_period):
        win_rate, avg_return, n_samples

    Returns nested dict: stats[dow_name][month_period] = {win_rate, avg_return, n}
    """
    symbol = getattr(daily_df, "name", "") or ""
    if symbol:
        from common.analysis_cache import get_cached, set_cached, TTL_DAILY
        cached = get_cached("overnight_dow_month_stats", symbol=symbol, max_age_seconds=TTL_DAILY)
        if cached is not None:
            return cached

    if daily_df.empty or len(daily_df) < 20:
        return {}

    df = daily_df.copy()

    # Compute overnight returns
    df["next_close"] = df["Close"].shift(-1)
    df["overnight_return"] = (df["next_close"] - df["Close"]) / df["Close"].replace(0, np.nan) * 100
    df = df.dropna(subset=["overnight_return"])

    # Filter to bullish closes (BTST entry pattern)
    df["day_range"] = df["High"] - df["Low"]
    df["close_position"] = np.where(
        df["day_range"] > 0,
        (df["Close"] - df["Low"]) / df["day_range"],
        0.5,
    )
    df = df[(df["Close"] > df["Open"]) & (df["close_position"] >= 0.7)]

    if df.empty:
        return {}

    df["dow"] = df.index.dayofweek
    df["month_period"] = df.index.map(lambda dt: classify_month_period(dt))

    stats = {}
    for dow in range(5):
        dow_name = DOW_NAMES[dow]
        stats[dow_name] = {}
        dow_data = df[df["dow"] == dow]

        for mp in ["begin", "mid", "end", "expiry_week"]:
            subset = dow_data[dow_data["month_period"] == mp]
            if len(subset) < 3:
                continue
            wins = (subset["overnight_return"] > 0).sum()
            stats[dow_name][mp] = {
                "win_rate": round(wins / len(subset) * 100, 1),
                "avg_return": round(float(subset["overnight_return"].mean()), 3),
                "n": len(subset),
            }

        # Overall DOW stats
        if len(dow_data) >= 3:
            wins = (dow_data["overnight_return"] > 0).sum()
            stats[dow_name]["all"] = {
                "win_rate": round(wins / len(dow_data) * 100, 1),
                "avg_return": round(float(dow_data["overnight_return"].mean()), 3),
                "n": len(dow_data),
            }

    # Overall stats
    if len(df) >= 10:
        wins = (df["overnight_return"] > 0).sum()
        stats["overall"] = {
            "win_rate": round(wins / len(df) * 100, 1),
            "avg_return": round(float(df["overnight_return"].mean()), 3),
            "n": len(df),
        }

    if symbol:
        from common.analysis_cache import set_cached, TTL_DAILY
        set_cached("overnight_dow_month_stats", stats, symbol=symbol)

    return stats
