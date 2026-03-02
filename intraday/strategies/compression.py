"""Compression (Squeeze Breakout) strategy.

Bollinger inside Keltner squeeze breakout.
"""

import numpy as np

from common.indicators import compute_atr
from intraday.features import (
    compute_rsi, compute_bollinger, compute_keltner,
    compute_squeeze, compute_volume_ratio, compute_intraday_levels,
)
from intraday.strategies._common import _build_result


def evaluate_compression(symbol, intra_ist, daily_df, symbol_regime):
    """Bollinger inside Keltner squeeze breakout.

    Upgrades:
    - Direction from close vs compression range high/low (not candle color)
    - Compression range from actual squeeze period bars
    - RSI divergence check: hidden bullish divergence = higher confidence
    - Volume must trend down during squeeze then expand on breakout
    """
    if intra_ist.empty or daily_df.empty or len(daily_df) < 20:
        return None

    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]
    if len(today_bars) < 10:
        return None

    close = today_bars["Close"]
    bb = compute_bollinger(close)
    kelt = compute_keltner(today_bars)

    # Check squeeze
    squeeze = compute_squeeze(bb, kelt)
    if squeeze.empty:
        return None

    # Was in squeeze recently (any of last 5 bars)
    recent_squeeze = squeeze.iloc[-5:].any() if len(squeeze) >= 5 else False
    if not recent_squeeze:
        return None

    # FIX: Min/max squeeze duration — <3 bars = noise, >25 bars = dead stock
    squeeze_bars_mask = squeeze & (squeeze.index.isin(today_bars.index))
    squeeze_bar_count = int(squeeze_bars_mask.sum())
    if squeeze_bar_count < 3 or squeeze_bar_count > 25:
        return None

    ltp = float(close.iloc[-1])
    atr = compute_atr(daily_df) if len(daily_df) >= 14 else np.nan
    if np.isnan(atr):
        return None

    # Identify squeeze period bars for compression range
    if squeeze_bars_mask.any():
        squeeze_period = today_bars.loc[squeeze_bars_mask]
        comp_high = float(squeeze_period["High"].max())
        comp_low = float(squeeze_period["Low"].min())
    else:
        # Fallback to last 10 bars
        comp_high = float(today_bars["High"].iloc[-10:].max())
        comp_low = float(today_bars["Low"].iloc[-10:].min())

    # Last candle = expansion trigger
    last = today_bars.iloc[-1]
    body = abs(float(last["Close"]) - float(last["Open"]))
    avg_body = today_bars.apply(
        lambda r: abs(r["Close"] - r["Open"]), axis=1
    ).iloc[-10:].mean()

    expansion_candle = body > 1.5 * avg_body if avg_body > 0 else False

    # Volume pattern: down during squeeze, up on breakout
    vol_ratio = compute_volume_ratio(intra_ist)
    current_vol = float(vol_ratio.iloc[-1]) if not vol_ratio.empty and not np.isnan(vol_ratio.iloc[-1]) else 1.0
    volume_expansion = current_vol > 1.5

    # Check volume trended down during squeeze
    squeeze_vol_declining = False
    if squeeze_bars_mask.any() and squeeze_bars_mask.sum() >= 3:
        sq_vols = today_bars.loc[squeeze_bars_mask, "Volume"]
        first_half = sq_vols.iloc[:len(sq_vols)//2].mean()
        second_half = sq_vols.iloc[len(sq_vols)//2:].mean()
        squeeze_vol_declining = second_half < first_half * 0.9 if first_half > 0 else False

    # RSI for divergence check
    rsi = compute_rsi(close, 14)
    rsi_val = float(rsi.iloc[-1]) if not rsi.empty and not np.isnan(rsi.iloc[-1]) else 50

    # Hidden bullish divergence: price makes higher low but RSI makes lower low
    rsi_divergence = False
    if len(close) >= 10 and len(rsi) >= 10:
        price_lows = close.iloc[-10:-1]
        rsi_lows = rsi.iloc[-10:-1]
        if not price_lows.empty and not rsi_lows.empty:
            price_min_idx = price_lows.idxmin()
            if ltp > float(price_lows.loc[price_min_idx]):
                rsi_at_trough = float(rsi.loc[price_min_idx]) if price_min_idx in rsi.index else rsi_val
                if rsi_val < rsi_at_trough:
                    rsi_divergence = True

    conditions = {
        "squeeze_detected": {"met": recent_squeeze, "detail": "BB inside Keltner recently"},
        "expansion_candle": {"met": expansion_candle, "detail": f"Body {body:.2f} vs avg {avg_body:.2f}"},
        "volume_expansion": {"met": volume_expansion, "detail": f"Vol ratio {current_vol:.2f}x"},
        "squeeze_vol_declining": {"met": squeeze_vol_declining, "detail": "Volume coiled down during squeeze"},
        "rsi_divergence": {"met": rsi_divergence, "detail": f"RSI {rsi_val:.1f}, hidden divergence: {rsi_divergence}"},
    }

    if not expansion_candle:
        return None

    # Direction: close vs compression range (not candle color)
    breakout_up = ltp > comp_high
    breakout_down = ltp < comp_low
    trend = symbol_regime.get("trend", "sideways")

    conf = 0.5
    if volume_expansion:
        conf += 0.2
    if expansion_candle:
        conf += 0.1
    if squeeze_vol_declining:
        conf += 0.05
    if rsi_divergence:
        conf += 0.05

    # FIX: HTF level proximity — cap target at daily R1/S1 (natural reversal zones)
    levels = compute_intraday_levels(daily_df)

    if breakout_up:
        conditions["direction_bias"] = {
            "met": trend not in ("strong_down",),
            "detail": f"Close {ltp:.2f} > comp high {comp_high:.2f}, trend: {trend}",
        }
        if trend in ("strong_up", "mild_up"):
            conf += 0.1
        raw_target = ltp + atr
        r1 = levels.get("r1", raw_target)
        target = min(raw_target, r1) if r1 > ltp else raw_target
        return _build_result(
            "compression", "long", ltp, comp_low, target, min(conf, 0.95), conditions,
            f"Squeeze breakout long: close above compression range, RVOL {current_vol:.1f}x",
        )
    elif breakout_down:
        conditions["direction_bias"] = {
            "met": trend not in ("strong_up",),
            "detail": f"Close {ltp:.2f} < comp low {comp_low:.2f}, trend: {trend}",
        }
        if trend in ("strong_down", "mild_down"):
            conf += 0.1
        raw_target = ltp - atr
        s1 = levels.get("s1", raw_target)
        target = max(raw_target, s1) if s1 < ltp else raw_target
        return _build_result(
            "compression", "short", ltp, comp_high, target, min(conf, 0.95), conditions,
            f"Squeeze breakout short: close below compression range, RVOL {current_vol:.1f}x",
        )
    else:
        # Still inside range — no signal yet
        return None
