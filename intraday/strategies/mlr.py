"""Morning Low Recovery (MLR) strategy.

Buy the morning dip, ride the recovery.
"""

import numpy as np

from common.indicators import compute_atr
from intraday.features import (
    compute_rsi, compute_intraday_levels,
    compute_candle_imbalance, compute_session_low_info,
)
from intraday.strategies._common import _build_result


def evaluate_mlr(symbol, intra_ist, daily_df, opening_range, symbol_regime,
                 day_type, mlr_config=None):
    """Morning Low Recovery: buy the morning dip, ride the recovery.

    Data shows 57% of daily lows form 9:15-11:00 AM, with avg +2.2% recovery
    to close. Works in all market regimes including bearish days.

    Fires during 9:30-11:30 when a stock makes a session low in the morning
    window with reversal confirmation (volume, RSI, VWAP reclaim).

    Signal quality fixes:
    - Profiles only inform confidence, not override entry/stop/target (1A)
    - Early return if config exists and ticker is disabled (1B)
    - Sequencing bonus: low_before_high_pct >= 80 => +0.03 conf
    - Recovery completion bonus: recovered_past_open_pct >= 70 => +0.03 conf
    """
    if intra_ist.empty or daily_df.empty:
        return None

    # 1B: Early return if config exists and ticker is disabled
    if mlr_config and symbol in mlr_config:
        ticker_cfg = mlr_config[symbol]
    elif mlr_config:
        # Config exists but ticker not in it (wasn't enabled) — skip
        return None
    else:
        ticker_cfg = None

    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]

    # Need >= 6 bars (30 min into session)
    if len(today_bars) < 6:
        return None

    current_time = today_bars.index[-1]

    # Time window: 10:00-11:30 (post-settle — ignore opening 45min noise)
    if current_time.hour < 10:
        return None
    if current_time.hour > 11 or (current_time.hour == 11 and current_time.minute > 30):
        return None

    # Per-ticker low cutoff from config (data-driven phase window)
    low_cutoff_h, low_cutoff_m = 11, 30
    if ticker_cfg:
        cutoff_str = ticker_cfg.get("low_cutoff_recommendation", "11:30")
        try:
            parts = cutoff_str.split(":")
            low_cutoff_h, low_cutoff_m = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass

    # Session low info — uses per-ticker adaptive cutoff
    low_info = compute_session_low_info(intra_ist, low_cutoff_h, low_cutoff_m)
    if not low_info:
        return None

    low_price = low_info["low_price"]
    low_in_morning = low_info["low_in_morning"]
    bars_since_low = low_info["bars_since_low"]
    recovery_pct = low_info["recovery_pct"]
    drop_from_open_pct = low_info["drop_from_open_pct"]
    recovery_vol_ratio = low_info["recovery_bar_vol_ratio"]

    # Guard: session low must be in morning window (before 11:00)
    if not low_in_morning:
        return None

    # Guard: at least 2 bars since the low (reversal confirmation)
    if bars_since_low < 2:
        return None

    ltp = float(today_bars["Close"].iloc[-1])
    atr = compute_atr(daily_df) if len(daily_df) >= 14 else np.nan
    if np.isnan(atr) or atr <= 0:
        return None

    # Guard: minimum drop depth — the dip must be real, not noise
    atr_pct = atr / ltp * 100 if ltp > 0 else 0
    min_drop_pct = 0.3 * atr_pct
    if drop_from_open_pct < min_drop_pct:
        return None

    # Previous close for target reference
    prev_close = float(daily_df["Close"].iloc[-2]) if len(daily_df) >= 2 else ltp
    levels = compute_intraday_levels(daily_df)
    pivot = levels.get("pivot", prev_close)

    # -- 7 Conditions --

    # 1. Morning low formed
    cond_morning_low = low_in_morning
    low_time_str = low_info["low_time"].strftime("%H:%M")

    # 2. Recovery started — price recovered >= 0.3% from session low
    cond_recovery = recovery_pct >= 0.3

    # 3. Volume confirmation — recovery bars have stronger volume than sell bars
    cond_volume = recovery_vol_ratio > 1.0
    vol_detail = f"Recovery/sell vol ratio {recovery_vol_ratio:.2f}x (need >1.0x)"

    # 4. RSI turning — RSI(14) was <= 35 near low, now rising
    rsi = compute_rsi(today_bars["Close"], 14)
    rsi_val = float(rsi.iloc[-1]) if not rsi.empty and not np.isnan(rsi.iloc[-1]) else 50
    low_bar_idx = low_info["low_bar_idx"]
    rsi_near_low = 50.0
    if low_bar_idx < len(rsi) and not np.isnan(rsi.iloc[low_bar_idx]):
        rsi_near_low = float(rsi.iloc[low_bar_idx])
    cond_rsi = rsi_near_low <= 35 and rsi_val > rsi_near_low

    # 5. VWAP reclaim — price crossed back above VWAP (or within 0.2%)
    vwap_val = float(today_bars["vwap"].iloc[-1]) if "vwap" in today_bars.columns else np.nan
    cond_vwap = False
    if not np.isnan(vwap_val) and vwap_val > 0:
        cond_vwap = ltp >= vwap_val * 0.998  # within 0.2% of VWAP or above

    # 6. Candle structure — last 2 bars positive imbalance (buyer dominance)
    imbalance = compute_candle_imbalance(today_bars)
    cond_candle = False
    if len(imbalance) >= 2:
        last_2_imb = imbalance.iloc[-2:]
        cond_candle = all(float(v) > 0 for v in last_2_imb)

    # 7. No lower lows — last 2 bars' low > session low (reversal structure intact)
    cond_no_lower = False
    if len(today_bars) >= 2:
        last_2_lows = today_bars["Low"].iloc[-2:]
        cond_no_lower = all(float(l) > low_price for l in last_2_lows)

    conditions = {
        "morning_low": {
            "met": cond_morning_low,
            "detail": f"Session low {low_price:.2f} at {low_time_str}, drop {drop_from_open_pct:.1f}% from open ({min_drop_pct:.1f}% ATR threshold)",
        },
        "recovery_started": {
            "met": cond_recovery,
            "detail": f"Recovery {recovery_pct:.2f}% from low (need >= 0.3%)",
        },
        "volume_confirm": {
            "met": cond_volume,
            "detail": vol_detail,
        },
        "rsi_turning": {
            "met": cond_rsi,
            "detail": f"RSI at low: {rsi_near_low:.1f}, now: {rsi_val:.1f} (need <= 35 at low, rising)",
        },
        "vwap_reclaim": {
            "met": cond_vwap,
            "detail": f"LTP {ltp:.2f} vs VWAP {vwap_val:.2f}" if not np.isnan(vwap_val) else "No VWAP",
        },
        "candle_structure": {
            "met": cond_candle,
            "detail": "Last 2 bars positive imbalance" if cond_candle else "Last 2 bars not all positive",
        },
        "no_lower_lows": {
            "met": cond_no_lower,
            "detail": f"Last 2 bars low > session low {low_price:.2f}",
        },
    }

    # Must have: morning low + recovery started + no lower lows (core reversal signal)
    if not (cond_morning_low and cond_recovery and cond_no_lower):
        return None

    # Need at least 4 of 7 conditions met
    met_count = sum(1 for v in conditions.values() if v["met"])
    if met_count < 4:
        return None

    # -- Entry / Target / Stop --

    # Entry: current close (or VWAP if close > VWAP — better fill)
    if not np.isnan(vwap_val) and ltp > vwap_val:
        entry = vwap_val
    else:
        entry = ltp

    # Stop: ATR-adaptive — session low minus 0.15x ATR buffer
    stop = low_price - 0.15 * atr

    # Target: best of prev_close / pivot / ATR-based, minimum 1.5% from entry
    structural_target = max(prev_close, pivot)
    atr_target = entry + 0.8 * atr  # 0.8x ATR — conservative intraday target
    if structural_target > entry * 1.01:
        target = structural_target
    else:
        target = atr_target
    # Floor: at least 1.5% from entry
    min_target = entry * 1.015
    if target < min_target:
        target = min_target

    # Override with mlr_config values if available for this ticker
    if ticker_cfg:
        if ticker_cfg.get("optimal_stop_pct"):
            stop = entry * (1 - ticker_cfg["optimal_stop_pct"] / 100)
        if ticker_cfg.get("optimal_target_pct"):
            target = entry * (1 + ticker_cfg["optimal_target_pct"] / 100)

    # -- Confidence --
    conf = 0.50

    # Volume: recovery bars outpacing sell bars
    if recovery_vol_ratio > 1.5:
        conf += 0.10  # strong buying conviction
    elif cond_volume:
        conf += 0.05

    # RSI: deep oversold bounce is higher quality
    if rsi_near_low <= 30 and rsi_val > rsi_near_low + 5:
        conf += 0.08
    elif cond_rsi:
        conf += 0.04

    # VWAP reclaim: institutional acceptance of higher prices
    if cond_vwap:
        conf += 0.07

    # Candle structure: consistent buyer dominance
    if cond_candle:
        conf += 0.05

    # Classify today's open type (5 types) and look up profile
    day_open = float(today_bars["Open"].iloc[0])
    gap_pct = (day_open - prev_close) / prev_close * 100 if prev_close > 0 else 0

    def _classify_open_type_live(gp):
        if gp >= 1.0:    return "gap_up_large"
        if gp >= 0.3:    return "gap_up_small"
        if gp <= -1.0:   return "gap_down_large"
        if gp <= -0.3:   return "gap_down_small"
        return "flat"

    today_open_type = _classify_open_type_live(gap_pct)
    today_profile = None
    if ticker_cfg:
        profiles = ticker_cfg.get("profiles", {})
        today_profile = profiles.get(today_open_type)

    # 1A FIX: Profiles only inform confidence, NOT override entry/stop/target
    if today_profile:
        pred = today_profile.get("predictability", 0)
        if pred >= 0.7:
            conf += 0.10
        elif pred >= 0.5:
            conf += 0.05
        # Sequencing bonus: if low reliably forms before high, safer entry
        if today_profile.get("low_before_high_pct", 0) >= 80:
            conf += 0.03
        # Recovery completion bonus: if most days recover past open
        if today_profile.get("recovered_past_open_pct", 0) >= 70:
            conf += 0.03
    else:
        # Fallback: gap-down day bonus (no profile available)
        if gap_pct < -0.3:
            conf += 0.05

    # DOW/month favorability from config
    if ticker_cfg and ticker_cfg.get("dow_favorable"):
        conf += 0.05

    # Drop depth bonus: deeper dips recover more strongly
    if drop_from_open_pct > atr_pct * 0.6:
        conf += 0.05

    # Penalty for strong_down daily trend (not fatal — MLR works in bear,
    # but less reliable when the daily structure is broken)
    trend = symbol_regime.get("trend", "sideways")
    if trend == "strong_down":
        conf -= 0.10

    conf = max(0.1, min(conf, 0.95))

    return _build_result(
        "mlr", "long", entry, stop, target, conf, conditions,
        f"MLR long: session low {low_price:.2f} at {low_time_str}, "
        f"drop {drop_from_open_pct:.1f}%, recovery {recovery_pct:.1f}%, "
        f"vol ratio {recovery_vol_ratio:.1f}x, RSI {rsi_val:.0f}",
    )
