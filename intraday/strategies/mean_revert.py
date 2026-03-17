"""Mean-Reversion to VWAP strategy.

Mean-reversion on range-bound/volatile days using VWAP standard-deviation bands.
"""

import numpy as np

from common.indicators import compute_atr
from intraday.features import (
    compute_rsi, compute_vwap_bands,
)
from intraday.strategies._common import _build_result


def evaluate_mean_revert(symbol, intra_ist, daily_df, symbol_regime, day_type,
                         sector_df=None):
    """Mean-reversion on range-bound/volatile days.

    Upgrades:
    - Entry at VWAP +/-2 standard deviations (using compute_vwap_bands)
    - Partial target at +/-1 sigma, full target at VWAP
    - Minimum wick size: > max(0.2 x intraday ATR, ltp * 0.001) (wick floor)
    - RSI exhaustion confirmation (RSI > 75 for short, < 25 for long)
    - Sector-relative check: skip if sector moving in same direction
    """
    # Day-type eligibility is handled by STRATEGY_REGIME_MAP in regime.py.
    # No redundant day_type check here — the caller already filters by day type.

    # Trend veto — mean-revert loses in strong trends
    trend = symbol_regime.get("trend", "sideways")
    if trend in ("strong_up", "strong_down"):
        return None

    if intra_ist.empty or daily_df.empty:
        return None

    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]
    if len(today_bars) < 10:
        return None

    # FIX: Time cutoff — not enough time for mean-reversion after 14:30
    last_bar_time = today_bars.index[-1]
    if last_bar_time.hour >= 14 and last_bar_time.minute >= 30:
        return None

    if "vwap" not in today_bars.columns:
        return None

    ltp = float(today_bars["Close"].iloc[-1])
    vwap_val = float(today_bars["vwap"].iloc[-1])

    if np.isnan(vwap_val) or vwap_val == 0:
        return None

    atr = compute_atr(daily_df) if len(daily_df) >= 14 else np.nan
    if np.isnan(atr):
        return None

    # Intraday ATR
    intra_tr = today_bars["High"] - today_bars["Low"]
    intra_atr = float(intra_tr.rolling(14).mean().iloc[-1]) if len(intra_tr) >= 14 else float(intra_tr.mean())

    # VWAP bands for entry trigger
    banded = compute_vwap_bands(intra_ist)
    today_banded = banded[banded.index.date == today] if not banded.empty else banded.__class__()

    # Use VWAP +/-2 sigma for entry trigger
    use_vwap_bands = False
    vwap_2sd_upper = np.nan
    vwap_2sd_lower = np.nan
    vwap_1sd_upper = np.nan
    vwap_1sd_lower = np.nan

    if not today_banded.empty and "vwap_upper_2sd" in today_banded.columns:
        vwap_2sd_upper = float(today_banded["vwap_upper_2sd"].iloc[-1])
        vwap_2sd_lower = float(today_banded["vwap_lower_2sd"].iloc[-1])
        vwap_1sd_upper = float(today_banded["vwap_upper_1sd"].iloc[-1])
        vwap_1sd_lower = float(today_banded["vwap_lower_1sd"].iloc[-1])
        if not (np.isnan(vwap_2sd_upper) or np.isnan(vwap_2sd_lower)):
            use_vwap_bands = True

    distance_from_vwap = ltp - vwap_val

    # Entry trigger: either VWAP +/-2 sigma (preferred) or fallback to 2x intraday ATR
    if use_vwap_bands:
        extended_above = ltp >= vwap_2sd_upper
        extended_below = ltp <= vwap_2sd_lower
        extended = extended_above or extended_below
        extension_detail = f"LTP {ltp:.2f} vs VWAP 2σ [{vwap_2sd_lower:.2f}, {vwap_2sd_upper:.2f}]"
    else:
        distance_atrs = abs(distance_from_vwap) / intra_atr if intra_atr > 0 else 0
        extended = distance_atrs >= 2.0
        extended_above = distance_from_vwap > 0 and extended
        extended_below = distance_from_vwap < 0 and extended
        extension_detail = f"{distance_atrs:.1f} ATRs from VWAP (fallback)"

    # Exhaustion candle check
    last = today_bars.iloc[-1]
    body = abs(float(last["Close"]) - float(last["Open"]))
    candle_range = float(last["High"]) - float(last["Low"])

    # Minimum wick floor: max(0.2 x intraday ATR, ltp * 0.001)
    min_wick_size = max(0.2 * intra_atr, ltp * 0.001)

    if extended_above:
        wick = float(last["High"]) - max(float(last["Open"]), float(last["Close"]))
        exhaustion = (wick > 1.5 * body and wick > min_wick_size) if body > 0 else False
    elif extended_below:
        wick = min(float(last["Open"]), float(last["Close"])) - float(last["Low"])
        exhaustion = (wick > 1.5 * body and wick > min_wick_size) if body > 0 else False
    else:
        exhaustion = False

    # RSI exhaustion confirmation
    rsi = compute_rsi(today_bars["Close"], 14)
    rsi_val = float(rsi.iloc[-1]) if not rsi.empty and not np.isnan(rsi.iloc[-1]) else 50

    # Volume drop
    recent_avg_vol = today_bars["Volume"].iloc[-5:-1].mean() if len(today_bars) > 5 else today_bars["Volume"].mean()
    current_vol = float(today_bars["Volume"].iloc[-1])
    vol_drop = current_vol < 0.7 * recent_avg_vol if recent_avg_vol > 0 else False

    # Sector-relative check: skip if sector trending in same direction as extension
    sector_aligned = False  # True means sector is moving with stock = NOT mean-reverting
    if sector_df is not None and not sector_df.empty and len(sector_df) >= 2:
        sector_ret = (float(sector_df["Close"].iloc[-1]) / float(sector_df["Close"].iloc[-2]) - 1) * 100
        if extended_above and sector_ret > 0.3:
            sector_aligned = True  # sector also up, stock trending with sector
        elif extended_below and sector_ret < -0.3:
            sector_aligned = True

    # FIX: Next-bar confirmation — require actual reversal, not just exhaustion
    next_bar_confirm = False
    if len(today_bars) >= 2:
        prev_bar_close = float(today_bars["Close"].iloc[-2])
        if extended_above:
            next_bar_confirm = ltp < prev_bar_close
        elif extended_below:
            next_bar_confirm = ltp > prev_bar_close

    conditions = {
        "extended_from_vwap": {"met": extended, "detail": extension_detail},
        "exhaustion_candle": {"met": exhaustion, "detail": f"Wick > 1.5x body and > {min_wick_size:.2f}"},
        "volume_drop": {"met": vol_drop, "detail": f"Vol {current_vol:.0f} vs avg {recent_avg_vol:.0f}"},
        "rsi_exhaustion": {
            "met": (rsi_val > 75 and extended_above) or (rsi_val < 25 and extended_below),
            "detail": f"RSI {rsi_val:.1f} ({'overbought' if rsi_val > 75 else 'oversold' if rsi_val < 25 else 'neutral'})",
        },
        "not_sector_trend": {"met": not sector_aligned, "detail": "Sector not trending in same direction"},
        "next_bar_confirm": {"met": next_bar_confirm, "detail": "Bar moving back toward VWAP"},
    }

    if not extended:
        return None

    # Skip if sector is moving in same direction (not a mean-reversion, just trending)
    if sector_aligned:
        return None

    # Require next-bar confirmation — avoids catching falling knives
    if not next_bar_confirm:
        return None

    conf = 0.45
    if exhaustion:
        conf += 0.2
    if vol_drop:
        conf += 0.15
    if day_type == "range_bound":
        conf += 0.1
    if (rsi_val > 75 and extended_above) or (rsi_val < 25 and extended_below):
        conf += 0.05

    if extended_above:
        # Short mean-revert
        stop = float(today_bars["High"].iloc[-3:].max()) + 0.15 * atr
        full_target = vwap_val
        partial_target = vwap_1sd_upper if use_vwap_bands and not np.isnan(vwap_1sd_upper) else (vwap_val + ltp) / 2
        return _build_result(
            "mean_revert", "short", ltp, stop, full_target, min(conf, 0.95), conditions,
            f"Mean-revert short: at VWAP +2σ, RSI {rsi_val:.0f}, partial tgt {partial_target:.2f}",
            partial_target=round(partial_target, 2),
        )
    else:
        # Long mean-revert
        stop = float(today_bars["Low"].iloc[-3:].min()) - 0.15 * atr
        full_target = vwap_val
        partial_target = vwap_1sd_lower if use_vwap_bands and not np.isnan(vwap_1sd_lower) else (vwap_val + ltp) / 2
        return _build_result(
            "mean_revert", "long", ltp, stop, full_target, min(conf, 0.95), conditions,
            f"Mean-revert long: at VWAP -2σ, RSI {rsi_val:.0f}, partial tgt {partial_target:.2f}",
            partial_target=round(partial_target, 2),
        )
