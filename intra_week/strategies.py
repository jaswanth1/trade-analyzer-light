"""
IntraWeek sub-strategy evaluation functions.

Three complementary strategies targeting 10-20% weekly upside:
  1. Oversold Recovery — sharp drawdown + sector divergence → mean reversion
  2. Volatility Compression — Bollinger/Keltner squeeze → breakout
  3. Weekly Context Recovery — calendar-driven dislocations (holiday/expiry weeks)

Each returns a candidate dict or None if conditions are not met.
"""

import numpy as np
import pandas as pd

from common.indicators import compute_atr, compute_atr_percentile, compute_relative_performance
from common.market import higher_lows_pattern
from intraday.features import (
    compute_ema, compute_rsi, compute_macd, compute_bollinger, compute_keltner,
)


# ── Thresholds ────────────────────────────────────────────────────────────

# Oversold recovery
DRAWDOWN_MIN_PCT = 5.0         # min 1-2 day drawdown to qualify
SECTOR_DIVERGENCE_MAX = 2.0    # sector must drop < this while stock drops > DRAWDOWN_MIN
RSI_OVERSOLD = 35              # RSI threshold for oversold
VOLUME_SPIKE_RATIO = 1.3       # volume on down day vs 20d median
CLOSE_POSITION_LOW = 0.25      # close in bottom 25% of range = capitulation

# Volatility compression
BB_LOOKBACK = 20               # Bollinger bandwidth lookback for minimum
ATR_PERCENTILE_LOW = 30        # ATR must be below this percentile
VOL_DECLINE_BARS = 5           # check volume declining over N bars

# Weekly context
EARLY_WEEK_DROP_MIN = 2.0      # Mon/Tue drop minimum %
SECTOR_POSITIVE_THRESHOLD = 0  # sector return must be > 0 for divergence

# Target/stop defaults
DEFAULT_TARGET_PCT = 12.0
DEFAULT_STOP_PCT = 5.0


def _recent_drawdown(daily_df, lookback_days=3):
    """Compute max drawdown in the last N trading days.

    Returns (drawdown_pct, n_down_days, max_down_day_pct, down_volume_ratio).
    """
    if len(daily_df) < lookback_days + 20:
        return 0, 0, 0, 1.0

    recent = daily_df.iloc[-lookback_days:]
    prior_close = float(daily_df["Close"].iloc[-(lookback_days + 1)])

    if prior_close <= 0:
        return 0, 0, 0, 1.0

    # Max drawdown from prior close to lowest low in window
    lowest = float(recent["Low"].min())
    drawdown = (prior_close - lowest) / prior_close * 100

    # Count down days
    daily_returns = recent["Close"].pct_change()
    n_down = int((daily_returns < -0.005).sum())

    # Worst single day
    max_down = float(daily_returns.min()) * 100 if not daily_returns.empty else 0

    # Volume on down days vs 20d median
    median_vol = float(daily_df["Volume"].iloc[-21:-lookback_days].median()) if len(daily_df) > 21 else float(daily_df["Volume"].median())
    if median_vol > 0:
        down_days_mask = recent["Close"] < recent["Open"]
        down_vol = float(recent.loc[down_days_mask, "Volume"].mean()) if down_days_mask.any() else 0
        down_volume_ratio = down_vol / median_vol
    else:
        down_volume_ratio = 1.0

    return drawdown, n_down, max_down, down_volume_ratio


def _close_position_in_range(daily_df):
    """Where today's close sits in today's range (0=low, 1=high)."""
    if daily_df.empty:
        return 0.5
    last = daily_df.iloc[-1]
    day_range = last["High"] - last["Low"]
    if day_range <= 0:
        return 0.5
    return (last["Close"] - last["Low"]) / day_range


def evaluate_oversold_recovery(symbol, daily_df, bench_df, sector_df,
                                weekly_ctx, market_ctx):
    """Evaluate oversold recovery setup.

    Trigger: Stock drops 5%+ in 1-2 days while sector drops < 2%.
    Confirmation: RSI < 35, volume spike on down day, close near range low.
    """
    if daily_df.empty or len(daily_df) < 50:
        return None

    close = daily_df["Close"]
    ltp = float(close.iloc[-1])
    if ltp <= 0:
        return None

    # Recent drawdown
    drawdown, n_down, max_down_day, down_vol_ratio = _recent_drawdown(daily_df, lookback_days=3)

    if drawdown < DRAWDOWN_MIN_PCT:
        return None

    # Sector divergence — sector must not have dropped as much
    if sector_df is not None and not sector_df.empty and len(sector_df) >= 4:
        sector_close = sector_df["Close"]
        sector_change = (float(sector_close.iloc[-1]) / float(sector_close.iloc[-4]) - 1) * 100
    else:
        sector_change = 0  # no sector data → don't block

    if sector_change < -SECTOR_DIVERGENCE_MAX:
        return None  # sector also crashed — not stock-specific

    # RSI
    rsi = compute_rsi(close, 14)
    rsi_val = float(rsi.iloc[-1]) if not rsi.empty and not np.isnan(rsi.iloc[-1]) else 50

    # Close position
    close_pos = _close_position_in_range(daily_df)

    # ATR for target/stop
    atr_val = compute_atr(daily_df)
    atr_pct = atr_val / ltp * 100 if not np.isnan(atr_val) and ltp > 0 else 3.0

    # Target: recovery to mean — use 2x ATR or default
    target_pct = min(max(atr_pct * 2.5, 10.0), 20.0)
    stop_pct = min(max(atr_pct * 1.0, 3.0), 7.0)

    # Build conditions
    conditions = {
        "downside_exhaustion": rsi_val < RSI_OVERSOLD,
        "momentum_reversal": _check_macd_turning(close),
        "volume_expansion": down_vol_ratio >= VOLUME_SPIKE_RATIO,
        "sector_strength": sector_change > SECTOR_POSITIVE_THRESHOLD,
        "relative_strength": sector_change - (-(drawdown)) > 3.0,  # stock much worse than sector
        "ema_alignment": _check_ema_structure(close),
        "vwap_reclaim": False,  # daily-only, set later if intraday available
        "not_overextended": drawdown < 20.0,  # not a complete collapse
        "atr_range_ok": 1.5 <= atr_pct <= 8.0,
    }

    return {
        "symbol": symbol,
        "strategy": "oversold_recovery",
        "conditions": conditions,
        "entry": ltp,
        "target_pct": round(target_pct, 2),
        "stop_pct": round(stop_pct, 2),
        "target_price": round(ltp * (1 + target_pct / 100), 2),
        "stop_price": round(ltp * (1 - stop_pct / 100), 2),
        "metrics": {
            "drawdown_pct": round(drawdown, 2),
            "n_down_days": n_down,
            "max_down_day_pct": round(max_down_day, 2),
            "rsi": round(rsi_val, 1),
            "down_vol_ratio": round(down_vol_ratio, 2),
            "close_position": round(close_pos, 3),
            "sector_change": round(sector_change, 2),
            "atr_pct": round(atr_pct, 2),
        },
    }


def evaluate_vol_compression(symbol, daily_df, bench_df, sector_df,
                              weekly_ctx, market_ctx):
    """Evaluate volatility compression breakout setup.

    Trigger: Bollinger bandwidth at 20-day low + Keltner squeeze active.
    Confirmation: Volume declining, EMA bullish, ATR percentile low.
    """
    if daily_df.empty or len(daily_df) < 50:
        return None

    close = daily_df["Close"]
    ltp = float(close.iloc[-1])
    if ltp <= 0:
        return None

    # Bollinger bands
    bb = compute_bollinger(close, period=20, std_dev=2)
    bw = bb["bandwidth"]
    if bw.isna().all() or len(bw.dropna()) < BB_LOOKBACK:
        return None

    bw_clean = bw.dropna()
    current_bw = float(bw_clean.iloc[-1])
    bw_min_20d = float(bw_clean.iloc[-BB_LOOKBACK:].min()) if len(bw_clean) >= BB_LOOKBACK else float(bw_clean.min())

    # Bandwidth must be at or near 20-day low
    bw_at_low = current_bw <= bw_min_20d * 1.05  # within 5% of minimum

    if not bw_at_low:
        return None

    # Keltner squeeze: BB inside KC
    kc = compute_keltner(daily_df, ema_period=20, atr_period=14, multiplier=1.5)
    bb_upper = float(bb["upper"].iloc[-1]) if not bb["upper"].isna().iloc[-1] else None
    bb_lower = float(bb["lower"].iloc[-1]) if not bb["lower"].isna().iloc[-1] else None
    kc_upper = float(kc["upper"].iloc[-1]) if not kc["upper"].isna().iloc[-1] else None
    kc_lower = float(kc["lower"].iloc[-1]) if not kc["lower"].isna().iloc[-1] else None

    squeeze_active = False
    if all(v is not None for v in [bb_upper, bb_lower, kc_upper, kc_lower]):
        squeeze_active = bb_upper < kc_upper and bb_lower > kc_lower

    # ATR percentile
    atr_ptile = compute_atr_percentile(daily_df, period=14, lookback=60)
    atr_val = compute_atr(daily_df)
    atr_pct = atr_val / ltp * 100 if not np.isnan(atr_val) and ltp > 0 else 3.0

    # Volume declining
    vol_declining = _check_volume_declining(daily_df, VOL_DECLINE_BARS)

    # EMA alignment
    ema_bullish = _check_ema_structure(close)

    # Target: breakouts from compression can be large
    target_pct = min(max(atr_pct * 3.0, 10.0), 20.0)
    stop_pct = min(max(atr_pct * 1.0, 3.0), 6.0)

    conditions = {
        "downside_exhaustion": atr_ptile < ATR_PERCENTILE_LOW if not np.isnan(atr_ptile) else False,
        "momentum_reversal": _check_macd_turning(close),
        "volume_expansion": vol_declining,  # declining into squeeze = energy building
        "sector_strength": True,  # not sector-specific strategy
        "relative_strength": True,
        "ema_alignment": ema_bullish,
        "vwap_reclaim": False,
        "not_overextended": True,
        "atr_range_ok": 1.5 <= atr_pct <= 8.0,
    }

    return {
        "symbol": symbol,
        "strategy": "vol_compression",
        "conditions": conditions,
        "entry": ltp,
        "target_pct": round(target_pct, 2),
        "stop_pct": round(stop_pct, 2),
        "target_price": round(ltp * (1 + target_pct / 100), 2),
        "stop_price": round(ltp * (1 - stop_pct / 100), 2),
        "metrics": {
            "bb_bandwidth": round(current_bw, 4),
            "bb_bandwidth_min_20d": round(bw_min_20d, 4),
            "squeeze_active": squeeze_active,
            "atr_percentile": round(atr_ptile, 1) if not np.isnan(atr_ptile) else None,
            "vol_declining": vol_declining,
            "ema_bullish": ema_bullish,
            "atr_pct": round(atr_pct, 2),
        },
    }


def evaluate_weekly_context(symbol, daily_df, bench_df, sector_df,
                             weekly_ctx, market_ctx):
    """Evaluate weekly context recovery setup.

    Trigger: Monday/Tuesday weakness + sector divergence.
    Confirmation: Holiday/expiry week, institutional flow positive, higher low forming.
    """
    if daily_df.empty or len(daily_df) < 50:
        return None

    close = daily_df["Close"]
    ltp = float(close.iloc[-1])
    if ltp <= 0:
        return None

    # Only trigger early in the week (Mon-Wed) — need days for recovery
    dow = weekly_ctx.get("day_of_week", 0)
    remaining = weekly_ctx.get("remaining_trading_days", 0)
    if remaining < 2:
        return None  # not enough days left

    # Check early-week weakness
    if dow <= 2 and len(daily_df) >= 3:  # Mon/Tue/Wed
        # Check if stock dropped significantly in recent 1-2 days
        recent_return = (ltp / float(close.iloc[-3]) - 1) * 100 if len(daily_df) >= 3 else 0
        early_week_drop = recent_return < -EARLY_WEEK_DROP_MIN
    else:
        early_week_drop = False

    # Also check for recent drawdown (works any day)
    drawdown, n_down, _, down_vol_ratio = _recent_drawdown(daily_df, lookback_days=2)
    has_drawdown = drawdown >= 3.0

    if not early_week_drop and not has_drawdown:
        return None

    # Context bonuses
    is_holiday_week = weekly_ctx.get("is_holiday_week", False)
    is_expiry_week = weekly_ctx.get("is_expiry_week", False)
    has_context = is_holiday_week or is_expiry_week

    # Sector strength
    if sector_df is not None and not sector_df.empty and len(sector_df) >= 3:
        sector_change = (float(sector_df["Close"].iloc[-1]) / float(sector_df["Close"].iloc[-3]) - 1) * 100
        sector_strong = sector_change > SECTOR_POSITIVE_THRESHOLD
    else:
        sector_strong = True
        sector_change = 0

    # Institutional flow
    inst_flow = market_ctx.get("inst_flow", "neutral")

    # RSI
    rsi = compute_rsi(close, 14)
    rsi_val = float(rsi.iloc[-1]) if not rsi.empty and not np.isnan(rsi.iloc[-1]) else 50

    # ATR
    atr_val = compute_atr(daily_df)
    atr_pct = atr_val / ltp * 100 if not np.isnan(atr_val) and ltp > 0 else 3.0

    target_pct = min(max(atr_pct * 2.0, 10.0), 18.0)
    stop_pct = min(max(atr_pct * 1.0, 3.0), 6.0)

    conditions = {
        "downside_exhaustion": rsi_val < 40,
        "momentum_reversal": _check_macd_turning(close),
        "volume_expansion": down_vol_ratio >= 1.2 if has_drawdown else False,
        "sector_strength": sector_strong,
        "relative_strength": True,  # evaluated in scoring
        "ema_alignment": _check_ema_structure(close),
        "weekly_context": has_context,
        "vwap_reclaim": False,
        "not_overextended": drawdown < 15.0,
        "atr_range_ok": 1.5 <= atr_pct <= 8.0,
    }

    return {
        "symbol": symbol,
        "strategy": "weekly_context",
        "conditions": conditions,
        "entry": ltp,
        "target_pct": round(target_pct, 2),
        "stop_pct": round(stop_pct, 2),
        "target_price": round(ltp * (1 + target_pct / 100), 2),
        "stop_price": round(ltp * (1 - stop_pct / 100), 2),
        "metrics": {
            "early_week_drop": early_week_drop,
            "drawdown_pct": round(drawdown, 2),
            "rsi": round(rsi_val, 1),
            "sector_change": round(sector_change, 2),
            "is_holiday_week": is_holiday_week,
            "is_expiry_week": is_expiry_week,
            "remaining_days": remaining,
            "inst_flow": inst_flow,
            "atr_pct": round(atr_pct, 2),
        },
    }


# ── Shared helpers ────────────────────────────────────────────────────────

def _check_macd_turning(close_series):
    """Check if MACD histogram is turning positive (momentum reversal)."""
    macd = compute_macd(close_series)
    hist = macd["histogram"]
    if len(hist) < 3 or hist.iloc[-3:].isna().any():
        return False
    # Histogram rising for 2 consecutive bars
    return float(hist.iloc[-1]) > float(hist.iloc[-2]) > float(hist.iloc[-3])


def _check_ema_structure(close_series):
    """Check if EMA 9 > 20 > 50 (bullish alignment)."""
    if len(close_series) < 50:
        return False
    ema9 = float(compute_ema(close_series, 9).iloc[-1])
    ema20 = float(compute_ema(close_series, 20).iloc[-1])
    ema50 = float(compute_ema(close_series, 50).iloc[-1])
    return ema9 > ema20 > ema50


def _check_volume_declining(daily_df, n_bars=5):
    """Check if volume has been declining over the last N bars."""
    if len(daily_df) < n_bars + 1:
        return False
    recent_vol = daily_df["Volume"].iloc[-n_bars:]
    # Linear regression slope — negative = declining
    x = np.arange(len(recent_vol), dtype=float)
    y = recent_vol.values.astype(float)
    if np.std(y) == 0:
        return False
    slope = np.polyfit(x, y, 1)[0]
    return slope < 0
