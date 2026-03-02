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

from common.data import TICKERS, PROJECT_ROOT
from common.indicators import compute_vwap, _to_ist
from common.market import check_earnings_proximity
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

    # Classify symbol regime (with Nifty for relative strength)
    nifty_daily = nifty_state.get("nifty_daily")
    symbol_regime = classify_symbol_regime(daily_df, intra_ist, nifty_daily=nifty_daily)

    # Get eligible strategies
    day_type = day_type_info.get("type", "range_bound")
    eligible = get_eligible_strategies(day_type, symbol_regime)

    if not eligible:
        return candidates

    # Get sector data for mean-revert sector check
    sym_sector = cfg.get("sector", "")
    sym_sector_df = sector_data.get(sym_sector, pd.DataFrame())

    # Run each eligible strategy
    for strategy_name in eligible:
        # Time-relevance check — skip expired strategy windows
        time_rel = compute_time_relevance(strategy_name, now_ist=now_ist)
        if time_rel["status"] == "EXPIRED":
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
            continue

        # ── Bug Fix #4: Minimum RR gate ──
        if candidate["rr_ratio"] < MIN_RR_RATIO:
            continue  # discard sub-threshold RR before scoring

        # ── LONG_ONLY filter: skip short/SELL setups in equity cash segment ──
        if LONG_ONLY and candidate.get("direction") == "short":
            continue

        # Enrich candidate with symbol metadata
        candidate["symbol"] = symbol
        candidate["name"] = cfg.get("name", symbol)
        candidate["sector"] = cfg.get("sector", "")
        candidate["ltp"] = ltp
        candidate["change_pct"] = (ltp / day_open - 1) * 100 if day_open > 0 else 0
        candidate["symbol_regime"] = symbol_regime
        candidate["day_type"] = day_type

        # ── Bug Fix #1: Direction-aware VWAP gate ──
        vwap_val = float(today_bars["vwap"].iloc[-1]) if "vwap" in today_bars.columns else np.nan
        direction = candidate.get("direction", "long")
        if not np.isnan(vwap_val):
            if direction == "long":
                vwap_gate = ltp > vwap_val
            else:
                vwap_gate = ltp < vwap_val
        else:
            vwap_gate = False

        # Must-have gates
        nifty_regime = nifty_state.get("regime", "unknown")
        nifty_new_lows = nifty_state.get("new_lows", False)
        nifty_ok = (nifty_regime != "bearish") and (not nifty_new_lows)

        # MLR is exempt from nifty_ok gate (works in bearish markets)
        # and vwap_gate (buys stocks recovering FROM below VWAP — the strategy
        # itself checks VWAP reclaim as condition #5)
        is_mlr = candidate["strategy"] == "mlr"

        gates = {
            "vwap_gate": vwap_gate if not is_mlr else True,
            "nifty_ok": nifty_ok if not is_mlr else True,
            "not_illiquid": symbol_regime.get("liquidity") != "illiquid",
        }
        candidate["gates"] = gates
        gates_pass = all(gates.values())

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
            dow_wr = dow_all.get("win_rate", overall_wr)

            mp_data = dow_data.get(month_period, {})
            mp_wr = mp_data.get("win_rate", overall_wr)

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

        # Weighted confidence score
        raw_conf = candidate["confidence"]
        score_adjustments = 0.0
        if vwap_gate:
            score_adjustments += 0.05
        if nifty_ok:
            score_adjustments += 0.05
        if dow_wr > 55:
            score_adjustments += 0.05
        elif dow_wr < 45:
            score_adjustments -= 0.05
        if mp_wr > 55:
            score_adjustments += 0.03
        elif mp_wr < 45:
            score_adjustments -= 0.03
        if candidate["rr_ratio"] >= 2.0:
            score_adjustments += 0.05

        # Momentum + relative strength bonuses
        if symbol_regime.get("momentum") == "accelerating":
            score_adjustments += 0.03
        elif symbol_regime.get("momentum") == "decelerating":
            score_adjustments -= 0.03
        if symbol_regime.get("relative_strength") == "outperforming":
            score_adjustments += 0.02

        # ── News sentiment adjustment ──
        sym_news = (news_data or {}).get(symbol, {})
        news_sentiment = sym_news.get("sentiment", 0)
        has_material = sym_news.get("has_material_event", False)
        candidate["news_sentiment"] = news_sentiment
        candidate["news_summary"] = sym_news.get("summary", "")

        if has_material and (
            (direction == "long" and news_sentiment < -0.3) or
            (direction == "short" and news_sentiment > 0.3)
        ):
            # Material news opposes trade direction — force AVOID later
            candidate["_news_avoid"] = True
        else:
            candidate["_news_avoid"] = False

        if news_sentiment > 0.5 and direction == "long":
            score_adjustments += 0.05
        elif news_sentiment < -0.5 and direction == "long":
            score_adjustments -= 0.05
        elif news_sentiment < -0.5 and direction == "short":
            score_adjustments += 0.05
        elif news_sentiment > 0.5 and direction == "short":
            score_adjustments -= 0.05

        # ── Convergence score ──
        conv = compute_convergence_score(candidate, today_bars, daily_df,
                                         symbol_regime)
        candidate["convergence_score"] = conv["score"]
        candidate["convergence_detail"] = (
            f"{conv['n_aligned']}/{conv['total']} "
            f"({', '.join(conv['aligned'])})"
        )

        candidate["_convergence_weak"] = False
        if conv["score"] > 70:
            score_adjustments += 0.08
        elif conv["score"] < 40:
            candidate["_convergence_weak"] = True

        # ── Historical hit rate ──
        hist = compute_historical_hit_rate(
            symbol, daily_df, candidate["strategy"],
            direction, day_type, dow_name,
        )
        candidate["historical_hit_rate"] = hist["hit_rate"]
        candidate["historical_sample_size"] = hist["sample_size"]
        candidate["historical_context"] = hist["context"]

        candidate["_history_weak"] = False
        if hist["sample_size"] >= 10 and hist["hit_rate"] > 60:
            score_adjustments += 0.05
        elif hist["sample_size"] >= 10 and hist["hit_rate"] < 40:
            candidate["_history_weak"] = True

        # ── Weekly trend alignment ──
        weekly_trend = symbol_regime.get("weekly_trend", "sideways")
        daily_trend = symbol_regime.get("trend", "sideways")
        strategy_name = candidate["strategy"]
        # If weekly and daily disagree for trend-following strategies → reduce confidence
        if strategy_name in ("orb", "pullback", "swing"):
            if daily_trend in ("strong_up", "mild_up") and weekly_trend == "down":
                score_adjustments -= 0.05
            elif daily_trend in ("strong_down", "mild_down") and weekly_trend == "up":
                score_adjustments -= 0.05

        # ── Time-relevance adjustment (LIVE mode) ──
        score_adjustments += time_rel["penalty"]
        candidate["time_status"] = time_rel["note"]
        candidate["time_window_status"] = time_rel["status"]

        final_score = max(0, min(1.0, raw_conf + score_adjustments))
        candidate["score"] = round(final_score, 2)

        # VIX info
        vix_val, vix_regime = vix_info

        # Apply news/convergence/history overrides before tier assignment
        if candidate.get("_news_avoid"):
            candidate["signal"] = "AVOID"
            candidate["signal_reason"] = f"Material news opposes {direction} (sentiment {news_sentiment:+.1f})"
        elif candidate.get("_convergence_weak"):
            candidate["signal"] = "WATCH"
            candidate["signal_reason"] = f"Weak convergence: {candidate['convergence_detail']}"
        elif candidate.get("_history_weak") and hist["sample_size"] >= 10:
            candidate["signal"] = "WATCH"
            candidate["signal_reason"] = f"Historical hit rate {hist['hit_rate']:.0f}% on {hist['sample_size']} samples"
        elif vix_regime == "stress":
            candidate["signal"] = "AVOID"
            candidate["signal_reason"] = f"VIX STRESS ({vix_val})"
        elif not gates_pass:
            failed = [k for k, v in gates.items() if not v]
            candidate["signal"] = "AVOID"
            candidate["signal_reason"] = f"Gate blocked: {', '.join(failed)}"
        elif dow_wr < 40 or mp_wr < 40:
            candidate["signal"] = "WATCH"
            candidate["signal_reason"] = f"DOW WR {dow_wr:.0f}% / Month WR {mp_wr:.0f}% too low"
        elif final_score >= 0.80 and candidate["rr_ratio"] >= 2.0:
            candidate["signal"] = "STRONG"
            candidate["signal_reason"] = f"Score {final_score:.0%}, RR {candidate['rr_ratio']:.1f}"
        elif final_score >= 0.65 and candidate["rr_ratio"] >= 1.5:
            candidate["signal"] = "ACTIVE"
            candidate["signal_reason"] = f"Score {final_score:.0%}, RR {candidate['rr_ratio']:.1f}"
        elif final_score >= 0.50:
            candidate["signal"] = "WATCH"
            candidate["signal_reason"] = f"Score {final_score:.0%} — borderline"
        else:
            candidate["signal"] = "AVOID"
            candidate["signal_reason"] = f"Score {final_score:.0%} too low"

        # Earnings check (skip in backtest — makes network calls)
        near_earnings = False
        earnings_date = ""
        if not skip_earnings_check:
            near_earnings, earnings_date = check_earnings_proximity(symbol, days_ahead=3)
        if near_earnings:
            candidate["signal"] = "AVOID"
            candidate["signal_reason"] = f"Earnings on {earnings_date}"

        # Position sizing hint for swing (wider stop → smaller size)
        candidate["size_multiplier"] = 0.5 if candidate["strategy"] == "swing" else 1.0
        # Expiry week sizing reduction
        if month_period == "expiry_week":
            candidate["size_multiplier"] *= 0.7

        candidates.append(candidate)

    return candidates


# ── Signal Ranking ───────────────────────────────────────────────────────

def rank_signals(all_candidates):
    """Rank all candidates across symbols.

    Sort: STRONG > ACTIVE > WATCH; within tier by score * rr_ratio.
    Apply portfolio risk overlays.
    """
    signal_order = {"STRONG": 0, "ACTIVE": 1, "WATCH": 2, "AVOID": 3}

    return sorted(
        all_candidates,
        key=lambda c: (
            signal_order.get(c.get("signal", "AVOID"), 4),
            -(c.get("score", 0) * c.get("rr_ratio", 0)),
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
