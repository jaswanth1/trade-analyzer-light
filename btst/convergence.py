"""
Daily convergence scoring and overnight historical hit rate for BTST setups.

Convergence: checks 7 daily indicators for LONG alignment (BTST is always long).
Hit rate: "has this exact BTST pattern worked in the last 6 months?"
"""

import numpy as np
import pandas as pd

from intraday.features import compute_ema, compute_rsi, compute_macd


def compute_daily_convergence(daily_df, symbol_regime, nifty_daily=None):
    """Check how many daily indicators agree with a LONG overnight hold.

    7 dimensions checked:
    1. Close > 50-EMA — bullish structure
    2. RSI(14) 40-70 — healthy long zone
    3. MACD histogram — positive or rising
    4. EMA alignment — 9 > 20 > 50 on daily
    5. Volume trend — today's volume > 1.2× 20-day median
    6. Weekly trend — from symbol_regime
    7. Relative strength — from symbol_regime

    Returns {score: 0-100, aligned: [...], conflicting: [...], n_aligned, total}
    """
    if daily_df.empty or len(daily_df) < 50:
        return {"score": 0, "aligned": [], "conflicting": [], "n_aligned": 0, "total": 0}

    close = daily_df["Close"]
    price = float(close.iloc[-1])
    aligned = []
    conflicting = []

    # 1. Close > 50-EMA
    ema50 = float(compute_ema(close, 50).iloc[-1])
    if price > ema50:
        aligned.append("above_50EMA")
    else:
        conflicting.append("above_50EMA")

    # 2. RSI(14) in 40-70 (healthy long zone)
    rsi = compute_rsi(close, 14)
    if not rsi.empty and not np.isnan(rsi.iloc[-1]):
        rsi_val = float(rsi.iloc[-1])
        if 40 <= rsi_val <= 70:
            aligned.append("RSI")
        else:
            conflicting.append("RSI")

    # 3. MACD histogram — positive or rising
    macd = compute_macd(close)
    hist = macd["histogram"]
    if len(hist) >= 2 and not hist.iloc[-2:].isna().any():
        hist_positive = float(hist.iloc[-1]) > 0
        hist_rising = float(hist.iloc[-1]) > float(hist.iloc[-2])
        if hist_positive or hist_rising:
            aligned.append("MACD")
        else:
            conflicting.append("MACD")

    # 4. EMA alignment: 9 > 20 > 50
    ema9 = float(compute_ema(close, 9).iloc[-1])
    ema20 = float(compute_ema(close, 20).iloc[-1])
    if ema9 > ema20 > ema50:
        aligned.append("EMA_align")
    else:
        conflicting.append("EMA_align")

    # 5. Volume trend: today > 1.2× 20-day median
    if "Volume" in daily_df.columns and len(daily_df) >= 21:
        today_vol = float(daily_df["Volume"].iloc[-1])
        median_vol = float(daily_df["Volume"].iloc[-21:-1].median())
        if median_vol > 0 and today_vol > 1.2 * median_vol:
            aligned.append("volume")
        elif median_vol > 0 and today_vol < 0.8 * median_vol:
            conflicting.append("volume")
        # neutral: neither aligned nor conflicting

    # 6. Weekly trend from symbol_regime
    weekly = symbol_regime.get("weekly_trend", "sideways")
    if weekly == "up":
        aligned.append("weekly_trend")
    elif weekly == "down":
        conflicting.append("weekly_trend")

    # 7. Relative strength from symbol_regime
    rs = symbol_regime.get("relative_strength", "inline")
    if rs == "outperforming":
        aligned.append("rel_strength")
    elif rs == "underperforming":
        conflicting.append("rel_strength")

    total = len(aligned) + len(conflicting)
    score = round(len(aligned) / max(total, 1) * 100)

    return {
        "score": score,
        "aligned": aligned,
        "conflicting": conflicting,
        "n_aligned": len(aligned),
        "total": total,
    }


def compute_overnight_hit_rate(daily_df, dow, month_period):
    """Scan 6 months of daily data for similar BTST setups.

    Matches: bullish close (close > open, close_position >= 0.8) on same DOW + month_period.
    Checks if next-day close was profitable.

    Args:
        daily_df: 6-month daily OHLCV data
        dow: int (0=Monday..4=Friday)
        month_period: str ("begin", "mid", "end", "expiry_week")

    Returns {hit_rate, sample_size, avg_return, context: str}
    """
    from common.analysis_cache import get_cached, set_cached, TTL_DAILY
    symbol = getattr(daily_df, "name", "") or ""
    params = f"{dow}|{month_period}"
    if symbol:
        cached = get_cached("overnight_hit_rate", symbol=symbol, params=params, max_age_seconds=TTL_DAILY)
        if cached is not None:
            return cached

    if daily_df.empty or len(daily_df) < 60:
        return {"hit_rate": 0, "sample_size": 0, "avg_return": 0,
                "context": "Insufficient data"}

    from intraday.regime import classify_month_period

    df = daily_df.copy()
    df["day_range"] = df["High"] - df["Low"]
    df["close_position"] = np.where(
        df["day_range"] > 0,
        (df["Close"] - df["Low"]) / df["day_range"],
        0.5,
    )
    df["bullish_close"] = (df["Close"] > df["Open"]) & (df["close_position"] >= 0.8)
    df["next_close"] = df["Close"].shift(-1)
    df["overnight_return"] = (df["next_close"] - df["Close"]) / df["Close"] * 100
    df = df.dropna(subset=["overnight_return"])

    # Filter to matching DOW + month_period + bullish close
    df["dow"] = df.index.dayofweek
    df["month_period"] = df.index.map(lambda dt: classify_month_period(dt))

    matches = df[
        (df["bullish_close"]) &
        (df["dow"] == dow) &
        (df["month_period"] == month_period)
    ]

    if matches.empty:
        return {"hit_rate": 0, "sample_size": 0, "avg_return": 0,
                "context": "No matching BTST setups found"}

    returns = matches["overnight_return"]
    wins = (returns > 0).sum()
    sample_size = len(returns)
    hit_rate = round(wins / sample_size * 100, 1)
    avg_return = round(float(returns.mean()), 3)

    dow_name = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}.get(dow, "?")
    context = (f"{hit_rate}% win on {sample_size} bullish-close BTST setups "
               f"({dow_name}, {month_period})")

    result = {
        "hit_rate": hit_rate,
        "sample_size": sample_size,
        "avg_return": avg_return,
        "context": context,
    }
    if symbol:
        set_cached("overnight_hit_rate", result, symbol=symbol, params=params)
    return result
