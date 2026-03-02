"""Swing Continuation strategy (1-5 day hold).

Multi-day swing entry on daily breakout pullback.
"""

import numpy as np

from common.indicators import compute_atr
from intraday.features import compute_ema
from intraday.strategies._common import _build_result


def evaluate_swing(symbol, intra_ist, daily_df, symbol_regime):
    """Multi-day swing entry on daily breakout pullback.

    Upgrades:
    - Breakout confirmation uses daily Close above 20-day high (not just High spike)
    - swing_hold = True flag so scanner exempts from 15:00 hard exit
    - Position sizing uses portfolio capital (handled in scanner) with wider stop
    - Weekly trend veto: if weekly_trend == "down", skip (swing against weekly = low probability)
    - Tightened breakout staleness: within last 2 sessions, not 3
    """
    if daily_df.empty or len(daily_df) < 25 or intra_ist.empty:
        return None

    trend = symbol_regime.get("trend", "sideways")
    if trend not in ("strong_up", "mild_up"):
        return None

    # Weekly trend veto: swing against weekly = low probability
    weekly_trend = symbol_regime.get("weekly_trend", "sideways")
    if weekly_trend == "down":
        return None

    close = daily_df["Close"]
    high = daily_df["High"]
    low = daily_df["Low"]

    ltp = float(close.iloc[-1])
    atr = compute_atr(daily_df) if len(daily_df) >= 14 else np.nan
    if np.isnan(atr):
        return None

    # Check: daily *Close* above 20-day high within last 2 sessions (tightened from 3)
    close_20d_high = float(close.iloc[-25:-2].max()) if len(close) >= 25 else float(close.iloc[:-2].max())
    recent_closes = close.iloc[-2:]
    broke_out = any(float(c) > close_20d_high for c in recent_closes)

    if not broke_out:
        return None

    # Today's intraday pullback toward yesterday's close or 9 EMA
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else ltp
    ema9_daily = float(compute_ema(close, 9).iloc[-1])

    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]
    if today_bars.empty:
        return None

    intra_ltp = float(today_bars["Close"].iloc[-1])
    intra_low = float(today_bars["Low"].min())

    # Pulled back near prev close or 9 EMA
    support_level = min(prev_close, ema9_daily)
    pulled_back = intra_low <= support_level * 1.005

    # Currently above VWAP
    above_vwap = False
    if "vwap" in today_bars.columns:
        vwap_val = float(today_bars["vwap"].iloc[-1])
        above_vwap = intra_ltp > vwap_val if not np.isnan(vwap_val) else False

    conditions = {
        "breakout_confirmed": {"met": broke_out, "detail": f"Daily close above 20d high {close_20d_high:.2f}"},
        "pullback_to_support": {"met": pulled_back, "detail": f"Low {intra_low:.2f} near support {support_level:.2f}"},
        "above_vwap": {"met": above_vwap, "detail": "LTP vs VWAP"},
        "trend_strong": {"met": trend == "strong_up", "detail": f"Daily trend: {trend}"},
        "weekly_aligned": {"met": weekly_trend != "down", "detail": f"Weekly trend: {weekly_trend}"},
    }

    if not (broke_out and pulled_back):
        return None

    # Swing low for stop (lowest low of last 5 daily bars)
    swing_low = float(low.iloc[-5:].min())
    stop = swing_low - 0.1 * atr
    target = intra_ltp + atr * 1.5  # multi-day target (1.5x ATR)

    conf = 0.5
    if above_vwap:
        conf += 0.15
    if trend == "strong_up":
        conf += 0.15
    if pulled_back:
        conf += 0.1

    return _build_result(
        "swing", "long", intra_ltp, stop, target, min(conf, 0.95), conditions,
        f"Swing long: daily close breakout, pullback to {support_level:.2f}, target {target:.2f} (1.5x ATR)",
        swing_hold=True,  # exempt from 15:00 hard exit
    )
