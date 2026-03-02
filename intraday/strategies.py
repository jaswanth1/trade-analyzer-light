"""
Intraday strategy modules — institutional grade.

Each strategy is a pure function: (features, regimes) → candidate_trade | None
Strategies: ORB, pullback, compression breakout, mean-reversion, swing continuation.

Upgrades over v1:
- RSI / MACD / pivot level integration
- Multi-bar confirmation (ORB 2-bar hold)
- VWAP standard-deviation bands for mean-revert
- Transaction cost deduction in _build_result
- Cumulative RVOL instead of single-bar volume ratio
- Per-stock intraday ATR-adaptive thresholds
- Swing hold flag (exempt from 15:00 exit)
"""

import numpy as np
import pandas as pd

from common.indicators import compute_atr, compute_vwap, _to_ist
from common.risk import NSE_ROUND_TRIP_COST_PCT
from intraday.features import (
    compute_ema, compute_rsi, compute_macd, compute_bollinger, compute_keltner,
    compute_squeeze, compute_ema_slope, compute_opening_range, compute_volume_ratio,
    compute_cumulative_return_from_open, compute_intraday_levels,
    compute_vwap_bands, compute_cumulative_rvol, compute_candle_imbalance,
    compute_session_low_info,
)


def _build_result(strategy, direction, entry_price, stop_price, target_price,
                  confidence, conditions, reason, **extra):
    """Build standardised candidate trade dict.

    Deducts NSE round-trip transaction cost from target_pct and recalculates RR.
    """
    stop_pct = abs(entry_price - stop_price) / entry_price * 100 if entry_price > 0 else 0
    raw_target_pct = abs(target_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

    # Deduct transaction cost for realistic RR
    effective_target_pct = max(0, raw_target_pct - NSE_ROUND_TRIP_COST_PCT)
    rr = effective_target_pct / stop_pct if stop_pct > 0 else 0

    result = {
        "strategy": strategy,
        "direction": direction,
        "entry_price": round(entry_price, 2),
        "stop_price": round(stop_price, 2),
        "target_price": round(target_price, 2),
        "stop_pct": round(stop_pct, 2),
        "target_pct": round(effective_target_pct, 2),
        "rr_ratio": round(rr, 2),
        "confidence": round(confidence, 2),
        "conditions": conditions,
        "reason": reason,
    }
    result.update(extra)
    return result


# ── Strategy 1: Opening Range Breakout ───────────────────────────────────

def evaluate_orb(symbol, intra_ist, daily_df, opening_range, day_type, symbol_regime):
    """ORB: price breaks above/below the first 30-min range with confirmation.

    Upgrades:
    - Stop at OR low (long) / OR high (short) — proper structural level
    - 2-bar hold confirmation: breakout must hold for 2 consecutive closes
    - Time decay: -0.05 confidence per hour after 10:00
    - RSI filter: reject long if RSI > 80, short if RSI < 20
    - Cumulative RVOL instead of single-bar volume ratio
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
    buffer = 0.1 * atr

    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]
    if len(today_bars) < 8:  # need bars after opening range + 2-bar hold
        return None

    ltp = float(today_bars["Close"].iloc[-1])

    # FIX: Hard 12:00 cutoff — ORB edge decays sharply after noon
    current_time = today_bars.index[-1]
    if current_time.hour >= 12:
        return None

    # Cumulative RVOL (institutional participation)
    cum_rvol = compute_cumulative_rvol(intra_ist)
    current_rvol = float(cum_rvol.iloc[-1]) if not cum_rvol.empty and not np.isnan(cum_rvol.iloc[-1]) else 1.0
    volume_ok = current_rvol > 1.3

    # RSI filter
    rsi = compute_rsi(today_bars["Close"], 14)
    rsi_val = float(rsi.iloc[-1]) if not rsi.empty and not np.isnan(rsi.iloc[-1]) else 50

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
        # RSI filter: reject overbought
        rsi_ok = rsi_val < 80
        conditions["rsi_filter"] = {
            "met": rsi_ok,
            "detail": f"RSI {rsi_val:.1f} (reject >80 for longs)",
        }
        if not rsi_ok:
            return None

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
        # RSI filter: reject oversold
        rsi_ok = rsi_val > 20
        conditions["rsi_filter"] = {
            "met": rsi_ok,
            "detail": f"RSI {rsi_val:.1f} (reject <20 for shorts)",
        }
        if not rsi_ok:
            return None

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


# ── Strategy 2: Trend Pullback Entry ─────────────────────────────────────

def evaluate_pullback(symbol, intra_ist, daily_df, symbol_regime):
    """Pullback into dynamic support/resistance during strong trend.

    Upgrades:
    - Proximity threshold: 0.3 × intraday ATR (adapts to stock volatility)
    - Pivot level confluence: +0.10 confidence if near S1/R1
    - MACD confirmation: histogram must turn in trade direction (rising 2 bars)
    - Target capped at min(R1 distance, 1.5R) instead of day_high
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

    # FIX: Max depth guard — if pullback > 1× ATR from trend extreme, it's a reversal
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
    proximity_threshold = 0.3 * intra_atr if intra_atr > 0 else ltp * 0.003

    # 20 EMA on intraday
    ema20 = compute_ema(today_bars["Close"], 20)
    ema20_val = float(ema20.iloc[-1])

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

        if near_support and (rejection or vol_dry):
            pullback_low = float(today_bars["Low"].iloc[-3:].min())
            stop = pullback_low - 0.1 * atr

            # Target: min(R1 distance, 1.5R) instead of day_high
            risk = ltp - stop
            r1 = levels.get("r1", ltp + 2 * risk)
            r1_target = r1
            rr_target = ltp + 1.5 * risk
            target = min(r1_target, rr_target)

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

        if near_resistance and (rejection or vol_dry):
            pullback_high = float(today_bars["High"].iloc[-3:].max())
            stop = pullback_high + 0.1 * atr

            risk = stop - ltp
            s1 = levels.get("s1", ltp - 2 * risk)
            s1_target = s1
            rr_target = ltp - 1.5 * risk
            target = max(s1_target, rr_target)

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

            return _build_result(
                "pullback", "short", ltp, stop, target, min(conf, 0.95), conditions,
                f"Pullback short: near EMA20/VWAP resistance, MACD {'confirming' if macd_turning_down else 'pending'}, {trend}",
            )

    return None


# ── Strategy 3: Breakout from Compression (Squeeze) ─────────────────────

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
            # Check if current close is higher than recent trough but RSI is lower
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


# ── Strategy 4: Mean-Reversion to VWAP ──────────────────────────────────

def evaluate_mean_revert(symbol, intra_ist, daily_df, symbol_regime, day_type,
                         sector_df=None):
    """Mean-reversion on range-bound/volatile days.

    Upgrades:
    - Entry at VWAP ±2 standard deviations (using compute_vwap_bands)
    - Partial target at ±1σ, full target at VWAP
    - Minimum wick size: > 0.2 × intraday ATR (prevent doji false signals)
    - RSI exhaustion confirmation (RSI > 75 for short, < 25 for long)
    - Sector-relative check: skip if sector moving in same direction
    """
    if day_type not in ("range_bound", "volatile_two_sided"):
        return None

    # FIX: Trend veto — mean-revert loses in strong trends
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
    today_banded = banded[banded.index.date == today] if not banded.empty else pd.DataFrame()

    # Use VWAP ±2σ for entry trigger
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

    # Entry trigger: either VWAP ±2σ (preferred) or fallback to 2× intraday ATR
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

    # Minimum wick size filter: wick > 0.2 × intraday ATR
    min_wick_size = 0.2 * intra_atr

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
    # Last bar must move back toward VWAP compared to the one before it
    next_bar_confirm = False
    if len(today_bars) >= 2:
        prev_bar_close = float(today_bars["Close"].iloc[-2])
        if extended_above:
            # For short entry: current close should be below prev close (moving toward VWAP)
            next_bar_confirm = ltp < prev_bar_close
        elif extended_below:
            # For long entry: current close should be above prev close (moving toward VWAP)
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
        # Target: VWAP (full), partial at 1σ band
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


# ── Strategy 5: Swing Continuation (1-5 day hold) ───────────────────────

def evaluate_swing(symbol, intra_ist, daily_df, symbol_regime):
    """Multi-day swing entry on daily breakout pullback.

    Upgrades:
    - Breakout confirmation uses daily Close above 20-day high (not just High spike)
    - swing_hold = True flag so scanner exempts from 15:00 hard exit
    - Position sizing uses portfolio capital (handled in scanner) with wider stop
    """
    if daily_df.empty or len(daily_df) < 25 or intra_ist.empty:
        return None

    trend = symbol_regime.get("trend", "sideways")
    if trend not in ("strong_up", "mild_up"):
        return None

    close = daily_df["Close"]
    high = daily_df["High"]
    low = daily_df["Low"]

    ltp = float(close.iloc[-1])
    atr = compute_atr(daily_df) if len(daily_df) >= 14 else np.nan
    if np.isnan(atr):
        return None

    # Check: daily *Close* above 20-day high within last 3 sessions
    # FIX: Stale breakout — max 3 days since breakout (edge decays fast)
    close_20d_high = float(close.iloc[-25:-3].max()) if len(close) >= 25 else float(close.iloc[:-3].max())
    recent_closes = close.iloc[-3:]
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
        "above_vwap": {"met": above_vwap, "detail": f"LTP vs VWAP"},
        "trend_strong": {"met": trend == "strong_up", "detail": f"Daily trend: {trend}"},
    }

    if not (broke_out and pulled_back):
        return None

    # Swing low for stop (lowest low of last 5 daily bars)
    swing_low = float(low.iloc[-5:].min())
    stop = swing_low - 0.1 * atr
    target = intra_ltp + atr * 1.5  # multi-day target (1.5x ATR ≈ P75 of post-breakout 5d returns)

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


# ── Strategy 6: Morning Low Recovery (MLR) ───────────────────────────

def evaluate_mlr(symbol, intra_ist, daily_df, opening_range, symbol_regime,
                 day_type, mlr_config=None):
    """Morning Low Recovery: buy the morning dip, ride the recovery.

    Data shows 57% of daily lows form 9:15–11:00 AM, with avg +2.2% recovery
    to close. Works in all market regimes including bearish days.

    Fires during 9:30–11:30 when a stock makes a session low in the morning
    window with reversal confirmation (volume, RSI, VWAP reclaim).
    """
    if intra_ist.empty or daily_df.empty:
        return None

    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]

    # Need ≥6 bars (30 min into session)
    if len(today_bars) < 6:
        return None

    current_time = today_bars.index[-1]

    # Time window: 9:30–11:30
    if current_time.hour < 9 or (current_time.hour == 9 and current_time.minute < 30):
        return None
    if current_time.hour > 11 or (current_time.hour == 11 and current_time.minute > 30):
        return None

    # Session low info
    low_info = compute_session_low_info(intra_ist)
    if not low_info:
        return None

    low_price = low_info["low_price"]
    low_in_morning = low_info["low_in_morning"]
    bars_since_low = low_info["bars_since_low"]
    recovery_pct = low_info["recovery_pct"]

    # Guard: session low must be in morning window (before 11:00)
    if not low_in_morning:
        return None

    # Guard: at least 2 bars since the low (reversal confirmation)
    if bars_since_low < 2:
        return None

    ltp = float(today_bars["Close"].iloc[-1])
    atr = compute_atr(daily_df) if len(daily_df) >= 14 else np.nan
    if np.isnan(atr):
        return None

    # Previous close for target reference
    prev_close = float(daily_df["Close"].iloc[-2]) if len(daily_df) >= 2 else ltp
    levels = compute_intraday_levels(daily_df)
    pivot = levels.get("pivot", prev_close)

    # ── 7 Conditions ──

    # 1. Morning low formed
    cond_morning_low = low_in_morning
    low_time_str = low_info["low_time"].strftime("%H:%M")

    # 2. Recovery started — price recovered ≥0.3% from session low
    cond_recovery = recovery_pct >= 0.3

    # 3. Volume confirmation — RVOL on recovery bars > 1.2×
    cum_rvol = compute_cumulative_rvol(intra_ist)
    current_rvol = float(cum_rvol.iloc[-1]) if not cum_rvol.empty and not np.isnan(cum_rvol.iloc[-1]) else 1.0
    cond_volume = current_rvol > 1.2

    # 4. RSI turning — RSI(14) was ≤35 near low, now rising
    rsi = compute_rsi(today_bars["Close"], 14)
    rsi_val = float(rsi.iloc[-1]) if not rsi.empty and not np.isnan(rsi.iloc[-1]) else 50
    # Check RSI near the low bar
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

    # 6. Candle structure — last 2 bars positive imbalance
    imbalance = compute_candle_imbalance(today_bars)
    cond_candle = False
    if len(imbalance) >= 2:
        last_2_imb = imbalance.iloc[-2:]
        cond_candle = all(float(v) > 0 for v in last_2_imb)

    # 7. No lower lows — last 2 bars' low > session low
    cond_no_lower = False
    if len(today_bars) >= 2:
        last_2_lows = today_bars["Low"].iloc[-2:]
        cond_no_lower = all(float(l) > low_price for l in last_2_lows)

    conditions = {
        "morning_low": {
            "met": cond_morning_low,
            "detail": f"Session low {low_price:.2f} at {low_time_str} (before 11:00)",
        },
        "recovery_started": {
            "met": cond_recovery,
            "detail": f"Recovery {recovery_pct:.2f}% from low (need ≥0.3%)",
        },
        "volume_confirm": {
            "met": cond_volume,
            "detail": f"RVOL {current_rvol:.2f}x (need >1.2x)",
        },
        "rsi_turning": {
            "met": cond_rsi,
            "detail": f"RSI at low: {rsi_near_low:.1f}, now: {rsi_val:.1f} (need ≤35 at low, rising)",
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

    # ── Entry / Target / Stop ──
    # Entry: current close (or VWAP if close > VWAP)
    if not np.isnan(vwap_val) and ltp > vwap_val:
        entry = vwap_val
    else:
        entry = ltp

    # Stop: session low − 0.3% buffer
    stop = low_price * 0.997

    # Target: previous close or pivot level (≥1.5% from entry)
    target = max(prev_close, pivot)
    min_target = entry * 1.015  # at least 1.5% from entry
    if target < min_target:
        target = min_target

    # Override with mlr_config values if available for this ticker
    ticker_cfg = None
    if mlr_config and symbol in mlr_config:
        ticker_cfg = mlr_config[symbol]
        if ticker_cfg.get("optimal_stop_pct"):
            stop = entry * (1 - ticker_cfg["optimal_stop_pct"] / 100)
        if ticker_cfg.get("optimal_target_pct"):
            target = entry * (1 + ticker_cfg["optimal_target_pct"] / 100)

    # ── Confidence ──
    conf = 0.50
    if current_rvol > 1.5:
        conf += 0.10  # strong RVOL
    elif cond_volume:
        conf += 0.05
    if rsi_near_low <= 30 and rsi_val > rsi_near_low + 5:
        conf += 0.08  # deep RSI bounce
    if cond_vwap:
        conf += 0.07  # VWAP reclaim
    if cond_candle:
        conf += 0.05  # consistent candle structure

    # Gap-down day bonus (MLR works especially well on gap-downs)
    day_open = float(today_bars["Open"].iloc[0])
    gap_pct = (day_open - prev_close) / prev_close * 100 if prev_close > 0 else 0
    if gap_pct < -0.3:
        conf += 0.05

    # DOW/month favorability from config
    if ticker_cfg and ticker_cfg.get("dow_favorable"):
        conf += 0.05

    # Penalty for strong_down trend
    trend = symbol_regime.get("trend", "sideways")
    if trend == "strong_down":
        conf -= 0.10

    conf = max(0.1, min(conf, 0.95))

    return _build_result(
        "mlr", "long", entry, stop, target, conf, conditions,
        f"MLR long: session low {low_price:.2f} at {low_time_str}, "
        f"recovery {recovery_pct:.1f}%, RVOL {current_rvol:.1f}x, RSI {rsi_val:.0f}",
    )
