"""
Scoring module — signal evaluation, ranking, and position management.

Extracted from scanner.py to allow clean imports without circular dependencies.
"""

from datetime import datetime, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from common.data import PROJECT_ROOT, load_universe_for_tier

TICKERS = load_universe_for_tier("intraday")
from common.indicators import compute_vwap, _to_ist, compute_atr as compute_atr_series
from common.market import check_earnings_proximity, compute_market_context_scores
from intraday.convergence import compute_convergence_score, compute_historical_hit_rate
from intraday.features import (
    compute_opening_range, compute_intraday_levels, compute_volume_ratio,
)
from intraday.regime import (
    classify_symbol_regime, classify_month_period, get_eligible_strategies, DOW_NAMES,
)
from intraday.strategies import (
    evaluate_orb, evaluate_pullback, evaluate_compression,
    evaluate_mean_revert, evaluate_swing, evaluate_mlr,
)

IST = ZoneInfo("Asia/Kolkata")

# ── Constants ─────────────────────────────────────────────────────────────

MIN_RR_RATIO = 1.2  # minimum RR gate — discard below this
LONG_ONLY = True  # equity cash segment — BUY only, no short selling
EXIT_DEADLINE = dtime(15, 0)
LUNCH_WINDOW = (dtime(12, 0), dtime(13, 0))

# ── Strategy Time Windows (LIVE mode) ────────────────────────────────────

STRATEGY_TIME_WINDOWS = {
    "orb":         (dtime(9, 15), dtime(12, 0)),
    "pullback":    (dtime(9, 30), dtime(14, 30)),
    "compression": (dtime(10, 0), dtime(14, 0)),
    "mean_revert": (dtime(10, 0), dtime(14, 30)),
    "swing":       (dtime(9, 15), dtime(15, 0)),
    "mlr":         (dtime(10, 0), dtime(11, 30)),
}

# ── MLR Config (precomputed per-ticker calibration) ──────────────────
MLR_CONFIG_PATH = PROJECT_ROOT / "intraday" / "mlr_config.yaml"
_mlr_config = {}
if MLR_CONFIG_PATH.exists():
    try:
        with open(MLR_CONFIG_PATH) as f:
            _raw = yaml.safe_load(f) or {}
        # Build per-ticker lookup: only enabled tickers
        for sym, cfg in _raw.get("tickers", {}).items():
            if cfg.get("enabled", False):
                _mlr_config[sym] = cfg
    except Exception:
        pass  # gracefully degrade without config


def compute_time_relevance(strategy, now_ist=None):
    """Compute time-relevance for a strategy during LIVE mode.

    Returns:
        status: "PRIME" | "FADING" | "EXPIRED"
        note: human-readable string
        penalty: score penalty to apply (0.0 or -0.05)
    """
    if now_ist is None:
        now_ist = datetime.now(IST)
    t = now_ist.time()

    window = STRATEGY_TIME_WINDOWS.get(strategy)
    if window is None:
        return {"status": "PRIME", "note": "", "penalty": 0.0}

    start, end = window

    if t > end:
        return {
            "status": "EXPIRED",
            "note": f"{strategy.upper()} window ({start.strftime('%H:%M')}-{end.strftime('%H:%M')}) — EXPIRED, skip.",
            "penalty": 0.0,  # we skip entirely
        }

    if t < start:
        mins_to_start = (datetime.combine(now_ist.date(), start) -
                         datetime.combine(now_ist.date(), t)).seconds // 60
        return {
            "status": "PRIME",
            "note": f"{strategy.upper()} window opens in {mins_to_start} min ({start.strftime('%H:%M')}).",
            "penalty": 0.0,
        }

    # Within window — check how much is left
    total_secs = (datetime.combine(now_ist.date(), end) -
                  datetime.combine(now_ist.date(), start)).seconds
    elapsed_secs = (datetime.combine(now_ist.date(), t) -
                    datetime.combine(now_ist.date(), start)).seconds
    pct_elapsed = elapsed_secs / total_secs if total_secs > 0 else 0
    mins_left = (total_secs - elapsed_secs) // 60

    if pct_elapsed > 0.75:
        return {
            "status": "FADING",
            "note": (f"{strategy.upper()} window {start.strftime('%H:%M')}-{end.strftime('%H:%M')} "
                     f"— FADING ({mins_left} min left)."),
            "penalty": -0.05,
        }

    return {
        "status": "PRIME",
        "note": (f"{strategy.upper()} window {start.strftime('%H:%M')}-{end.strftime('%H:%M')} "
                 f"— PRIME ({mins_left} min left)."),
        "penalty": 0.0,
    }


# ── Multi-Factor Scoring (replaces binary gates) ────────────────────

FACTOR_WEIGHTS = {
    "market_regime":       0.12,
    "vwap_alignment":      0.08,
    "stock_trend":         0.15,
    "convergence":         0.15,
    "relative_strength":   0.10,
    "momentum":            0.05,
    "seasonality":         0.05,
    "news_sentiment":      0.05,
    "strategy_confidence": 0.15,
    "rr_quality":          0.10,
}


def compute_composite_score(candidate, market_ctx, symbol_regime, convergence_pct,
                             dow_wr, mp_wr, news_sentiment, vwap_distance_atrs,
                             counter_trend_strength=0.0):
    """Unified multi-factor scoring following institutional methodology.

    Every dimension produces a 0.0-1.0 factor score. The composite is the
    weighted average. No binary gates — all factors are continuous.

    Returns (composite_score, factor_details_dict).
    """
    factors = {}
    direction = candidate.get("direction", "long")

    # Market regime
    factors["market_regime"] = market_ctx.get("regime_score", 0.5)

    # VWAP alignment: continuous distance measure
    if direction == "long":
        vwap_f = np.interp(vwap_distance_atrs, [-1.0, -0.3, 0, 0.3, 1.0],
                           [0.1, 0.3, 0.55, 0.8, 1.0])
    else:
        vwap_f = np.interp(-vwap_distance_atrs, [-1.0, -0.3, 0, 0.3, 1.0],
                           [0.1, 0.3, 0.55, 0.8, 1.0])
    factors["vwap_alignment"] = float(vwap_f)

    # Stock trend strength
    trend = symbol_regime.get("trend", "sideways")
    if direction == "long":
        t_map = {"strong_up": 1.0, "mild_up": 0.75, "sideways": 0.5,
                 "mild_down": 0.3, "strong_down": 0.15}
    else:
        t_map = {"strong_down": 1.0, "mild_down": 0.75, "sideways": 0.5,
                 "mild_up": 0.3, "strong_up": 0.15}
    factors["stock_trend"] = t_map.get(trend, 0.5)

    # Convergence
    factors["convergence"] = convergence_pct / 100.0

    # Relative strength + counter-trend bonus
    rs = symbol_regime.get("relative_strength", "inline")
    rs_map = {"outperforming": 0.85, "inline": 0.5, "underperforming": 0.2}
    rs_val = rs_map.get(rs, 0.5)
    if counter_trend_strength > 0:
        rs_val = min(1.0, rs_val + 0.25 * counter_trend_strength)
    factors["relative_strength"] = rs_val

    # Momentum
    mom = symbol_regime.get("momentum", "steady")
    factors["momentum"] = {"accelerating": 0.85, "steady": 0.5, "decelerating": 0.2}.get(mom, 0.5)

    # Seasonality
    dow_f = float(np.interp(dow_wr, [20, 40, 50, 60, 80], [0.15, 0.35, 0.5, 0.65, 0.85]))
    mp_f = float(np.interp(mp_wr, [20, 40, 50, 60, 80], [0.15, 0.35, 0.5, 0.65, 0.85]))
    factors["seasonality"] = (dow_f + mp_f) / 2

    # News sentiment
    factors["news_sentiment"] = float(np.interp(
        news_sentiment if direction == "long" else -news_sentiment,
        [-1.0, -0.3, 0, 0.3, 1.0], [0.1, 0.35, 0.5, 0.65, 0.9]))

    # Strategy raw confidence
    factors["strategy_confidence"] = candidate.get("confidence", 0.5)

    # RR quality
    rr = candidate.get("rr_ratio", 1.0)
    factors["rr_quality"] = float(np.interp(rr, [0.5, 1.2, 2.0, 3.0, 5.0],
                                            [0.0, 0.3, 0.6, 0.85, 1.0]))

    # Weighted composite
    composite = sum(FACTOR_WEIGHTS[k] * factors[k] for k in FACTOR_WEIGHTS)

    # Sector RS bonus (additive)
    sec_rs = symbol_regime.get("sector_relative_strength", "inline")
    sec_vs = symbol_regime.get("sector_vs_market", "inline")
    if sec_rs == "outperforming" and sec_vs == "outperforming":
        composite += 0.03
    elif sec_rs == "underperforming" and sec_vs == "underperforming":
        composite -= 0.02

    # Weekly trend penalty for trend-following strategies
    weekly = symbol_regime.get("weekly_trend", "sideways")
    strat = candidate.get("strategy", "")
    if strat in ("orb", "pullback", "swing"):
        if (trend in ("strong_up", "mild_up") and weekly == "down") or \
           (trend in ("strong_down", "mild_down") and weekly == "up"):
            composite -= 0.04

    # Historical hit rate bonus/penalty
    hist_rate = candidate.get("historical_hit_rate", 50)
    hist_n = candidate.get("historical_sample_size", 0)
    if hist_n >= 10:
        if hist_rate > 60:
            composite += 0.03
        elif hist_rate < 40:
            composite -= 0.03

    # Time relevance
    if candidate.get("time_window_status") == "FADING":
        composite -= 0.03

    # VIX score from market context
    composite = composite * (0.7 + 0.3 * market_ctx.get("vix_score", 0.65))

    return round(max(0.0, min(1.0, composite)), 3), factors


def evaluate_symbol(symbol, intra_df, daily_df, nifty_state, vix_info,
                    day_type_info, dow_month_stats, sector_data,
                    news_data=None, now_ist=None, skip_earnings_check=False):
    """Evaluate one symbol across all eligible strategies.

    Returns list of candidate setups (a symbol can have multiple).
    """
    cfg = TICKERS.get(symbol, {"name": symbol, "sector": ""})
    candidates = []

    if intra_df.empty or daily_df.empty:
        return candidates

    # Convert to IST + compute VWAP
    intra_ist = compute_vwap(_to_ist(intra_df))
    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]
    if today_bars.empty:
        today_bars = intra_ist.tail(20)
    if today_bars.empty:
        return candidates

    ltp = float(today_bars["Close"].iloc[-1])
    day_open = float(today_bars["Open"].iloc[0])

    # Compute features
    opening_range = compute_opening_range(intra_ist)
    levels = compute_intraday_levels(daily_df)
    vol_ratio = compute_volume_ratio(intra_ist)
    current_vol_ratio = float(vol_ratio.iloc[-1]) if not vol_ratio.empty and not np.isnan(vol_ratio.iloc[-1]) else 1.0

    # Get sector data (needed for both regime classification and mean-revert)
    sym_sector = cfg.get("sector", "")
    sym_sector_df = sector_data.get(sym_sector, pd.DataFrame())

    # Classify symbol regime (with Nifty for relative strength + sector RS)
    nifty_daily = nifty_state.get("nifty_daily")
    symbol_regime = classify_symbol_regime(
        daily_df, intra_ist, nifty_daily=nifty_daily, sector_daily=sym_sector_df,
    )

    # Get eligible strategies
    day_type = day_type_info.get("type", "range_bound")
    eligible = get_eligible_strategies(day_type, symbol_regime)

    if not eligible:
        print(f"    [DEBUG] {symbol}: NO eligible strategies | day_type={day_type}, "
              f"trend={symbol_regime.get('trend')}, liquidity={symbol_regime.get('liquidity')}")
        return candidates

    _debug_parts = []  # collect per-strategy rejection reasons

    # Run each eligible strategy
    for strategy_name in eligible:
        # Time-relevance check — skip expired strategy windows
        time_rel = compute_time_relevance(strategy_name, now_ist=now_ist)
        if time_rel["status"] == "EXPIRED":
            _debug_parts.append(f"{strategy_name}=EXPIRED")
            continue

        candidate = None

        if strategy_name == "orb":
            candidate = evaluate_orb(symbol, intra_ist, daily_df, opening_range,
                                     day_type, symbol_regime)
        elif strategy_name == "pullback":
            candidate = evaluate_pullback(symbol, intra_ist, daily_df, symbol_regime)
        elif strategy_name == "compression":
            candidate = evaluate_compression(symbol, intra_ist, daily_df, symbol_regime)
        elif strategy_name == "mean_revert":
            candidate = evaluate_mean_revert(symbol, intra_ist, daily_df, symbol_regime,
                                             day_type, sector_df=sym_sector_df)
        elif strategy_name == "swing":
            candidate = evaluate_swing(symbol, intra_ist, daily_df, symbol_regime)
        elif strategy_name == "mlr":
            candidate = evaluate_mlr(symbol, intra_ist, daily_df, opening_range,
                                      symbol_regime, day_type, mlr_config=_mlr_config)

        if candidate is None:
            _debug_parts.append(f"{strategy_name}=no_setup")
            continue

        # ── Bug Fix #4: Minimum RR gate ──
        if candidate["rr_ratio"] < MIN_RR_RATIO:
            _debug_parts.append(f"{strategy_name}=RR_{candidate['rr_ratio']:.1f}<{MIN_RR_RATIO}")
            continue  # discard sub-threshold RR before scoring

        # ── LONG_ONLY filter: skip short/SELL setups in equity cash segment ──
        if LONG_ONLY and candidate.get("direction") == "short":
            _debug_parts.append(f"{strategy_name}=short_blocked")
            continue

        # Enrich candidate with symbol metadata
        candidate["symbol"] = symbol
        candidate["name"] = cfg.get("name", symbol)
        candidate["sector"] = cfg.get("sector", "")
        candidate["ltp"] = ltp
        candidate["change_pct"] = (ltp / day_open - 1) * 100 if day_open > 0 else 0
        candidate["symbol_regime"] = symbol_regime
        candidate["day_type"] = day_type

        # ── Compute VWAP distance in ATR units (continuous) ──
        vwap_val = float(today_bars["vwap"].iloc[-1]) if "vwap" in today_bars.columns else np.nan
        direction = candidate.get("direction", "long")

        # ATR for normalization (compute_atr returns a scalar)
        try:
            _atr_raw = compute_atr_series(today_bars, period=14) if len(today_bars) >= 14 else None
            atr_val = float(_atr_raw) if _atr_raw is not None and not np.isnan(_atr_raw) else (ltp * 0.02)
        except Exception:
            atr_val = ltp * 0.02
        vwap_distance_atrs = (ltp - vwap_val) / atr_val if atr_val > 0 and not np.isnan(vwap_val) else 0.0

        # ── Hard filters (mechanical/fundamental only) ──
        is_mlr = candidate["strategy"] == "mlr"
        is_illiquid = symbol_regime.get("liquidity") == "illiquid"

        # DOW/month-period adjustments
        _now = now_ist or datetime.now(IST)
        dow = _now.weekday()
        dow_name = DOW_NAMES.get(dow, "Unknown")
        month_period = classify_month_period(_now)

        dow_wr = 50.0
        mp_wr = 50.0
        overall_wr = 50.0

        if dow_month_stats:
            overall_data = dow_month_stats.get("overall", {})
            overall_wr = overall_data.get("win_rate", 50.0)

            dow_data = dow_month_stats.get(dow_name, {})
            dow_all = dow_data.get("all", {})
            dow_n = dow_all.get("sample_size", dow_all.get("n", 0))
            dow_wr = dow_all.get("win_rate", overall_wr) if dow_n >= 10 else overall_wr

            mp_data = dow_data.get(month_period, {})
            mp_n = mp_data.get("sample_size", mp_data.get("n", 0))
            mp_wr = mp_data.get("win_rate", overall_wr) if mp_n >= 10 else overall_wr

        candidate["dow_name"] = dow_name
        candidate["dow_wr"] = dow_wr
        candidate["month_period"] = month_period
        candidate["month_period_wr"] = mp_wr

        # Target scaling by DOW/month factors (capped ±20% to prevent absurd targets)
        dow_factor = dow_wr / overall_wr if overall_wr > 0 else 1.0
        mp_factor = mp_wr / overall_wr if overall_wr > 0 else 1.0
        combined_factor = max(0.8, min(1.2, dow_factor * mp_factor))
        candidate["target_price"] = round(
            candidate["entry_price"] + (candidate["target_price"] - candidate["entry_price"]) * combined_factor,
            2,
        )
        # Recalculate target_pct and rr_ratio
        if candidate["entry_price"] > 0:
            candidate["target_pct"] = round(
                abs(candidate["target_price"] - candidate["entry_price"]) / candidate["entry_price"] * 100, 2
            )
            candidate["rr_ratio"] = round(
                candidate["target_pct"] / candidate["stop_pct"], 2
            ) if candidate["stop_pct"] > 0 else 0

        # ── Convergence score (computed BEFORE scoring, not after) ──
        conv = compute_convergence_score(candidate, today_bars, daily_df, symbol_regime)
        candidate["convergence_score"] = conv["score"]
        candidate["convergence_detail"] = f"{conv['n_aligned']}/{conv['total']} ({', '.join(conv['aligned'])})"

        # ── Historical hit rate ──
        hist = compute_historical_hit_rate(symbol, daily_df, candidate["strategy"],
                                           direction, day_type, dow_name)
        candidate["historical_hit_rate"] = hist["hit_rate"]
        candidate["historical_sample_size"] = hist["sample_size"]
        candidate["historical_context"] = hist["context"]

        # ── News sentiment ──
        sym_news = (news_data or {}).get(symbol, {})
        news_sentiment = sym_news.get("sentiment", 0)
        has_material = sym_news.get("has_material_event", False)
        candidate["news_sentiment"] = news_sentiment
        candidate["news_summary"] = sym_news.get("summary", "")
        candidate["_news_avoid"] = (has_material and (
            (direction == "long" and news_sentiment < -0.3) or
            (direction == "short" and news_sentiment > 0.3)))

        # ── Counter-trend strength ──
        # Use the value from classify_symbol_regime (daily data, more robust),
        # enhanced with intraday divergence if available
        counter_trend = symbol_regime.get("counter_trend_strength", 0.0)
        nifty_ist = nifty_state.get("nifty_ist")
        if nifty_ist is not None and not nifty_ist.empty:
            nifty_today = nifty_ist[nifty_ist.index.date == today]
            if not nifty_today.empty and len(nifty_today) >= 2:
                nifty_ret = (float(nifty_today["Close"].iloc[-1]) / float(nifty_today["Open"].iloc[0]) - 1) * 100
                stock_ret = candidate["change_pct"]
                intra_ct = 0.0
                if nifty_ret < -0.3 and stock_ret > 0.3:
                    intra_ct = min(1.0, (stock_ret - nifty_ret) / 3.0)
                elif nifty_ret > 0.3 and stock_ret < -0.3:
                    intra_ct = min(1.0, (nifty_ret - stock_ret) / 3.0)
                # Take the stronger of daily and intraday counter-trend signals
                counter_trend = max(counter_trend, intra_ct)
        candidate["counter_trend_strength"] = round(counter_trend, 3)

        # ── Market context (from nifty_state or compute fresh) ──
        market_ctx = nifty_state.get("market_ctx")
        if market_ctx is None:
            vix_val_local, _ = vix_info
            market_ctx = compute_market_context_scores(
                nifty_state.get("nifty_daily", pd.DataFrame()),
                vix_val_local,
                nifty_state.get("institutional_flow", "neutral"),
                nifty_state.get("regime_strength", 0.5),
            )

        # ── Time-relevance ──
        candidate["time_status"] = time_rel["note"]
        candidate["time_window_status"] = time_rel["status"]

        # ── Compute composite score ──
        composite, factor_scores = compute_composite_score(
            candidate, market_ctx, symbol_regime, conv["score"],
            dow_wr, mp_wr, news_sentiment, vwap_distance_atrs,
            counter_trend,
        )
        candidate["score"] = composite
        candidate["factor_scores"] = factor_scores

        # ── Signal tier assignment (simple thresholds, no binary gates) ──
        vix_val, vix_regime = vix_info

        # Hard filters only: mechanical and fundamental
        near_earnings = False
        earnings_date = ""
        if not skip_earnings_check:
            near_earnings, earnings_date = check_earnings_proximity(symbol, days_ahead=3)

        if candidate.get("_news_avoid"):
            candidate["signal"] = "AVOID"
            candidate["signal_reason"] = f"Material news opposes {direction} (sentiment {news_sentiment:+.1f})"
        elif near_earnings:
            candidate["signal"] = "AVOID"
            candidate["signal_reason"] = f"Earnings on {earnings_date}"
        elif is_illiquid:
            candidate["signal"] = "AVOID"
            candidate["signal_reason"] = "Illiquid stock"
        elif composite >= 0.68:
            candidate["signal"] = "STRONG"
            candidate["signal_reason"] = f"Composite {composite:.0%} — strong multi-factor alignment"
        elif composite >= 0.52:
            candidate["signal"] = "ACTIVE"
            candidate["signal_reason"] = f"Composite {composite:.0%} — adequate edge"
        elif composite >= 0.38:
            candidate["signal"] = "WATCH"
            candidate["signal_reason"] = f"Composite {composite:.0%} — monitor"
        else:
            candidate["signal"] = "AVOID"
            candidate["signal_reason"] = f"Composite {composite:.0%} — insufficient edge"

        # Position sizing hints
        candidate["size_multiplier"] = 0.5 if candidate["strategy"] == "swing" else 1.0
        if month_period == "expiry_week":
            candidate["size_multiplier"] *= 0.7

        # Keep gates dict for backward compatibility (reporting)
        candidate["gates"] = {
            "vwap_alignment": f"{vwap_distance_atrs:+.2f} ATR",
            "market_regime": f"{market_ctx.get('regime_score', 0.5):.2f}",
            "liquidity": "normal" if not is_illiquid else "illiquid",
        }

        _debug_parts.append(f"{strategy_name}={candidate['signal']}({composite:.0%})")
        candidates.append(candidate)

    # Print debug summary for this symbol
    if _debug_parts:
        print(f"    [DEBUG] {symbol}: eligible={eligible} | {', '.join(_debug_parts)}")

    return candidates


# ── Signal Ranking ───────────────────────────────────────────────────────

def rank_signals(all_candidates, nifty_regime="range"):
    """Rank all candidates with universe-level relative strength.

    Institutional approach: rank candidates not just by individual score
    but by how they compare to the rest of the universe. In a bearish market,
    stocks bucking the trend get a bigger RS boost because that outperformance
    is rarer and more meaningful.

    Sort: STRONG > ACTIVE > WATCH; within tier by RS-boosted composite.
    """
    signal_order = {"STRONG": 0, "ACTIVE": 1, "WATCH": 2, "AVOID": 3}

    # Compute universe-level relative strength percentile
    non_avoid = [c for c in all_candidates if c.get("signal") != "AVOID"]
    if len(non_avoid) >= 2:
        changes = sorted([c.get("change_pct", 0) for c in non_avoid])
        n = len(changes)
        for c in non_avoid:
            change = c.get("change_pct", 0)
            # Percentile rank within the candidate universe
            rank_pos = sum(1 for ch in changes if ch <= change)
            rs_percentile = rank_pos / n  # 0.0 = weakest, 1.0 = strongest

            # In bearish markets, RS matters MORE (outperformance is rarer)
            rs_weight = 0.3 if nifty_regime == "bearish" else 0.15
            c["_rs_boost"] = round(c.get("score", 0) * (1 + rs_weight * rs_percentile), 3)
            c["rs_percentile"] = round(rs_percentile, 2)
    else:
        for c in non_avoid:
            c["_rs_boost"] = c.get("score", 0)
            c["rs_percentile"] = 0.5

    for c in all_candidates:
        if c.get("signal") == "AVOID":
            c["_rs_boost"] = 0
            c["rs_percentile"] = 0

    return sorted(
        all_candidates,
        key=lambda c: (
            signal_order.get(c.get("signal", "AVOID"), 4),
            -(c.get("_rs_boost", 0) * c.get("rr_ratio", 0)),
        ),
    )


# ── Position Management ─────────────────────────────────────────────────

def manage_positions(open_positions, current_bars, now_ist):
    """Position management for open trades.

    Returns list of action dicts:
      - TRAIL: move stop to breakeven or trail
      - LUNCH_EXIT: exit if < 0.3x target during lunch
      - HARD_EXIT: close all at 15:00 (except swing_hold positions)
      - STOP_HIT / TARGET_HIT: price hit levels
    """
    actions = []
    t = now_ist.time()

    for pos in open_positions:
        sym = pos["symbol"]
        entry = pos["entry_price"]
        stop = pos["stop_price"]
        target = pos["target_price"]
        direction = pos.get("direction", "long")
        is_swing = pos.get("swing_hold", False)

        bar = current_bars.get(sym)
        if bar is None:
            continue

        ltp = float(bar["Close"])
        target_dist = abs(target - entry)

        # Check stop/target hit
        if direction == "long":
            if ltp <= stop:
                actions.append({"symbol": sym, "action": "STOP_HIT", "ltp": ltp, "stop": stop})
                continue
            if ltp >= target:
                actions.append({"symbol": sym, "action": "TARGET_HIT", "ltp": ltp, "target": target})
                continue
            progress = (ltp - entry) / target_dist if target_dist > 0 else 0
        else:
            if ltp >= stop:
                actions.append({"symbol": sym, "action": "STOP_HIT", "ltp": ltp, "stop": stop})
                continue
            if ltp <= target:
                actions.append({"symbol": sym, "action": "TARGET_HIT", "ltp": ltp, "target": target})
                continue
            progress = (entry - ltp) / target_dist if target_dist > 0 else 0

        # Hard exit at 15:00 — exempt swing_hold positions
        if t >= EXIT_DEADLINE and not is_swing:
            actions.append({"symbol": sym, "action": "HARD_EXIT", "ltp": ltp,
                            "reason": "Exit deadline 15:00"})
            continue

        # Lunch window exit if low progress (not for swings)
        if not is_swing and LUNCH_WINDOW[0] <= t <= LUNCH_WINDOW[1] and progress < 0.3:
            actions.append({"symbol": sym, "action": "LUNCH_EXIT", "ltp": ltp,
                            "progress": f"{progress:.0%}"})
            continue

        # Trail stop
        if progress >= 0.75:
            if direction == "long":
                new_stop = entry + 0.5 * target_dist
            else:
                new_stop = entry - 0.5 * target_dist
            actions.append({"symbol": sym, "action": "TRAIL", "ltp": ltp,
                            "new_stop": round(new_stop, 2), "progress": f"{progress:.0%}"})
        elif progress >= 0.5:
            actions.append({"symbol": sym, "action": "BREAKEVEN", "ltp": ltp,
                            "new_stop": entry, "progress": f"{progress:.0%}"})

    return actions
