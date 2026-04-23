"""
Weekly convergence scoring and historical hit rate for IntraWeek setups.

Convergence: checks 7 daily indicators for LONG alignment (weekly hold).
Hit rate: "has this pattern delivered 10%+ in the last 6 months?"
"""

import numpy as np
import pandas as pd

from common.indicators import compute_atr
from intraday.features import compute_ema, compute_rsi, compute_macd


def compute_weekly_convergence(daily_df, symbol_regime=None, nifty_daily=None):
    """Check how many daily indicators agree with a LONG weekly hold.

    7 dimensions checked:
    1. Close > 20-EMA — trend support
    2. RSI(14) — not overbought (< 70) and recovering
    3. MACD histogram — positive or rising
    4. EMA alignment — 9 > 20 > 50 on daily
    5. Volume trend — recent volume > 1.2x 20-day median
    6. Weekly trend — from symbol_regime
    7. Higher low forming — price structure

    Returns {score: 0-100, aligned: [...], conflicting: [...], n_aligned, total: 7}
    """
    if daily_df.empty or len(daily_df) < 50:
        return {"score": 0, "aligned": [], "conflicting": [], "n_aligned": 0, "total": 0}

    close = daily_df["Close"]
    price = float(close.iloc[-1])
    aligned = []
    conflicting = []

    # 1. Close > 20-EMA
    ema20 = float(compute_ema(close, 20).iloc[-1])
    if price > ema20:
        aligned.append("above_20EMA")
    else:
        conflicting.append("above_20EMA")

    # 2. RSI(14) — healthy recovery zone (25-65 for mean reversion, not overbought)
    rsi = compute_rsi(close, 14)
    if not rsi.empty and not np.isnan(rsi.iloc[-1]):
        rsi_val = float(rsi.iloc[-1])
        if rsi_val < 70:  # not overbought
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

    # 4. EMA alignment — 9 > 20 > 50
    ema9 = float(compute_ema(close, 9).iloc[-1])
    ema50 = float(compute_ema(close, 50).iloc[-1])
    if ema9 > ema20 > ema50:
        aligned.append("EMA_alignment")
    else:
        conflicting.append("EMA_alignment")

    # 5. Volume trend — recent vs 20d median
    if len(daily_df) >= 21:
        recent_vol = float(daily_df["Volume"].iloc[-3:].mean())
        median_vol = float(daily_df["Volume"].iloc[-21:-3].median())
        if median_vol > 0 and recent_vol > 1.2 * median_vol:
            aligned.append("volume_trend")
        else:
            conflicting.append("volume_trend")

    # 6. Weekly trend — from symbol regime if available
    if symbol_regime:
        weekly = symbol_regime.get("weekly_trend", "sideways")
        if weekly in ("up",):
            aligned.append("weekly_trend")
        elif weekly == "down":
            conflicting.append("weekly_trend")
        else:
            conflicting.append("weekly_trend")  # sideways = not confirming
    else:
        # Fallback: check 5-day vs 20-day price action
        if len(close) >= 20:
            ret_5d = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0
            ret_20d = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else 0
            if ret_5d > 0 and ret_20d > 0:
                aligned.append("weekly_trend")
            else:
                conflicting.append("weekly_trend")

    # 7. Higher low forming — last 2 lows ascending
    if len(daily_df) >= 10:
        recent_lows = daily_df["Low"].iloc[-5:]
        prior_lows = daily_df["Low"].iloc[-10:-5]
        if float(recent_lows.min()) > float(prior_lows.min()):
            aligned.append("higher_low")
        else:
            conflicting.append("higher_low")

    total = len(aligned) + len(conflicting)
    n_aligned = len(aligned)
    score = round(n_aligned / total * 100, 1) if total > 0 else 0

    return {
        "score": score,
        "aligned": aligned,
        "conflicting": conflicting,
        "n_aligned": n_aligned,
        "total": total,
    }


def compute_weekly_hit_rate(daily_df, lookback_months=6):
    """Historical hit rate: when similar oversold conditions existed,
    what % of stocks delivered 10%+ in the next 5 trading days?

    Scans daily data for prior instances of:
    - RSI < 35 AND 3-day drawdown > 5%
    Then checks the subsequent 5-day return.

    Returns {hit_rate_10pct, hit_rate_20pct, avg_return, n_samples}
    """
    result = {"hit_rate_10pct": 0, "hit_rate_20pct": 0, "avg_return": 0, "n_samples": 0}

    if daily_df.empty or len(daily_df) < 60:
        return result

    close = daily_df["Close"]
    rsi = compute_rsi(close, 14)

    # Lookback window
    lookback_days = lookback_months * 21  # approx trading days
    start_idx = max(20, len(daily_df) - lookback_days)

    signals = []
    for i in range(start_idx, len(daily_df) - 5):
        rsi_val = float(rsi.iloc[i]) if not np.isnan(rsi.iloc[i]) else 50
        # 3-day drawdown
        if i < 3:
            continue
        dd = (float(close.iloc[i - 3]) - float(close.iloc[i])) / float(close.iloc[i - 3]) * 100

        if rsi_val < 35 and dd > 5:
            # Forward 5-day return
            fwd_return = (float(close.iloc[i + 5]) / float(close.iloc[i]) - 1) * 100
            signals.append(fwd_return)

    if not signals:
        return result

    arr = np.array(signals)
    result["n_samples"] = len(arr)
    result["hit_rate_10pct"] = round(float(np.mean(arr >= 10)) * 100, 1)
    result["hit_rate_20pct"] = round(float(np.mean(arr >= 20)) * 100, 1)
    result["avg_return"] = round(float(np.mean(arr)), 2)

    return result
