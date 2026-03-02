"""
Statistical convergence scoring and historical pattern replay.

Convergence: checks 7 independent indicators for alignment before firing a signal.
Historical replay: "has this exact setup worked in the last 6 months?"
"""

import numpy as np
import pandas as pd

from common.indicators import compute_atr, classify_gaps
from intraday.features import (
    compute_ema, compute_rsi, compute_macd, compute_candle_imbalance,
    compute_cumulative_rvol,
)


def compute_convergence_score(candidate: dict, today_bars, daily_df,
                              symbol_regime: dict, nifty_ist=None) -> dict:
    """Check how many independent indicators agree with the trade direction.

    7 dimensions checked:
    1. Price vs VWAP
    2. RSI(14) zone
    3. MACD histogram direction
    4. EMA alignment (9 > 20 > 50)
    5. Candle imbalance (last 3 bars)
    6. Volume trend (RVOL)
    7. Relative strength vs Nifty

    Returns {score: 0-100, aligned: [...], conflicting: [...], total: 7}
    """
    direction = candidate.get("direction", "long")
    is_long = direction == "long"

    aligned = []
    conflicting = []

    close = today_bars["Close"]
    ltp = float(close.iloc[-1])

    # 1. Price vs VWAP
    if "vwap" in today_bars.columns:
        vwap_val = float(today_bars["vwap"].iloc[-1])
        if not np.isnan(vwap_val):
            above_vwap = ltp > vwap_val
            if (is_long and above_vwap) or (not is_long and not above_vwap):
                aligned.append("VWAP")
            else:
                conflicting.append("VWAP")
        # skip if nan
    # skip if no vwap column

    # 2. RSI(14) zone
    rsi = compute_rsi(close, 14)
    if not rsi.empty and not np.isnan(rsi.iloc[-1]):
        rsi_val = float(rsi.iloc[-1])
        if is_long:
            if 40 <= rsi_val <= 70:
                aligned.append("RSI")
            else:
                conflicting.append("RSI")
        else:
            if 30 <= rsi_val <= 60:
                aligned.append("RSI")
            else:
                conflicting.append("RSI")

    # 3. MACD histogram
    macd = compute_macd(close)
    hist = macd["histogram"]
    if len(hist) >= 2 and not hist.iloc[-2:].isna().any():
        hist_rising = float(hist.iloc[-1]) > float(hist.iloc[-2])
        hist_positive = float(hist.iloc[-1]) > 0
        if is_long and (hist_rising or hist_positive):
            aligned.append("MACD")
        elif not is_long and (not hist_rising or not hist_positive):
            aligned.append("MACD")
        else:
            conflicting.append("MACD")

    # 4. EMA alignment (9 > 20 > 50)
    if len(close) >= 50:
        ema9 = float(compute_ema(close, 9).iloc[-1])
        ema20 = float(compute_ema(close, 20).iloc[-1])
        ema50 = float(compute_ema(close, 50).iloc[-1])
        if is_long and ema9 > ema20 > ema50:
            aligned.append("EMA_align")
        elif not is_long and ema9 < ema20 < ema50:
            aligned.append("EMA_align")
        else:
            conflicting.append("EMA_align")
    elif len(close) >= 20:
        ema9 = float(compute_ema(close, 9).iloc[-1])
        ema20 = float(compute_ema(close, 20).iloc[-1])
        if is_long and ema9 > ema20:
            aligned.append("EMA_align")
        elif not is_long and ema9 < ema20:
            aligned.append("EMA_align")
        else:
            conflicting.append("EMA_align")

    # 5. Candle imbalance (last 3 bars average)
    imbalance = compute_candle_imbalance(today_bars)
    if len(imbalance) >= 3:
        avg_imb = float(imbalance.iloc[-3:].mean())
        if is_long and avg_imb > 0.3:
            aligned.append("imbalance")
        elif not is_long and avg_imb < -0.3:
            aligned.append("imbalance")
        elif (is_long and avg_imb < -0.3) or (not is_long and avg_imb > 0.3):
            conflicting.append("imbalance")
        # neutral range: neither aligned nor conflicting

    # 6. Volume trend (RVOL)
    try:
        # Use cumulative RVOL from intra data that includes today_bars
        cum_rvol = compute_cumulative_rvol(today_bars)
        if not cum_rvol.empty and not np.isnan(cum_rvol.iloc[-1]):
            rvol = float(cum_rvol.iloc[-1])
            if rvol > 1.2:
                aligned.append("RVOL")
            elif rvol < 0.8:
                conflicting.append("RVOL")
    except Exception:
        pass

    # 7. Relative strength vs Nifty
    rs = symbol_regime.get("relative_strength", "inline")
    if is_long and rs == "outperforming":
        aligned.append("rel_strength")
    elif not is_long and rs == "underperforming":
        aligned.append("rel_strength")
    elif is_long and rs == "underperforming":
        conflicting.append("rel_strength")
    elif not is_long and rs == "outperforming":
        conflicting.append("rel_strength")

    total = len(aligned) + len(conflicting)
    if total < 4:
        # Insufficient dimensions — don't claim high convergence
        # Use 7 (max possible) as denominator so 2 aligned = 28%, not 100%
        score = round(len(aligned) / 7 * 100)
    else:
        score = round(len(aligned) / max(total, 1) * 100)

    return {
        "score": score,
        "aligned": aligned,
        "conflicting": conflicting,
        "n_aligned": len(aligned),
        "total": total,
    }


def compute_historical_hit_rate(symbol: str, daily_df, strategy: str,
                                direction: str, day_type: str,
                                dow_name: str) -> dict:
    """Scan 6 months of daily data for similar setups.

    Simplified proxy: match on direction + day_type pattern + gap classification.
    For each matching historical day, check if same-day intraday return (open→close)
    was profitable — all strategies are intraday, so we measure intraday edge.

    Returns {hit_rate, sample_size, avg_return, context}.
    """
    from common.analysis_cache import get_cached, set_cached, TTL_DAILY
    params = f"{strategy}|{direction}|{day_type}"
    cached = get_cached("historical_hit_rate", symbol=symbol, params=params, max_age_seconds=TTL_DAILY)
    if cached is not None:
        return cached

    if daily_df.empty or len(daily_df) < 60:
        return {"hit_rate": 0, "sample_size": 0, "avg_return": 0, "context": "Insufficient data"}

    try:
        gap_df = classify_gaps(daily_df)
    except Exception:
        return {"hit_rate": 0, "sample_size": 0, "avg_return": 0, "context": "Gap classification failed"}

    if gap_df.empty or len(gap_df) < 20:
        return {"hit_rate": 0, "sample_size": 0, "avg_return": 0, "context": "Insufficient gap data"}

    # Classify each historical day's characteristics
    matches = []
    is_long = direction == "long"

    for i in range(len(gap_df)):
        row = gap_df.iloc[i]

        # Match criteria (simplified proxy):
        # 1. Same gap direction tendency
        gap_type = row.get("gap_type", "flat")
        intra_return = row.get("open_to_close_pct", 0)
        intra_dir = "up" if intra_return > 0 else "down"

        # For ORB: match gap_and_go / trend days
        # For pullback: match trend days with mild retracement
        # For compression: match range-bound days
        # For mean_revert: match volatile/range days
        # For swing: match strong up days
        match = False

        if strategy == "orb":
            if day_type in ("trend_up", "gap_and_go") and is_long:
                match = gap_type in ("small_up", "large_up", "flat") and intra_dir == "up"
            elif day_type in ("trend_down", "gap_and_go") and not is_long:
                match = gap_type in ("small_down", "large_down", "flat") and intra_dir == "down"
        elif strategy == "pullback":
            if is_long:
                match = intra_dir == "up" and abs(intra_return) < 2.0
            else:
                match = intra_dir == "down" and abs(intra_return) < 2.0
        elif strategy == "compression":
            day_range = row.get("day_range_pct", 999)
            match = day_range < 1.5  # low-range days
        elif strategy == "mean_revert":
            day_range = row.get("day_range_pct", 0)
            match = day_range > 1.0  # volatile days
        elif strategy == "swing":
            match = is_long and intra_dir == "up" and intra_return > 0.5
        elif strategy == "mlr":
            # MLR: days where open < prev_close (gap-down or weak open) AND close > open (recovery day)
            prev_close_val = float(gap_df.iloc[i - 1]["Close"]) if i > 0 and "Close" in gap_df.columns else 0
            day_open = float(row.get("Open", 0)) if "Open" in gap_df.columns else 0
            day_close = float(row.get("Close", 0)) if "Close" in gap_df.columns else 0
            weak_open = day_open < prev_close_val if prev_close_val > 0 else False
            recovery = day_close > day_open if day_open > 0 else False
            match = weak_open and recovery

        if match:
            # All strategies are intraday — measure same-day return (open→close)
            # Using next-day return for intraday strategies is look-ahead bias
            day_open_val = float(row.get("Open", 0)) if "Open" in gap_df.columns else 0
            day_close_val = float(row.get("Close", 0)) if "Close" in gap_df.columns else 0
            if day_open_val > 0:
                intraday_return = (day_close_val - day_open_val) / day_open_val * 100
                if not is_long:
                    intraday_return = -intraday_return
                matches.append(intraday_return)

    sample_size = len(matches)
    if sample_size == 0:
        return {"hit_rate": 0, "sample_size": 0, "avg_return": 0,
                "context": "No matching historical setups"}

    wins = sum(1 for r in matches if r > 0)
    hit_rate = round(wins / sample_size * 100, 1)
    avg_return = round(sum(matches) / sample_size, 3)

    result = {
        "hit_rate": hit_rate,
        "sample_size": sample_size,
        "avg_return": avg_return,
        "context": f"{hit_rate}% win on {sample_size} similar {strategy} setups",
    }
    set_cached("historical_hit_rate", result, symbol=symbol, params=params)
    return result
