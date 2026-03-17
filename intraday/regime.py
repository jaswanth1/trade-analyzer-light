"""
Market and symbol regime classification for intraday strategies.

Day-type classification (market-level), symbol-level regime tags,
DOW + month-period seasonality, and strategy-regime compatibility.
"""

import calendar
from datetime import datetime

import numpy as np
import pandas as pd

from common.indicators import compute_atr, _to_ist, compute_vwap
from common.data import BENCHMARK
from intraday.features import compute_ema, compute_ema_slope


# ── Day-Type Regime (Market-Level) ────────────────────────────────────────

def classify_day_type(nifty_intra_ist, nifty_daily):
    """Classify market day-type from first 30-60 min of Nifty action.

    Returns:
        type: "trend_up" | "trend_down" | "range_bound" |
              "volatile_two_sided" | "gap_and_go" | "gap_and_fade"
        confidence: float 0-1
        detail: str
    """
    default = {"type": "range_bound", "confidence": 0.3, "detail": "Insufficient data"}

    if nifty_intra_ist.empty or nifty_daily.empty or len(nifty_daily) < 14:
        return default

    today = nifty_intra_ist.index[-1].date()
    today_bars = nifty_intra_ist[nifty_intra_ist.index.date == today]
    if len(today_bars) < 6:
        return default

    # Previous close from daily
    prev_close = float(nifty_daily["Close"].iloc[-2]) if len(nifty_daily) >= 2 else None
    if prev_close is None or prev_close == 0:
        return default

    today_open = float(today_bars["Open"].iloc[0])
    gap_pct = (today_open - prev_close) / prev_close * 100

    # First 30 minutes (6 bars at 5-min)
    first_30 = today_bars.iloc[:6]
    first_30_high = float(first_30["High"].max())
    first_30_low = float(first_30["Low"].min())
    first_30_range = first_30_high - first_30_low
    first_30_close = float(first_30["Close"].iloc[-1])
    first_30_direction = first_30_close - today_open

    # ATR reference (daily)
    daily_atr = compute_atr(nifty_daily)
    if np.isnan(daily_atr) or daily_atr == 0:
        daily_atr = first_30_range * 3  # fallback

    # Count reversals in first 30 min
    closes = first_30["Close"].values
    reversals = 0
    for i in range(2, len(closes)):
        if (closes[i] - closes[i - 1]) * (closes[i - 1] - closes[i - 2]) < 0:
            reversals += 1

    # Classification logic
    abs_gap = abs(gap_pct)
    gap_dir_positive = gap_pct > 0
    move_dir_positive = first_30_direction > 0

    # Gap scenarios
    if abs_gap > 0.5:
        if gap_dir_positive == move_dir_positive:
            conf = min(0.9, 0.5 + abs_gap / 3)
            direction = "up" if gap_dir_positive else "down"
            return {
                "type": "gap_and_go",
                "confidence": round(conf, 2),
                "detail": f"Gap {gap_pct:+.2f}% continuing {direction}, "
                          f"first 30m range {first_30_range:.1f}",
            }
        else:
            conf = min(0.85, 0.5 + abs_gap / 4)
            return {
                "type": "gap_and_fade",
                "confidence": round(conf, 2),
                "detail": f"Gap {gap_pct:+.2f}% fading, "
                          f"first 30m reversal range {first_30_range:.1f}",
            }

    # Range bound: narrow first 30 min
    if first_30_range < 0.5 * daily_atr and abs(first_30_direction) < 0.2 * daily_atr:
        return {
            "type": "range_bound",
            "confidence": round(min(0.8, 0.5 + (0.5 - first_30_range / daily_atr)), 2),
            "detail": f"Narrow range: {first_30_range:.1f} vs ATR {daily_atr:.1f}",
        }

    # Volatile two-sided: wide range with reversals
    if first_30_range > 1.5 * daily_atr and reversals >= 2:
        return {
            "type": "volatile_two_sided",
            "confidence": round(min(0.85, 0.5 + reversals * 0.1), 2),
            "detail": f"Wide range {first_30_range:.1f} ({first_30_range / daily_atr:.1f}x ATR), "
                      f"{reversals} reversals",
        }

    # Trend detection
    if first_30_direction > 0.3 * daily_atr:
        # Check for shallow pullbacks (higher lows pattern)
        lows = first_30["Low"].values
        shallow = all(lows[i] >= lows[i - 1] * 0.998 for i in range(1, len(lows)))
        conf = 0.65 if shallow else 0.55
        return {
            "type": "trend_up",
            "confidence": round(conf, 2),
            "detail": f"Directional move +{first_30_direction:.1f} "
                      f"({first_30_direction / daily_atr:.1f}x ATR)",
        }

    if first_30_direction < -0.3 * daily_atr:
        highs = first_30["High"].values
        shallow = all(highs[i] <= highs[i - 1] * 1.002 for i in range(1, len(highs)))
        conf = 0.65 if shallow else 0.55
        return {
            "type": "trend_down",
            "confidence": round(conf, 2),
            "detail": f"Directional move {first_30_direction:.1f} "
                      f"({first_30_direction / daily_atr:.1f}x ATR)",
        }

    # Default to range_bound
    return {
        "type": "range_bound",
        "confidence": 0.4,
        "detail": f"No clear pattern. Range {first_30_range:.1f}, "
                  f"direction {first_30_direction:.1f}",
    }


def reclassify_day_type(nifty_intra_ist, nifty_daily):
    """Re-classify day type mid-session using all available bars (not just first 30 min).

    Call after 11:00 IST when 60+ bars are available for better accuracy.
    Uses the full session data instead of just the opening range.
    """
    default = {"type": "range_bound", "confidence": 0.3, "detail": "Insufficient data for reclassification"}

    if nifty_intra_ist.empty or nifty_daily.empty or len(nifty_daily) < 14:
        return default

    today = nifty_intra_ist.index[-1].date()
    today_bars = nifty_intra_ist[nifty_intra_ist.index.date == today]
    if len(today_bars) < 12:  # need at least 1 hour of data
        return classify_day_type(nifty_intra_ist, nifty_daily)

    prev_close = float(nifty_daily["Close"].iloc[-2]) if len(nifty_daily) >= 2 else None
    if prev_close is None or prev_close == 0:
        return default

    today_open = float(today_bars["Open"].iloc[0])
    gap_pct = (today_open - prev_close) / prev_close * 100
    current_close = float(today_bars["Close"].iloc[-1])
    session_high = float(today_bars["High"].max())
    session_low = float(today_bars["Low"].min())
    session_range = session_high - session_low

    daily_atr = compute_atr(nifty_daily)
    if np.isnan(daily_atr) or daily_atr == 0:
        daily_atr = session_range * 2

    session_direction = current_close - today_open

    # Count reversals across all bars
    closes = today_bars["Close"].values
    reversals = 0
    for i in range(2, len(closes)):
        if (closes[i] - closes[i - 1]) * (closes[i - 1] - closes[i - 2]) < 0:
            reversals += 1
    reversal_rate = reversals / max(1, len(closes) - 2)

    # Full session classification (more data = higher confidence)
    if session_range > 1.5 * daily_atr and reversal_rate > 0.4:
        return {
            "type": "volatile_two_sided",
            "confidence": round(min(0.9, 0.6 + reversal_rate), 2),
            "detail": f"Mid-session: range {session_range:.1f} ({session_range/daily_atr:.1f}x ATR), "
                      f"{reversals} reversals",
        }

    if abs(session_direction) > 0.5 * daily_atr and reversal_rate < 0.3:
        direction = "up" if session_direction > 0 else "down"
        return {
            "type": f"trend_{direction}",
            "confidence": round(min(0.9, 0.6 + abs(session_direction) / daily_atr * 0.2), 2),
            "detail": f"Mid-session: directional {session_direction:+.1f} "
                      f"({session_direction/daily_atr:.1f}x ATR), low reversal rate",
        }

    if session_range < 0.7 * daily_atr and reversal_rate > 0.3:
        return {
            "type": "range_bound",
            "confidence": round(min(0.85, 0.5 + (0.7 - session_range / daily_atr) * 0.5), 2),
            "detail": f"Mid-session: contained range {session_range:.1f}, choppy",
        }

    # Gap continuation/fade with full session data
    if abs(gap_pct) > 0.5:
        gap_dir = gap_pct > 0
        session_dir = session_direction > 0
        if gap_dir == session_dir:
            return {
                "type": "gap_and_go",
                "confidence": round(min(0.9, 0.6 + abs(gap_pct) / 3), 2),
                "detail": f"Mid-session: gap {gap_pct:+.2f}% continuing",
            }
        else:
            return {
                "type": "gap_and_fade",
                "confidence": round(min(0.85, 0.55 + abs(gap_pct) / 4), 2),
                "detail": f"Mid-session: gap {gap_pct:+.2f}% reversed",
            }

    return {
        "type": "range_bound",
        "confidence": 0.45,
        "detail": f"Mid-session: no clear pattern. Range {session_range:.1f}",
    }


def detect_regime_transition(nifty_ist, nifty_daily):
    """Detect if Nifty is transitioning between regimes intraday.

    Compares the first-hour regime (opening character) with the current
    regime (latest bars). Transitions are where the biggest opportunities
    emerge — a stock suppressed during a bearish morning becomes actionable
    as the market stabilizes.

    Returns dict:
        transition: None | "bear_to_range" | "range_to_bull" | "bull_to_range" | "range_to_bear"
        transition_strength: 0.0-1.0 (how confident the transition is)
        regime_score_adjustment: additive adjustment to regime_score
    """
    result = {"transition": None, "transition_strength": 0.0, "regime_score_adjustment": 0.0}

    if nifty_ist is None or nifty_ist.empty:
        return result

    today = nifty_ist.index[-1].date()
    today_bars = nifty_ist[nifty_ist.index.date == today]
    if len(today_bars) < 12:  # need at least 1 hour of data
        return result

    # First-hour character (first 12 bars = 60 min of 5-min data)
    first_hour = today_bars.head(12)
    fh_ret = (float(first_hour["Close"].iloc[-1]) / float(first_hour["Open"].iloc[0]) - 1) * 100
    fh_range = (float(first_hour["High"].max()) - float(first_hour["Low"].min()))

    # Recent character (last 6 bars = 30 min)
    recent = today_bars.tail(6)
    recent_ret = (float(recent["Close"].iloc[-1]) / float(recent["Open"].iloc[0]) - 1) * 100

    # Full session return
    session_ret = (float(today_bars["Close"].iloc[-1]) / float(today_bars["Open"].iloc[0]) - 1) * 100

    # Detect transitions
    if fh_ret < -0.3 and recent_ret > 0.2 and session_ret > fh_ret + 0.3:
        # Morning was bearish, recent bars turning positive
        strength = min(1.0, abs(recent_ret - fh_ret) / 1.5)
        result["transition"] = "bear_to_range"
        result["transition_strength"] = round(strength, 3)
        result["regime_score_adjustment"] = round(0.12 * strength, 3)

    elif fh_ret > 0.3 and recent_ret < -0.2 and session_ret < fh_ret - 0.3:
        # Morning was bullish, recent bars turning negative
        strength = min(1.0, abs(fh_ret - recent_ret) / 1.5)
        result["transition"] = "bull_to_range"
        result["transition_strength"] = round(strength, 3)
        result["regime_score_adjustment"] = round(-0.08 * strength, 3)

    elif abs(fh_ret) < 0.2 and recent_ret > 0.4:
        # Flat morning, breaking out bullish
        strength = min(1.0, recent_ret / 1.0)
        result["transition"] = "range_to_bull"
        result["transition_strength"] = round(strength, 3)
        result["regime_score_adjustment"] = round(0.10 * strength, 3)

    elif abs(fh_ret) < 0.2 and recent_ret < -0.4:
        # Flat morning, breaking down bearish
        strength = min(1.0, abs(recent_ret) / 1.0)
        result["transition"] = "range_to_bear"
        result["transition_strength"] = round(strength, 3)
        result["regime_score_adjustment"] = round(-0.10 * strength, 3)

    return result


# ── Symbol-Level Regime ──────────────────────────────────────────────────

def classify_symbol_regime(daily_df, intra_ist, nifty_daily=None, sector_daily=None):
    """Per-symbol regime tags (5 dimensions + sector RS).

    Returns:
        trend: "strong_up" | "mild_up" | "sideways" | "mild_down" | "strong_down"
        volatility: "compressed" | "normal" | "expanded"
        liquidity: "normal" | "illiquid"
        momentum: "accelerating" | "steady" | "decelerating"
        relative_strength: "outperforming" | "inline" | "underperforming"
        sector_relative_strength: "outperforming" | "inline" | "underperforming"
        sector_vs_market: "outperforming" | "inline" | "underperforming"
    """
    default = {
        "trend": "sideways",
        "volatility": "normal",
        "liquidity": "normal",
        "momentum": "steady",
        "relative_strength": "inline",
        "weekly_trend": "sideways",
    }

    # Extract symbol name from daily_df for cache key
    symbol = getattr(daily_df, "name", "") or ""
    if symbol:
        from common.analysis_cache import get_cached, set_cached, TTL_DAILY
        cached = get_cached("symbol_regime", symbol=symbol, max_age_seconds=TTL_DAILY)
        if cached is not None:
            return cached

    if daily_df.empty or len(daily_df) < 50:
        return default

    close = daily_df["Close"]
    price = float(close.iloc[-1])

    # EMA alignment
    ema9 = compute_ema(close, 9).iloc[-1]
    ema20 = compute_ema(close, 20).iloc[-1]
    ema50 = compute_ema(close, 50).iloc[-1]

    # 5-day return
    ret_5d = (price / float(close.iloc[-6]) - 1) * 100 if len(close) >= 6 else 0

    # Trend classification
    if price > ema9 > ema20 > ema50 and ret_5d > 2:
        trend = "strong_up"
    elif price > ema20 and ema20 > ema50:
        trend = "mild_up"
    elif price < ema9 < ema20 < ema50 and ret_5d < -2:
        trend = "strong_down"
    elif price < ema20 and ema20 < ema50:
        trend = "mild_down"
    elif abs(ret_5d) <= 1:
        trend = "sideways"
    elif ret_5d > 0:
        trend = "mild_up"
    else:
        trend = "mild_down"

    # Volatility: 5-day ATR vs 20-day ATR
    h = daily_df["High"]
    l = daily_df["Low"]
    c = daily_df["Close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    atr_5 = float(tr.iloc[-5:].mean()) if len(tr) >= 5 else np.nan
    atr_20 = float(tr.iloc[-20:].mean()) if len(tr) >= 20 else np.nan

    if np.isnan(atr_5) or np.isnan(atr_20) or atr_20 == 0:
        volatility = "normal"
    else:
        ratio = atr_5 / atr_20
        if ratio < 0.7:
            volatility = "compressed"
        elif ratio > 1.3:
            volatility = "expanded"
        else:
            volatility = "normal"

    # Liquidity: today's volume vs 20-day intraday median (time-normalized)
    # During live market hours, today's cumulative volume is lower than a full-day
    # median simply because the session isn't over yet. We normalize by comparing
    # today's bars-so-far against the same number of bars from historical days.
    liquidity = "normal"
    if not intra_ist.empty:
        today = intra_ist.index[-1].date()
        today_bars = intra_ist[intra_ist.index.date == today]
        if not today_bars.empty:
            today_vol = today_bars["Volume"].sum()
            today_bar_count = len(today_bars)
            dates = sorted(intra_ist.index.date)
            unique_dates = list(dict.fromkeys(dates))
            if len(unique_dates) > 1:
                hist_dates = unique_dates[:-1][-20:]
                hist_vols = []
                for d in hist_dates:
                    day_bars = intra_ist[intra_ist.index.date == d]
                    # Compare same number of bars (time-normalized)
                    comparable_bars = day_bars.head(today_bar_count)
                    day_vol = comparable_bars["Volume"].sum()
                    hist_vols.append(day_vol)
                if hist_vols:
                    median_vol = float(np.median(hist_vols))
                    if median_vol > 0 and today_vol < 0.5 * median_vol:
                        liquidity = "illiquid"

    # Momentum: EMA20 slope over 5 bars
    ema20_series = compute_ema(close, 20)
    slope = compute_ema_slope(ema20_series, lookback=5)
    if slope > 0.5:
        momentum = "accelerating"
    elif slope < -0.2:
        momentum = "decelerating"
    else:
        momentum = "steady"

    # Relative strength vs Nifty
    relative_strength = "inline"
    if nifty_daily is not None and not nifty_daily.empty and len(nifty_daily) >= 2:
        stock_ret = (price / float(close.iloc[-2]) - 1) * 100 if len(close) >= 2 else 0
        nifty_ret = (
            float(nifty_daily["Close"].iloc[-1]) / float(nifty_daily["Close"].iloc[-2]) - 1
        ) * 100
        diff = stock_ret - nifty_ret
        if diff > 0.5:
            relative_strength = "outperforming"
        elif diff < -0.5:
            relative_strength = "underperforming"

    # Sector relative strength: stock vs sector, sector vs Nifty
    sector_relative_strength = "inline"
    sector_vs_market = "inline"
    if sector_daily is not None and not sector_daily.empty and len(sector_daily) >= 2:
        sector_ret = (
            float(sector_daily["Close"].iloc[-1]) / float(sector_daily["Close"].iloc[-2]) - 1
        ) * 100
        stock_ret_1d = (price / float(close.iloc[-2]) - 1) * 100 if len(close) >= 2 else 0
        # Stock vs sector (±0.5% threshold)
        stock_vs_sector = stock_ret_1d - sector_ret
        if stock_vs_sector > 0.5:
            sector_relative_strength = "outperforming"
        elif stock_vs_sector < -0.5:
            sector_relative_strength = "underperforming"
        # Sector vs Nifty (±0.3% threshold)
        if nifty_daily is not None and not nifty_daily.empty and len(nifty_daily) >= 2:
            nifty_ret = (
                float(nifty_daily["Close"].iloc[-1]) / float(nifty_daily["Close"].iloc[-2]) - 1
            ) * 100
            sec_vs_nifty = sector_ret - nifty_ret
            if sec_vs_nifty > 0.3:
                sector_vs_market = "outperforming"
            elif sec_vs_nifty < -0.3:
                sector_vs_market = "underperforming"

    # Weekly trend alignment (multi-timeframe)
    weekly_trend = "sideways"
    if len(daily_df) >= 40:
        # Resample daily to weekly
        weekly_close = close.resample("W").last().dropna()
        if len(weekly_close) >= 20:
            w_ema9 = float(compute_ema(weekly_close, 9).iloc[-1])
            w_ema20 = float(compute_ema(weekly_close, 20).iloc[-1])
            w_price = float(weekly_close.iloc[-1])
            if w_price > w_ema9 > w_ema20:
                weekly_trend = "up"
            elif w_price < w_ema9 < w_ema20:
                weekly_trend = "down"
            else:
                weekly_trend = "sideways"

    # Counter-trend strength: stock diverging from Nifty direction intraday.
    # High values mean the stock is bucking the market — a valuable signal
    # that should boost ranking in bearish/bullish markets alike.
    counter_trend_strength = 0.0
    if nifty_daily is not None and not nifty_daily.empty and len(nifty_daily) >= 2:
        nifty_ret_1d = (
            float(nifty_daily["Close"].iloc[-1]) / float(nifty_daily["Close"].iloc[-2]) - 1
        ) * 100
        stock_ret_1d = (price / float(close.iloc[-2]) - 1) * 100 if len(close) >= 2 else 0
        # Stock is green when Nifty is red (or vice versa)
        if nifty_ret_1d < -0.3 and stock_ret_1d > 0.3:
            counter_trend_strength = min(1.0, (stock_ret_1d - nifty_ret_1d) / 3.0)
        elif nifty_ret_1d > 0.3 and stock_ret_1d < -0.3:
            counter_trend_strength = min(1.0, (nifty_ret_1d - stock_ret_1d) / 3.0)

    result = {
        "trend": trend,
        "volatility": volatility,
        "liquidity": liquidity,
        "momentum": momentum,
        "relative_strength": relative_strength,
        "sector_relative_strength": sector_relative_strength,
        "sector_vs_market": sector_vs_market,
        "weekly_trend": weekly_trend,
        "counter_trend_strength": round(counter_trend_strength, 3),
    }

    if symbol:
        from common.analysis_cache import set_cached, TTL_DAILY
        set_cached("symbol_regime", result, symbol=symbol)

    return result


# ── DOW + Month-Period Classification ────────────────────────────────────

DOW_NAMES = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"}


def classify_month_period(dt):
    """Returns "begin" (1-7), "mid" (8-15), "end" (16+), or "expiry_week".

    Expiry week = week containing last Thursday of month. Takes priority.
    """
    day = dt.day
    year = dt.year
    month = dt.month

    # Find last Thursday
    last_day = calendar.monthrange(year, month)[1]
    last_thurs = last_day
    while True:
        test = dt.replace(day=last_thurs)
        if test.weekday() == 3:  # Thursday
            break
        last_thurs -= 1

    # Expiry week: Monday to Friday of the week containing last Thursday
    # ISO weekday: Mon=0, Thu=3, so Monday of that week = last_thurs - 3
    expiry_monday = max(1, last_thurs - 3)
    expiry_friday = min(last_day, last_thurs + 1)

    if expiry_monday <= day <= expiry_friday:
        return "expiry_week"
    elif day <= 7:
        return "begin"
    elif day <= 15:
        return "mid"
    else:
        return "end"


def compute_dow_month_stats(daily_df):
    """Historical intraday return stats broken down by DOW and month_period.

    For each historical day:
        intraday_return = (close - open) / open
    Group by (day_of_week, month_period):
        win_rate, avg_return, n_samples

    Returns nested dict: stats[dow][month_period] = {win_rate, avg_return, n}
    """
    symbol = getattr(daily_df, "name", "") or ""
    if symbol:
        from common.analysis_cache import get_cached, set_cached, TTL_DAILY
        cached = get_cached("dow_month_stats", symbol=symbol, max_age_seconds=TTL_DAILY)
        if cached is not None:
            return cached

    if daily_df.empty or len(daily_df) < 20:
        return {}

    df = daily_df.copy()
    df["intraday_return"] = (df["Close"] - df["Open"]) / df["Open"].replace(0, np.nan) * 100
    df = df.dropna(subset=["intraday_return"])

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
            wins = (subset["intraday_return"] > 0).sum()
            stats[dow_name][mp] = {
                "win_rate": round(wins / len(subset) * 100, 1),
                "avg_return": round(float(subset["intraday_return"].mean()), 3),
                "n": len(subset),
            }

        # Overall DOW stats
        if len(dow_data) >= 3:
            wins = (dow_data["intraday_return"] > 0).sum()
            stats[dow_name]["all"] = {
                "win_rate": round(wins / len(dow_data) * 100, 1),
                "avg_return": round(float(dow_data["intraday_return"].mean()), 3),
                "n": len(dow_data),
            }

    # Overall stats
    if len(df) >= 10:
        wins = (df["intraday_return"] > 0).sum()
        stats["overall"] = {
            "win_rate": round(wins / len(df) * 100, 1),
            "avg_return": round(float(df["intraday_return"].mean()), 3),
            "n": len(df),
        }

    if symbol:
        from common.analysis_cache import set_cached, TTL_DAILY
        set_cached("dow_month_stats", stats, symbol=symbol)

    return stats


# ── Strategy-Regime Compatibility ────────────────────────────────────────

STRATEGY_REGIME_MAP = {
    "orb":          {"trend_up", "trend_down", "gap_and_go"},
    # Pullback now eligible on gap_and_fade: if a stock has a strong individual
    # trend but the MARKET gap faded, the stock's trend may continue — pullback
    # entries into that trend are valid. The extra trend check in
    # get_eligible_strategies ensures only strong-trend stocks qualify.
    "pullback":     {"trend_up", "trend_down", "gap_and_fade"},
    "compression":  {"range_bound", "trend_up", "trend_down"},
    # Mean-revert is a natural fit for gap_and_fade: the gap created an
    # overextension, the fade IS mean-reversion. Also added gap_and_go for
    # stocks that overextended in the gap direction.
    "mean_revert":  {"range_bound", "volatile_two_sided", "gap_and_fade", "gap_and_go"},
    "swing":        {"trend_up", "trend_down"},
    "mlr":          {"trend_up", "trend_down", "range_bound", "volatile_two_sided", "gap_and_go", "gap_and_fade"},
}


def get_eligible_strategies(day_type, symbol_regime):
    """Return list of strategy names eligible for this regime combination.

    Filters by day_type compatibility and excludes if symbol is illiquid.
    """
    if symbol_regime.get("liquidity") == "illiquid":
        return []

    eligible = []
    for strategy, valid_days in STRATEGY_REGIME_MAP.items():
        if day_type in valid_days:
            # Extra compatibility checks
            trend = symbol_regime.get("trend", "sideways")
            if strategy == "pullback" and trend in ("sideways",):
                continue
            if strategy == "swing" and trend in ("sideways", "mild_down", "strong_down"):
                continue
            # Removed compression double-gate: intraday squeeze detection in the
            # strategy itself is sufficient. A stock with normal daily volatility
            # can still have an intraday 5-min squeeze.
            eligible.append(strategy)

    # MLR is always eligible (all day types) but skip illiquid stocks
    # (already filtered above). Re-add explicitly if filtered by day_type mismatch
    # shouldn't happen since MLR has all day types, but defensive.
    if "mlr" not in eligible and day_type in STRATEGY_REGIME_MAP.get("mlr", set()):
        eligible.append("mlr")

    return eligible
