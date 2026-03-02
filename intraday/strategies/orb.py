"""Opening Range Breakout (ORB) strategy.

Price breaks above/below the first 30-min range with confirmation.
"""

import numpy as np

from common.indicators import compute_atr
from intraday.features import (
    compute_rsi, compute_ema_slope, compute_ema, compute_opening_range,
    compute_intraday_levels, compute_cumulative_rvol,
)
from intraday.strategies._common import _build_result


def evaluate_orb(symbol, intra_ist, daily_df, opening_range, day_type, symbol_regime):
    """ORB: price breaks above/below the first 30-min range with confirmation.

    Upgrades:
    - Stop at OR low (long) / OR high (short) — proper structural level
    - 2-bar hold confirmation: breakout must hold for 2 consecutive closes
    - Time decay: -0.05 confidence per hour after 10:00
    - RSI filter: reject long if RSI > 80, short if RSI < 20
    - Cumulative RVOL instead of single-bar volume ratio
    - Extended cutoff to 13:00 (ORB continuation works until post-lunch)
    - EMA slope check: skip long if 5-bar EMA slope < 0
    """
    if not opening_range or intra_ist.empty or daily_df.empty:
        return None

    # FIX: Gap-and-fade conflict — ORB assumes continuation; gap_and_fade is reversal
    if day_type == "gap_and_fade":
        return None

    or_high = opening_range["or_high"]
    or_low = opening_range["or_low"]
    or_range = opening_range["or_range"]

    if or_range <= 0:
        return None

    atr = compute_atr(daily_df) if len(daily_df) >= 14 else or_range * 2
    if np.isnan(atr):
        atr = or_range * 2
    buffer = 0.15 * atr

    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]
    if len(today_bars) < 8:  # need bars after opening range + 2-bar hold
        return None

    ltp = float(today_bars["Close"].iloc[-1])

    # Extended cutoff to 13:00 — ORB continuation works until post-lunch
    current_time = today_bars.index[-1]
    if current_time.hour >= 13:
        return None

    # Cumulative RVOL (institutional participation)
    cum_rvol = compute_cumulative_rvol(intra_ist)
    current_rvol = float(cum_rvol.iloc[-1]) if not cum_rvol.empty and not np.isnan(cum_rvol.iloc[-1]) else 1.0
    volume_ok = current_rvol > 1.3

    # RSI filter
    rsi = compute_rsi(today_bars["Close"], 14)
    rsi_val = float(rsi.iloc[-1]) if not rsi.empty and not np.isnan(rsi.iloc[-1]) else 50

    # EMA slope check: skip long if 5-bar EMA slope < 0 (declining momentum breakout is noise)
    ema5 = compute_ema(today_bars["Close"], 5)
    ema5_slope = compute_ema_slope(ema5, lookback=5)

    # Time decay: confidence penalty after 10:00
    hours_after_10 = max(0, (current_time.hour - 10) + current_time.minute / 60)
    time_penalty = hours_after_10 * 0.05

    # 2-bar hold confirmation
    last_2 = today_bars["Close"].iloc[-2:]
    long_breakout = all(float(c) > or_high + buffer for c in last_2)
    short_breakout = all(float(c) < or_low - buffer for c in last_2)

    # FIX: Failed ORB re-entry — if OR was broken one direction then reversed, skip
    all_closes = today_bars["Close"].values
    had_long_break = any(float(c) > or_high + buffer for c in all_closes[6:-2])
    had_short_break = any(float(c) < or_low - buffer for c in all_closes[6:-2])
    if long_breakout and had_short_break:
        return None  # previously broke down then reversed up — bull trap risk
    if short_breakout and had_long_break:
        return None  # previously broke up then reversed down — bear trap risk

    conditions = {}
    trend = symbol_regime.get("trend", "sideways")

    conditions["breakout_held"] = {
        "met": long_breakout or short_breakout,
        "detail": f"LTP {ltp:.2f}, 2-bar hold vs OR [{or_low:.2f}-{or_high:.2f}]",
    }
    conditions["cumulative_rvol"] = {
        "met": volume_ok,
        "detail": f"Cum RVOL {current_rvol:.2f}x (need >1.3x)",
    }

    if long_breakout and trend not in ("strong_down", "mild_down"):
        # EMA slope check: skip long if declining momentum
        if ema5_slope < 0:
            return None

        # RSI filter: reject overbought
        rsi_ok = rsi_val < 80
        conditions["rsi_filter"] = {
            "met": rsi_ok,
            "detail": f"RSI {rsi_val:.1f} (reject >80 for longs)",
        }
        if not rsi_ok:
            return None

        conditions["ema_slope"] = {
            "met": ema5_slope >= 0,
            "detail": f"5-bar EMA slope {ema5_slope:.4f} (need >= 0)",
        }
        conditions["regime_ok"] = {"met": True, "detail": f"Trend {trend} allows long"}

        # Levels for target
        levels = compute_intraday_levels(daily_df)
        r1 = levels.get("r1", ltp + atr)

        target = min(ltp + 1.5 * or_range, ltp + atr, r1)
        stop = or_low  # structural stop at OR low

        conf = 0.5
        if volume_ok:
            conf += 0.2
        if day_type in ("trend_up", "gap_and_go"):
            conf += 0.15
        if trend in ("strong_up", "mild_up"):
            conf += 0.1
        conf -= time_penalty

        conditions["day_type"] = {
            "met": day_type in ("trend_up", "gap_and_go"),
            "detail": f"Day type: {day_type}",
        }
        conditions["time_decay"] = {
            "met": time_penalty < 0.15,
            "detail": f"-{time_penalty:.2f} conf ({hours_after_10:.1f}h after 10:00)",
        }

        return _build_result(
            "orb", "long", ltp, stop, target, max(0.1, min(conf, 0.95)), conditions,
            f"ORB long: 2-bar hold above {or_high:.2f}, RVOL {current_rvol:.1f}x, RSI {rsi_val:.0f}",
        )

    if short_breakout and trend not in ("strong_up", "mild_up"):
        # EMA slope check: skip short if rising momentum
        if ema5_slope > 0:
            return None

        # RSI filter: reject oversold
        rsi_ok = rsi_val > 20
        conditions["rsi_filter"] = {
            "met": rsi_ok,
            "detail": f"RSI {rsi_val:.1f} (reject <20 for shorts)",
        }
        if not rsi_ok:
            return None

        conditions["ema_slope"] = {
            "met": ema5_slope <= 0,
            "detail": f"5-bar EMA slope {ema5_slope:.4f} (need <= 0)",
        }
        conditions["regime_ok"] = {"met": True, "detail": f"Trend {trend} allows short"}

        levels = compute_intraday_levels(daily_df)
        s1 = levels.get("s1", ltp - atr)

        target = max(ltp - 1.5 * or_range, ltp - atr, s1)
        stop = or_high  # structural stop at OR high

        conf = 0.5
        if volume_ok:
            conf += 0.2
        if day_type in ("trend_down", "gap_and_go"):
            conf += 0.15
        if trend in ("strong_down", "mild_down"):
            conf += 0.1
        conf -= time_penalty

        conditions["day_type"] = {
            "met": day_type in ("trend_down", "gap_and_go"),
            "detail": f"Day type: {day_type}",
        }
        conditions["time_decay"] = {
            "met": time_penalty < 0.15,
            "detail": f"-{time_penalty:.2f} conf ({hours_after_10:.1f}h after 10:00)",
        }

        return _build_result(
            "orb", "short", ltp, stop, target, max(0.1, min(conf, 0.95)), conditions,
            f"ORB short: 2-bar hold below {or_low:.2f}, RVOL {current_rvol:.1f}x, RSI {rsi_val:.0f}",
        )

    return None
