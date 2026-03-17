"""Trend Pullback Entry strategy.

Pullback into dynamic support/resistance during strong trend.
"""

import numpy as np

from common.indicators import compute_atr
from intraday.features import (
    compute_ema, compute_ema_slope, compute_macd, compute_intraday_levels,
)
from intraday.strategies._common import _build_result


def evaluate_pullback(symbol, intra_ist, daily_df, symbol_regime):
    """Pullback into dynamic support/resistance during strong trend.

    Upgrades:
    - Proximity threshold: 0.3 x intraday ATR (adapts to stock volatility)
    - Proximity floor: max(0.3 * intra_atr, ltp * 0.001) — prevents impossibly tight thresholds
    - Pivot level confluence: +0.10 confidence if near S1/R1
    - MACD confirmation: histogram must turn in trade direction (rising 2 bars)
    - Target capped at min(R1 distance, 1.5R) instead of day_high
    - Recovery volume check: last bar volume > max(last 5 bars volume)
    """
    if intra_ist.empty or daily_df.empty or len(daily_df) < 20:
        return None

    trend = symbol_regime.get("trend", "sideways")
    if trend not in ("strong_up", "mild_up", "strong_down", "mild_down"):
        return None

    # FIX: Trend maturity check — if 5+ consecutive up/down days, trend is mature
    if len(daily_df) >= 6:
        recent_returns = daily_df["Close"].pct_change().iloc[-5:]
        if trend in ("strong_up", "mild_up") and all(r > 0 for r in recent_returns.dropna()):
            return None  # 5 consecutive up days — reversal risk, not pullback
        if trend in ("strong_down", "mild_down") and all(r < 0 for r in recent_returns.dropna()):
            return None  # 5 consecutive down days

    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]
    if len(today_bars) < 10:
        return None

    ltp = float(today_bars["Close"].iloc[-1])
    atr = compute_atr(daily_df) if len(daily_df) >= 14 else np.nan
    if np.isnan(atr):
        return None

    # FIX: Max depth guard — if pullback > 1x ATR from trend extreme, it's a reversal
    if trend in ("strong_up", "mild_up"):
        trend_high = float(daily_df["High"].iloc[-5:].max())
        if trend_high - ltp > atr:
            return None  # too deep — this is a reversal, not a pullback
    elif trend in ("strong_down", "mild_down"):
        trend_low = float(daily_df["Low"].iloc[-5:].min())
        if ltp - trend_low > atr:
            return None

    # Intraday ATR for adaptive proximity
    intra_tr = today_bars["High"] - today_bars["Low"]
    intra_atr = float(intra_tr.rolling(14).mean().iloc[-1]) if len(intra_tr) >= 14 else float(intra_tr.mean())
    # Proximity floor: prevent impossibly tight thresholds on low-ATR stocks
    proximity_threshold = max(0.3 * intra_atr, ltp * 0.001) if intra_atr > 0 else ltp * 0.003

    # 20 EMA on intraday
    ema20 = compute_ema(today_bars["Close"], 20)
    ema20_val = float(ema20.iloc[-1])

    # EMA20 slope veto: if the moving average itself is rolling over,
    # the "pullback" is likely a trend reversal, not a continuation
    ema20_slope = compute_ema_slope(ema20, lookback=5)
    if trend in ("strong_up", "mild_up") and ema20_slope < -0.05:
        return None  # EMA20 declining — trend weakening, not a pullback
    if trend in ("strong_down", "mild_down") and ema20_slope > 0.05:
        return None  # EMA20 rising — downtrend weakening

    # VWAP
    vwap_val = float(today_bars["vwap"].iloc[-1]) if "vwap" in today_bars.columns else ema20_val

    # Pivot levels for confluence and target capping
    levels = compute_intraday_levels(daily_df)

    # MACD confirmation
    macd = compute_macd(today_bars["Close"])
    hist = macd["histogram"]
    macd_turning_up = False
    macd_turning_down = False
    if len(hist) >= 3 and not hist.iloc[-3:].isna().any():
        macd_turning_up = float(hist.iloc[-1]) > float(hist.iloc[-2]) > float(hist.iloc[-3])
        macd_turning_down = float(hist.iloc[-1]) < float(hist.iloc[-2]) < float(hist.iloc[-3])

    # Last candle analysis
    last = today_bars.iloc[-1]
    body = abs(float(last["Close"]) - float(last["Open"]))
    lower_wick = float(last["Open" if last["Close"] > last["Open"] else "Close"]) - float(last["Low"])
    upper_wick = float(last["High"]) - float(last["Close" if last["Close"] > last["Open"] else "Open"])

    # Recovery volume check: last bar volume > max(last 5 bars volume)
    last_bar_vol = float(today_bars["Volume"].iloc[-1])
    prev_5_max_vol = float(today_bars["Volume"].iloc[-6:-1].max()) if len(today_bars) >= 6 else last_bar_vol
    recovery_volume_ok = last_bar_vol > prev_5_max_vol

    conditions = {}

    if trend in ("strong_up", "mild_up"):
        # Adaptive proximity to support
        near_support = (ltp - ema20_val <= proximity_threshold) or (ltp - vwap_val <= proximity_threshold)
        conditions["near_support"] = {
            "met": near_support,
            "detail": f"LTP {ltp:.2f} vs EMA20 {ema20_val:.2f}, VWAP {vwap_val:.2f} (thresh {proximity_threshold:.2f})",
        }

        rejection = lower_wick > 1.5 * body if body > 0 else False
        conditions["rejection_candle"] = {
            "met": rejection,
            "detail": f"Lower wick {lower_wick:.2f} vs body {body:.2f}",
        }

        recent_vol = today_bars["Volume"].iloc[-3:].mean()
        prior_vol = today_bars["Volume"].iloc[:-3].mean() if len(today_bars) > 6 else recent_vol
        vol_dry = recent_vol < prior_vol * 0.8 if prior_vol > 0 else False
        conditions["volume_drying"] = {
            "met": vol_dry,
            "detail": f"Recent vol {recent_vol:.0f} vs prior {prior_vol:.0f}",
        }

        # MACD confirmation for longs
        conditions["macd_confirm"] = {
            "met": macd_turning_up,
            "detail": f"MACD histogram {'rising' if macd_turning_up else 'not rising'} for 2 bars",
        }

        # Pivot confluence
        s1 = levels.get("s1", 0)
        pivot_confluence = abs(ltp - s1) / ltp < 0.005 if s1 > 0 else False
        conditions["pivot_confluence"] = {
            "met": pivot_confluence,
            "detail": f"LTP {ltp:.2f} vs S1 {s1:.2f}" if s1 else "No pivot data",
        }

        # Recovery volume
        conditions["recovery_volume"] = {
            "met": recovery_volume_ok,
            "detail": f"Last bar vol {last_bar_vol:.0f} vs max prev 5 {prev_5_max_vol:.0f}",
        }

        if near_support and (rejection or vol_dry):
            pullback_low = float(today_bars["Low"].iloc[-3:].min())
            stop = pullback_low - 0.1 * atr

            # Target: min(R1 distance, 1.5R) — but only use R1 if it's above entry
            risk = ltp - stop
            rr_target = ltp + 1.5 * risk
            r1 = levels.get("r1", 0)
            # Only cap at R1 if R1 is a valid resistance ABOVE current price
            if r1 > ltp:
                target = min(r1, rr_target)
            else:
                target = rr_target

            conf = 0.5
            if rejection:
                conf += 0.15
            if vol_dry:
                conf += 0.1
            if trend == "strong_up":
                conf += 0.15
            if macd_turning_up:
                conf += 0.05
            if pivot_confluence:
                conf += 0.10
            if recovery_volume_ok:
                conf += 0.05

            return _build_result(
                "pullback", "long", ltp, stop, target, min(conf, 0.95), conditions,
                f"Pullback long: near EMA20/VWAP, MACD {'confirming' if macd_turning_up else 'pending'}, {trend}",
            )

    elif trend in ("strong_down", "mild_down"):
        near_resistance = (ema20_val - ltp <= proximity_threshold) or (vwap_val - ltp <= proximity_threshold)
        conditions["near_resistance"] = {
            "met": near_resistance,
            "detail": f"LTP {ltp:.2f} vs EMA20 {ema20_val:.2f}, VWAP {vwap_val:.2f} (thresh {proximity_threshold:.2f})",
        }

        rejection = upper_wick > 1.5 * body if body > 0 else False
        conditions["rejection_candle"] = {
            "met": rejection,
            "detail": f"Upper wick {upper_wick:.2f} vs body {body:.2f}",
        }

        recent_vol = today_bars["Volume"].iloc[-3:].mean()
        prior_vol = today_bars["Volume"].iloc[:-3].mean() if len(today_bars) > 6 else recent_vol
        vol_dry = recent_vol < prior_vol * 0.8 if prior_vol > 0 else False
        conditions["volume_drying"] = {
            "met": vol_dry,
            "detail": f"Recent vol {recent_vol:.0f} vs prior {prior_vol:.0f}",
        }

        conditions["macd_confirm"] = {
            "met": macd_turning_down,
            "detail": f"MACD histogram {'falling' if macd_turning_down else 'not falling'} for 2 bars",
        }

        r1 = levels.get("r1", 0)
        pivot_confluence = abs(ltp - r1) / ltp < 0.005 if r1 > 0 else False
        conditions["pivot_confluence"] = {
            "met": pivot_confluence,
            "detail": f"LTP {ltp:.2f} vs R1 {r1:.2f}" if r1 else "No pivot data",
        }

        conditions["recovery_volume"] = {
            "met": recovery_volume_ok,
            "detail": f"Last bar vol {last_bar_vol:.0f} vs max prev 5 {prev_5_max_vol:.0f}",
        }

        if near_resistance and (rejection or vol_dry):
            pullback_high = float(today_bars["High"].iloc[-3:].max())
            stop = pullback_high + 0.1 * atr

            risk = stop - ltp
            rr_target = ltp - 1.5 * risk
            s1 = levels.get("s1", 0)
            # Only cap at S1 if S1 is a valid support BELOW current price
            if 0 < s1 < ltp:
                target = max(s1, rr_target)
            else:
                target = rr_target

            conf = 0.5
            if rejection:
                conf += 0.15
            if vol_dry:
                conf += 0.1
            if trend == "strong_down":
                conf += 0.15
            if macd_turning_down:
                conf += 0.05
            if pivot_confluence:
                conf += 0.10
            if recovery_volume_ok:
                conf += 0.05

            return _build_result(
                "pullback", "short", ltp, stop, target, min(conf, 0.95), conditions,
                f"Pullback short: near EMA20/VWAP resistance, MACD {'confirming' if macd_turning_down else 'pending'}, {trend}",
            )

    return None
