"""
Multi-Ticker Scalp Scanner
Run: python -m scalp.scanner
Loads scalp_config.yaml, evaluates all tickers, prints unified dashboard,
calls LLM for full advisory.
"""

import os
import warnings
from datetime import datetime, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from common.data import fetch_yf, fetch_bulk, GAP_THRESHOLDS, SCALP_CONFIG_PATH, SCALP_REPORT_DIR

CONFIG_PATH = SCALP_CONFIG_PATH
from common.indicators import compute_vwap, compute_atr, compute_atr_percentile, _to_ist, classify_gaps
from common.market import (
    fetch_india_vix, vix_position_scale, detect_nifty_regime,
    check_earnings_proximity, nifty_making_new_lows, higher_lows_pattern,
    outperforming_nifty,
)
from common.risk import (
    compute_position_size, compute_correlation_clusters,
    NSE_ROUND_TRIP_COST_PCT,
    MAX_RISK_PER_TRADE_PCT, CORR_CLUSTER_THRESHOLD, CORR_LOOKBACK_DAYS,
)
from common.display import fmt, box_top, box_mid, box_bot, box_line, W

warnings.filterwarnings("ignore")

IST = ZoneInfo("Asia/Kolkata")

# ── Risk constants ─────────────────────────────────────────────────────────
MAX_SECTOR_EXPOSURE = 2         # max concurrent trades from same sector/regime tag
MAX_DAILY_DRAWDOWN_PCT = 1.5    # hard stop: cease trading if daily loss exceeds this
DEFAULT_MAX_HOLD_MINUTES = 45   # time-exit: close stale positions after this
BREAKEVEN_TRIGGER = 0.5         # move stop to breakeven when profit reaches this × target
TRAIL_TRIGGER = 0.75            # start trailing stop when profit reaches this × target
REENTRY_COOLDOWN_MINUTES = 30   # block re-entry on same ticker within N min after exit

# Volume seasonality multipliers (relative to daily median per window)
VOLUME_SEASONALITY = {
    "09:15-10:00": 2.0,   # morning is naturally 2x daily avg
    "10:00-11:30": 1.3,
    "11:30-12:30": 0.8,
    "12:30-13:30": 0.6,   # lunch break — lowest liquidity
    "13:30-14:30": 0.8,   # pre-close buildup
    "14:30-15:15": 1.2,
}

# ── Phase helpers ───────────────────────────────────────────────────────────

PHASE_LABELS = {
    "PRE_MARKET":        "Pre-Market (before 09:15)",
    "AVOID_ZONE":        "Avoid Zone (09:15-09:30)",
    "MORNING_SCALP":     "Morning Scalp (09:30-10:30)",
    "LATE_MORNING":      "Late Morning (10:30-11:30)",
    "LUNCH_HOUR":        "Lunch Hour (11:30-12:30)",
    "EARLY_AFTERNOON":   "Early Afternoon (12:30-13:30)",
    "PRE_CLOSE_SETUP":   "Pre-Close Setup (13:30-14:30)",
    "AFTERNOON_SCALP":   "Afternoon Scalp (14:30-15:15)",
    "CLOSING":           "Closing (15:15-15:30)",
    "POST_MARKET":       "Post-Market (after 15:30)",
}


def parse_time(s):
    """Parse 'HH:MM' string to time object."""
    h, m = map(int, s.split(":"))
    return dtime(h, m)


def get_phase(t, phases_cfg):
    """Determine market phase from config-defined phase windows."""
    for name, window in phases_cfg.items():
        start = parse_time(window["start"])
        end = parse_time(window["end"])
        if start <= t < end:
            return name
    return "POST_MARKET"


def get_next_phase(current_phase, phases_cfg):
    """Return (next_phase_name, start_time_str, end_time_str) or None if no next phase."""
    phase_names = list(phases_cfg.keys())
    try:
        idx = phase_names.index(current_phase)
    except ValueError:
        return None
    if idx + 1 < len(phase_names):
        next_name = phase_names[idx + 1]
        w = phases_cfg[next_name]
        return next_name, str(w["start"]), str(w["end"])
    return None


# ── Condition Computation ───────────────────────────────────────────────────

def today_gap(daily_df):
    """Classify today's gap from daily bars."""
    gdf = classify_gaps(daily_df)
    if gdf.empty:
        return "unknown", 0.0
    last = gdf.iloc[-1]
    return last["gap_type"], last["gap_pct"] * 100


def vwap_reclaim_check(intra_today):
    """Check if price crossed above VWAP and held for 2 completed bars.

    No look-ahead: only uses bars that have already closed.
    Requires crossing from below to above AND staying above for 2 full bars.
    """
    if "vwap" not in intra_today.columns or len(intra_today) < 3:
        return False
    above = intra_today["Close"] > intra_today["vwap"]
    # Need at least 2 consecutive bars above VWAP after a cross from below
    for i in range(2, len(above)):
        was_below = not above.iloc[i - 2]
        held_above = above.iloc[i - 1] and above.iloc[i]
        if was_below and held_above:
            return True
    return False


def opening_low(intra_ist):
    """Low of first 3x5-min candles (09:15-09:30)."""
    today = intra_ist.index[-1].date()
    mask = (intra_ist.index.date == today) & (intra_ist.index.time < dtime(9, 30))
    bars = intra_ist[mask]
    if bars.empty:
        today_bars = intra_ist[intra_ist.index.date == today]
        bars = today_bars.head(3)
    return bars["Low"].min() if not bars.empty else np.nan


def check_higher_low(intra_ist, op_low):
    """Current swing low > opening low."""
    if np.isnan(op_low):
        return False, np.nan
    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]
    if len(today_bars) < 4:
        return False, np.nan
    recent_low = today_bars["Low"].iloc[-6:].min() if len(today_bars) >= 6 else today_bars["Low"].min()
    return recent_low > op_low, recent_low


def volume_regime(daily_df, current_window=None):
    """Today's volume vs 20-day median, adjusted for time-of-day seasonality.

    Morning bars naturally have 2-3x more volume than afternoon bars.
    Raw comparison to daily median would flag normal afternoon volume as 'Contraction'.
    """
    if len(daily_df) < 2:
        return "N/A", np.nan
    today_vol = daily_df["Volume"].iloc[-1]
    hist = daily_df["Volume"].iloc[-21:-1] if len(daily_df) > 20 else daily_df["Volume"].iloc[:-1]
    median_20 = hist.median()
    if median_20 == 0 or np.isnan(median_20):
        return "N/A", np.nan

    # Adjust for time-of-day seasonality
    seasonality = VOLUME_SEASONALITY.get(current_window, 1.0) if current_window else 1.0
    adjusted_median = median_20 * seasonality

    ratio = today_vol / adjusted_median
    if ratio >= 1.5:
        return "Expansion", ratio
    elif ratio <= 0.5:
        return "Contraction", ratio
    return "Normal", ratio


# ── Evaluate a single ticker ───────────────────────────────────────────────

def evaluate_ticker(ticker_cfg, intra_df, daily_df, nifty_ist, nifty_ok, phase, now_ist,
                     next_phase_info=None):
    """Evaluate all conditions for one ticker. Returns a state dict.

    next_phase_info: optional (next_phase_name, start_str, end_str) tuple.
    """
    symbol = ticker_cfg["symbol"]
    result = {
        "symbol": symbol,
        "name": ticker_cfg["name"],
        "enabled": ticker_cfg.get("enabled", True),
        "direction": ticker_cfg.get("direction", "long"),
        "edge_strength": ticker_cfg.get("edge_strength", 3),
        "phase_eligible": False,
        "signal": "NO_DATA",
        "conditions": {},
        "ltp": np.nan,
        "change_pct": np.nan,
        "gap_type": "unknown",
        "gap_pct": 0.0,
        "vwap_val": np.nan,
        "atr_val": np.nan,
        "atr_pct": np.nan,
        "entry_price": np.nan,
        "target_price": np.nan,
        "stop_price": np.nan,
        "action_text": "",
        "conditions_met": 0,
        "conditions_total": 0,
        # Next-phase lookahead
        "next_phase": None,
        "next_phase_window": None,
        "next_phase_eligible": False,
        "next_phase_gap_ok": False,
    }

    if not ticker_cfg.get("enabled", True):
        result["signal"] = "DISABLED"
        result["action_text"] = "Disabled in config"
        return result

    if intra_df.empty or daily_df.empty:
        result["action_text"] = "No data available"
        return result

    # Convert to IST + VWAP
    intra_ist = _to_ist(intra_df)
    if not isinstance(intra_ist.index, pd.DatetimeIndex):
        result["action_text"] = "Bad index format"
        return result
    intra_ist = compute_vwap(intra_ist)
    today = now_ist.date()
    intra_today = intra_ist[intra_ist.index.date == today]
    if intra_today.empty:
        intra_today = intra_ist.tail(20)
    if intra_today.empty:
        result["action_text"] = "No intraday bars"
        return result

    # Basic price info
    ltp = intra_today["Close"].iloc[-1]
    day_open = intra_today["Open"].iloc[0]
    change_pct = (ltp / day_open - 1) * 100 if day_open != 0 else np.nan
    result["ltp"] = ltp
    result["change_pct"] = change_pct

    # Gap
    gap_type, gap_pct = today_gap(daily_df)
    result["gap_type"] = gap_type
    result["gap_pct"] = gap_pct

    # VWAP
    vwap_val = intra_today["vwap"].iloc[-1] if "vwap" in intra_today.columns else np.nan
    above_vwap = ltp > vwap_val if not np.isnan(vwap_val) else False
    vwap_dist_pct = (ltp - vwap_val) / vwap_val * 100 if not np.isnan(vwap_val) and vwap_val > 0 else 0.0
    result["vwap_val"] = vwap_val
    result["vwap_dist_pct"] = round(vwap_dist_pct, 3)

    # VWAP reclaim
    vwap_reclaimed = vwap_reclaim_check(intra_today)

    # Higher low
    op_low = opening_low(intra_ist)
    has_hl, swing_low = check_higher_low(intra_ist, op_low)

    # ATR
    atr_val = compute_atr(daily_df) if len(daily_df) >= 14 else np.nan
    atr_pct = atr_val / ltp * 100 if not np.isnan(atr_val) and ltp > 0 else np.nan
    atr_rank = compute_atr_percentile(daily_df) if len(daily_df) >= 14 else 50.0
    result["atr_val"] = atr_val
    result["atr_pct"] = atr_pct
    result["atr_rank"] = atr_rank

    # Day range
    day_range_pct = (intra_today["High"].max() - intra_today["Low"].min()) / day_open * 100 if day_open > 0 else 0

    # Volume (with time-of-day seasonality adjustment)
    # Map current phase back to window for seasonality lookup
    _phase_to_window = {"MORNING_SCALP": "09:15-10:00", "LATE_MORNING": "10:00-11:30",
                        "LUNCH_HOUR": "11:30-12:30", "EARLY_AFTERNOON": "12:30-13:30",
                        "PRE_CLOSE_SETUP": "13:30-14:30", "AFTERNOON_SCALP": "14:30-15:15"}
    current_window = _phase_to_window.get(phase)
    vol_tag, vol_ratio = volume_regime(daily_df, current_window)

    # Move from open
    move_from_open = abs(change_pct) if not np.isnan(change_pct) else 0

    # RS vs Nifty
    rs_positive = outperforming_nifty(intra_ist, nifty_ist) if not nifty_ist.empty else False

    # ── Check phase eligibility ──
    active_phases = ticker_cfg.get("active_phases", [])
    avoid_phases = ticker_cfg.get("avoid_phases", [])
    entry_conds = ticker_cfg.get("entry_conditions", {})
    risk = ticker_cfg.get("risk", {})

    if phase in avoid_phases:
        result["signal"] = "AVOID"
        result["action_text"] = f"Avoid zone — negative edge"
        return result

    phase_eligible = phase in active_phases
    result["phase_eligible"] = phase_eligible

    # ── Next-phase lookahead ──
    if next_phase_info:
        np_name, np_start, np_end = next_phase_info
        result["next_phase"] = np_name
        result["next_phase_window"] = f"{np_start}-{np_end}"
        result["next_phase_eligible"] = np_name in active_phases and np_name not in avoid_phases
        np_gap_rule = ticker_cfg.get("gap_rules", {}).get(np_name, {})
        np_preferred = np_gap_rule.get("preferred_gaps", [])
        result["next_phase_gap_ok"] = gap_type in np_preferred if np_preferred else True

    # ── Check gap preference for current phase ──
    gap_rules = ticker_cfg.get("gap_rules", {})
    phase_gap_rule = gap_rules.get(phase, {})
    preferred_gaps = phase_gap_rule.get("preferred_gaps", [])
    gap_ok = gap_type in preferred_gaps if preferred_gaps else True

    # ── Build conditions checklist ──
    conditions = {}
    conditions["gap_preferred"] = gap_ok
    conditions["above_vwap"] = above_vwap
    conditions["vwap_reclaimed"] = vwap_reclaimed if entry_conds.get("require_vwap_reclaim", False) else True
    conditions["higher_low"] = has_hl if entry_conds.get("require_higher_low", False) else True
    conditions["nifty_ok"] = nifty_ok if entry_conds.get("require_nifty_ok", False) else True

    min_vol = entry_conds.get("min_volume_ratio", 0)
    conditions["volume_ok"] = vol_ratio >= min_vol if not np.isnan(vol_ratio) else False

    min_range = entry_conds.get("min_range_multiple_of_atr", 0)
    range_ok = (day_range_pct >= atr_pct * min_range) if not np.isnan(atr_pct) else (min_range == 0)
    conditions["range_ok"] = range_ok

    max_move = entry_conds.get("max_move_from_open_pct", 999)
    conditions["move_not_extended"] = move_from_open <= max_move

    if entry_conds.get("require_intraday_rs_positive", False):
        conditions["rs_positive"] = rs_positive

    if entry_conds.get("require_volume_expansion", False):
        conditions["volume_expansion"] = vol_tag == "Expansion"

    result["conditions"] = conditions

    # ── Weighted condition scoring ──
    # Must-have gates: if ANY fails, cannot be ACTIVE
    must_have = ["gap_preferred", "above_vwap", "nifty_ok"]
    # Weighted nice-to-haves
    # Continuous VWAP weight: 0 if below, scales 0→1 as dist goes 0→0.3%, caps at 1.0
    vwap_continuous_weight = min(1.0, max(0.0, vwap_dist_pct / 0.3)) if vwap_dist_pct > 0 else 0.0

    # ATR percentile weight: expanding vol (P50+) favors scalps, compressed (P30-) hurts
    # Maps [20, 80] → [0, 1], so P50 = 0.5, P80+ = 1.0, P20- = 0.0
    atr_pctl_weight = min(1.0, max(0.0, (atr_rank - 20) / 60))

    condition_weights = {
        "gap_preferred": 3.0,   # critical
        "above_vwap": 2.5,     # gate stays binary, weight uses continuous below
        "nifty_ok": 2.5,
        "vwap_reclaimed": 2.0,
        "higher_low": 1.5,
        "volume_ok": 1.0,
        "range_ok": 1.0,
        "move_not_extended": 1.5,
        "rs_positive": 0.5,
        "volume_expansion": 0.5,
    }

    met = sum(conditions.values())
    total = len(conditions)
    result["conditions_met"] = met
    result["conditions_total"] = total

    # Weighted score (0-1): continuous weights for VWAP and ATR percentile
    # ATR percentile is an additive bonus (weight 1.0) not tied to a binary condition
    atr_bonus_weight = 1.0
    total_weight = sum(condition_weights.get(k, 1.0) for k in conditions) + atr_bonus_weight
    weighted_score = atr_bonus_weight * atr_pctl_weight  # start with ATR bonus
    for k, v in conditions.items():
        w = condition_weights.get(k, 1.0)
        if k == "above_vwap":
            weighted_score += w * vwap_continuous_weight
        elif v:
            weighted_score += w
    result["weighted_score"] = weighted_score / total_weight if total_weight > 0 else 0

    # Check must-have gates
    must_have_pass = all(conditions.get(k, True) for k in must_have if k in conditions)

    # ── Compute entry/target/stop (ATR-adaptive when available) ──
    target_pct = risk.get("base_target_pct", 1.0)
    stop_pct = risk.get("base_stop_pct", 1.5)

    # Use ATR multiples if available and ATR is computed
    atr_target_mult = risk.get("atr_target_multiple")
    atr_stop_mult = risk.get("atr_stop_multiple")
    if atr_target_mult and atr_stop_mult and not np.isnan(atr_pct) and atr_pct > 0:
        target_pct = round(atr_target_mult * atr_pct, 2)
        stop_pct = round(atr_stop_mult * atr_pct, 2)

    # MAE-informed stop tightening: if p90 of winners never drew down past
    # optimal_stop, use it (tighter stop = better RR without losing winners)
    mae = risk.get("mae_analysis", {})
    if mae:
        optimal_stop = mae.get("optimal_stop_pct")
        if optimal_stop and 0 < optimal_stop < stop_pct:
            stop_pct = round(optimal_stop, 2)

    # Deduct round-trip transaction costs from target for realistic RR
    effective_target_pct = max(target_pct - NSE_ROUND_TRIP_COST_PCT, 0.01)
    rr_ratio = round(effective_target_pct / stop_pct, 2) if stop_pct > 0 else 0

    result["entry_price"] = ltp
    result["target_price"] = ltp * (1 + effective_target_pct / 100)
    result["stop_price"] = ltp * (1 - stop_pct / 100)

    # ── Extra display data ──
    result["vol_tag"] = vol_tag
    result["vol_ratio"] = vol_ratio
    result["rs_positive"] = rs_positive
    result["op_low"] = op_low
    result["swing_low"] = swing_low
    result["day_range_pct"] = day_range_pct
    result["target_pct"] = round(effective_target_pct, 2)
    result["stop_pct"] = stop_pct
    result["rr_ratio"] = rr_ratio

    # ── Determine signal ──
    if phase == "CLOSING":
        result["signal"] = "EXIT"
        result["action_text"] = "Flatten all intraday by 15:15"
        return result

    if phase in ("PRE_MARKET", "POST_MARKET"):
        result["signal"] = "PREP"
        result["action_text"] = "Next session prep — see scenarios below"
        return result

    if not phase_eligible:
        result["signal"] = "WATCH"
        if result["next_phase_eligible"]:
            np_label = PHASE_LABELS.get(result["next_phase"], result["next_phase"])
            gap_note = ""
            if not result["next_phase_gap_ok"]:
                gap_note = f" (gap {gap_type} not preferred)"
            result["action_text"] = f"Not active now — upcoming in {np_label}{gap_note}"
        else:
            result["action_text"] = f"Not active in {phase} phase"
        return result

    if must_have_pass and met == total:
        result["signal"] = "ACTIVE"
        result["action_text"] = "SCALP LONG — All conditions met"
    elif must_have_pass and result["weighted_score"] >= 0.75:
        result["signal"] = "WATCH"
        failed = [k for k, v in conditions.items() if not v]
        result["action_text"] = f"Near-ready — missing: {', '.join(failed)}"
    elif not must_have_pass:
        failed_gates = [k for k in must_have if k in conditions and not conditions[k]]
        result["signal"] = "NO_TRADE"
        result["action_text"] = f"Gate blocked: {', '.join(failed_gates)}"
    elif not range_ok or vol_tag == "Contraction":
        result["signal"] = "STAND_ASIDE"
        result["action_text"] = "Low edge — range compression or volume contraction"
    elif must_have_pass and result["weighted_score"] >= 0.60:
        failed = [k for k, v in conditions.items() if not v]
        result["signal"] = "WATCH"
        result["action_text"] = f"Building — needs: {', '.join(failed)} (score {result['weighted_score']:.0%})"
    else:
        result["signal"] = "NO_TRADE"
        result["action_text"] = f"Conditions not met ({met}/{total}, score {result['weighted_score']:.0%})"

    return result


# ── Position P&L ────────────────────────────────────────────────────────────

def evaluate_positions(positions, ticker_states, phase, now_ist=None):
    """Compute P&L, urgency, trailing stops, and time-exits for open positions."""
    results = []
    if not positions:
        return results

    ltp_map = {s["symbol"]: s["ltp"] for s in ticker_states}
    for pos in positions:
        sym = pos["symbol"]
        ltp = ltp_map.get(sym, np.nan)
        entry = pos.get("entry_price", np.nan)
        qty = pos.get("quantity", 0)
        stop = pos.get("stop_loss", np.nan)
        target = pos.get("target", np.nan)
        direction = pos.get("direction", "long")
        max_hold = pos.get("max_hold_minutes", DEFAULT_MAX_HOLD_MINUTES)

        if direction == "long":
            pnl = (ltp - entry) * qty if not np.isnan(ltp) and not np.isnan(entry) else 0
            pnl_pct = (ltp / entry - 1) * 100 if entry > 0 and not np.isnan(ltp) else 0
        else:
            pnl = (entry - ltp) * qty if not np.isnan(ltp) and not np.isnan(entry) else 0
            pnl_pct = (entry / ltp - 1) * 100 if ltp > 0 and not np.isnan(entry) else 0

        # Compute profit as fraction of target distance
        if not np.isnan(target) and not np.isnan(entry) and target != entry:
            target_dist = abs(target - entry)
            current_profit = (ltp - entry) if direction == "long" else (entry - ltp)
            profit_ratio = current_profit / target_dist if target_dist > 0 else 0
        else:
            profit_ratio = 0

        # Time-exit check
        minutes_held = np.nan
        if now_ist and pos.get("entry_time"):
            try:
                entry_time = datetime.strptime(pos["entry_time"], "%Y-%m-%d %H:%M")
                entry_time = entry_time.replace(tzinfo=IST)
                minutes_held = (now_ist - entry_time).total_seconds() / 60
            except (ValueError, TypeError):
                pass

        # Dynamic stop recommendation
        recommended_stop = stop
        if profit_ratio >= TRAIL_TRIGGER:
            # Trail stop: entry + 25% of target distance
            trail_offset = 0.25 * abs(target - entry) if not np.isnan(target) else 0
            if direction == "long":
                recommended_stop = entry + trail_offset
            else:
                recommended_stop = entry - trail_offset
        elif profit_ratio >= BREAKEVEN_TRIGGER:
            # Move stop to breakeven + small buffer
            buffer = abs(entry) * 0.001  # 0.1% buffer
            recommended_stop = entry + buffer if direction == "long" else entry - buffer

        # Urgency determination (priority order)
        urgency = "HOLD"
        if not np.isnan(stop) and direction == "long" and ltp <= stop:
            urgency = "STOP HIT — EXIT NOW"
        elif not np.isnan(stop) and direction == "short" and ltp >= stop:
            urgency = "STOP HIT — EXIT NOW"
        elif not np.isnan(target) and direction == "long" and ltp >= target:
            urgency = "TARGET HIT — Book profits"
        elif not np.isnan(target) and direction == "short" and ltp <= target:
            urgency = "TARGET HIT — Book profits"
        elif phase == "CLOSING":
            urgency = "EXIT — Flatten by 15:15"
        elif not np.isnan(minutes_held) and minutes_held > max_hold:
            urgency = f"TIME EXIT — Held {int(minutes_held)}min ({pnl_pct:+.2f}%), edge decayed"
        elif profit_ratio >= TRAIL_TRIGGER:
            urgency = f"TRAIL STOP — Move stop to {fmt(recommended_stop)}"
        elif profit_ratio >= BREAKEVEN_TRIGGER:
            urgency = f"BREAKEVEN STOP — Move stop to {fmt(recommended_stop)}"
        elif not np.isnan(target) and direction == "long" and ltp >= target * 0.995:
            urgency = "NEAR TARGET — Consider partial exit"

        results.append({
            "symbol": sym,
            "direction": direction,
            "entry_price": entry,
            "quantity": qty,
            "ltp": ltp,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "stop_loss": stop,
            "recommended_stop": recommended_stop,
            "target": target,
            "urgency": urgency,
            "minutes_held": minutes_held,
            "notes": pos.get("notes", ""),
        })
    return results


# ── Edge Decay Monitoring ──────────────────────────────────────────────────

def compute_edge_decay(tickers_cfg, lookback_days=14):
    """Compare recent per-symbol win rate from journal against config expectation.

    Returns dict of {symbol: {recent_wr, expected_wr, n_recent, decaying}} for symbols
    where recent performance significantly diverges from historical.
    """
    try:
        from common.db import _select
        from datetime import timedelta
    except ImportError:
        return {}

    cutoff = (datetime.now(IST) - timedelta(days=lookback_days)).isoformat()
    rows = _select(
        "trades", "*",
        where="status = %s AND scanner_type = %s AND exit_time >= %s",
        params=["closed", "scalp", cutoff],
        order="exit_time",
    )
    if not rows:
        return {}

    # Group trades by symbol
    by_sym = {}
    for r in rows:
        sym = r.get("symbol")
        if sym:
            by_sym.setdefault(sym, []).append(r)

    decay_signals = {}
    for tc in tickers_cfg:
        sym = tc["symbol"]
        trades = by_sym.get(sym, [])
        if len(trades) < 5:  # need minimum sample
            continue

        wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        recent_wr = wins / len(trades) * 100

        # Expected win rate from config notes (extract from best combo)
        # Use 55% as default expected win rate if not parseable
        expected_wr = 55.0
        notes = tc.get("notes", "")
        import re
        match = re.search(r"(\d+)% hit", notes)
        if match:
            expected_wr = float(match.group(1))

        # Flag as decaying if recent WR is >15pp below expected
        decaying = recent_wr < expected_wr - 15

        decay_signals[sym] = {
            "recent_wr": round(recent_wr, 1),
            "expected_wr": round(expected_wr, 1),
            "n_recent": len(trades),
            "decaying": decaying,
        }

    return decay_signals


# ── Cached Report Data ──────────────────────────────────────────────────────

def load_cached_stats(symbol, output_dir):
    """Load historical stats from tickers_5min_report output."""
    ticker_dir = Path(output_dir) / symbol
    stats = {}

    # Time window stats
    tw_path = ticker_dir / "time_window_stats.csv"
    if tw_path.exists():
        tw = pd.read_csv(tw_path)
        stats["time_windows"] = tw.to_dict("records")

    # Probability matrix summary
    pm_path = ticker_dir / "probability_matrix.csv"
    if pm_path.exists():
        pm = pd.read_csv(pm_path)
        # Summarize: for each gap type, best target/stop combo
        decided = pm[pm["result"].isin(["target", "stop"])]
        summaries = []
        for gt in decided["gap_type"].unique():
            sub = decided[decided["gap_type"] == gt]
            for tgt in sub["target_pct"].unique():
                for stp in sub["stop_pct"].unique():
                    combo = sub[(sub["target_pct"] == tgt) & (sub["stop_pct"] == stp)]
                    if len(combo) > 0:
                        hit_rate = (combo["result"] == "target").mean() * 100
                        summaries.append({
                            "gap_type": gt, "target": tgt, "stop": stp,
                            "hit_rate": round(hit_rate, 1), "n": len(combo),
                        })
        stats["probability_summary"] = summaries

    return stats


# ── Next-Session Prep ───────────────────────────────────────────────────────

def compute_session_prep(ticker_cfg, daily_df, intra_ist, output_dir):
    """Compute next-session preparation data for a ticker."""
    symbol = ticker_cfg["symbol"]
    prep = {
        "symbol": symbol,
        "name": ticker_cfg["name"],
        "enabled": ticker_cfg.get("enabled", True),
        "edge_strength": ticker_cfg.get("edge_strength", 3),
        "last_close": np.nan,
        "prev_close": np.nan,
        "atr_val": np.nan,
        "atr_pct": np.nan,
        "gap_scenarios": [],
        "best_phase": None,
        "best_phase_wr": np.nan,
        "key_levels": {},
        "historical_gap_dist": {},
        "today_action": "",
        "volume_trend": "",
    }

    if not ticker_cfg.get("enabled", True):
        return prep

    if daily_df.empty:
        return prep

    last_close = daily_df["Close"].iloc[-1]
    prev_close = daily_df["Close"].iloc[-2] if len(daily_df) >= 2 else np.nan
    prep["last_close"] = last_close
    prep["prev_close"] = prev_close

    # ATR
    atr_val = compute_atr(daily_df) if len(daily_df) >= 14 else np.nan
    atr_pct = atr_val / last_close * 100 if not np.isnan(atr_val) and last_close > 0 else np.nan
    prep["atr_val"] = atr_val
    prep["atr_pct"] = atr_pct

    # Historical gap distribution
    gdf = classify_gaps(daily_df)
    if not gdf.empty:
        total = len(gdf)
        for gt in ["flat", "small_up", "small_down", "large_up", "large_down"]:
            cnt = (gdf["gap_type"] == gt).sum()
            prep["historical_gap_dist"][gt] = round(cnt / total * 100, 1) if total > 0 else 0

    # Volume trend (last 5 days vs 20-day median)
    if len(daily_df) >= 5:
        recent_vol = daily_df["Volume"].iloc[-5:].mean()
        median_vol = daily_df["Volume"].iloc[-21:].median() if len(daily_df) >= 20 else daily_df["Volume"].median()
        if median_vol > 0 and not np.isnan(median_vol):
            vol_ratio = recent_vol / median_vol
            if vol_ratio >= 1.3:
                prep["volume_trend"] = f"Rising ({vol_ratio:.1f}x)"
            elif vol_ratio <= 0.7:
                prep["volume_trend"] = f"Declining ({vol_ratio:.1f}x)"
            else:
                prep["volume_trend"] = f"Normal ({vol_ratio:.1f}x)"

    # Key levels from today's intraday
    if not intra_ist.empty:
        today = intra_ist.index[-1].date()
        today_bars = intra_ist[intra_ist.index.date == today]
        if not today_bars.empty:
            prep["key_levels"]["today_high"] = today_bars["High"].max()
            prep["key_levels"]["today_low"] = today_bars["Low"].min()
            prep["key_levels"]["today_open"] = today_bars["Open"].iloc[0]
            if "vwap" in today_bars.columns:
                prep["key_levels"]["today_vwap"] = today_bars["vwap"].iloc[-1]

    # Best time window from cached stats
    cached = load_cached_stats(symbol, output_dir)
    if cached.get("time_windows"):
        tw = cached["time_windows"]
        best = max(tw, key=lambda x: x.get("win_rate", 0))
        prep["best_phase"] = best.get("window", "N/A")
        prep["best_phase_wr"] = best.get("win_rate", np.nan)

    # Gap scenarios: what happens if tomorrow opens with each gap type
    gap_rules = ticker_cfg.get("gap_rules", {})
    risk = ticker_cfg.get("risk", {})
    target_pct = risk.get("base_target_pct", 1.0)
    stop_pct = risk.get("base_stop_pct", 1.5)
    active_phases = ticker_cfg.get("active_phases", [])

    # Compute probable open prices for each gap type
    gap_thresholds = {
        "flat":       (0.0, "±0.3%"),
        "small_up":   (+0.006, "+0.3% to +1.0%"),
        "small_down": (-0.006, "-0.3% to -1.0%"),
        "large_up":   (+0.015, "> +1.0%"),
        "large_down": (-0.015, "> -1.0%"),
    }

    prob_summary = {}
    if cached.get("probability_summary"):
        for p in cached["probability_summary"]:
            key = (p["gap_type"], p["target"], p["stop"])
            prob_summary[key] = p

    for gap_type, (gap_mult, gap_desc) in gap_thresholds.items():
        probable_open = last_close * (1 + gap_mult)
        target_price = probable_open * (1 + target_pct / 100)
        stop_price = probable_open * (1 - stop_pct / 100)

        # Is this gap type preferred in any active phase?
        tradeable_phases = []
        for ph in active_phases:
            phase_gaps = gap_rules.get(ph, {}).get("preferred_gaps", [])
            if gap_type in phase_gaps:
                tradeable_phases.append(ph)

        # Historical hit rate for this gap type with configured target/stop
        hist_key = (gap_type, target_pct, stop_pct)
        hist = prob_summary.get(hist_key, {})
        hit_rate = hist.get("hit_rate", None)
        n_samples = hist.get("n", 0)

        # Historical frequency of this gap type
        freq = prep["historical_gap_dist"].get(gap_type, 0)

        scenario = {
            "gap_type": gap_type,
            "gap_desc": gap_desc,
            "probability": freq,
            "probable_open": probable_open,
            "target_price": target_price,
            "stop_price": stop_price,
            "tradeable": len(tradeable_phases) > 0,
            "tradeable_phases": tradeable_phases,
            "hist_hit_rate": hit_rate,
            "hist_n": n_samples,
        }
        prep["gap_scenarios"].append(scenario)

    # Sort scenarios: tradeable first, then by probability
    prep["gap_scenarios"].sort(key=lambda x: (-x["tradeable"], -x["probability"]))

    # Today's summary action
    if not intra_ist.empty:
        today = intra_ist.index[-1].date()
        today_bars = intra_ist[intra_ist.index.date == today]
        if len(today_bars) >= 2:
            day_ret = (today_bars["Close"].iloc[-1] / today_bars["Open"].iloc[0] - 1) * 100
            if day_ret > 0.5:
                prep["today_action"] = f"Bullish close (+{day_ret:.2f}%)"
            elif day_ret < -0.5:
                prep["today_action"] = f"Bearish close ({day_ret:.2f}%)"
            else:
                prep["today_action"] = f"Flat close ({day_ret:+.2f}%)"

    return prep


# ── OpenAI Advisory ─────────────────────────────────────────────────────────

AI_SYSTEM_PROMPT = """You are a professional intraday scalp trading advisor for Indian equity markets (NSE/BSE).
You receive structured data about multiple stock tickers, their current conditions, open positions, and historical backtest statistics.

Your job:
1. RANK the active opportunities by quality (edge strength, conditions met, historical hit rate)
2. For each open position: recommend specific action (hold, trail stop, partial exit, full exit)
3. UPCOMING window: For tickers eligible in the next phase, advise whether to trade now or wait for better entry in the next window. Compare current conditions vs expected next-window setup.
4. Give 1-2 risk warnings if relevant (correlated positions, approaching daily drawdown, extended moves)

Be concise. Use bullet points. No disclaimers. Assume the user is an experienced trader.
Respond in 150-250 words max."""

AI_PREP_SYSTEM_PROMPT = """You are a professional intraday scalp trading advisor preparing a next-session briefing for Indian equity markets (NSE/BSE).
You receive structured data about multiple stock tickers: today's close, ATR, key levels, gap scenario probabilities with historical hit rates, and config rules.

Respond in TWO sections:

## Quick Summary (plain language, anyone can understand)
Write 3-5 bullet points in simple everyday language:
- Which stocks look good tomorrow and why (in one sentence each)
- What to avoid and why
- One clear "best trade idea" with exact price: "If [stock] opens near [price], buy with target [price] and stop at [price]"

## Detailed Analysis (for the trading desk)
1. RANK which tickers have the best setup probability for tomorrow
2. For each ticker: the 1-2 most likely gap scenarios and whether they're tradeable
3. Exact LEVELS: entry triggers, VWAP zones, stop placement
4. "If X happens, do Y" conditional plans with specific prices
5. What NOT to trade (traps, low-edge scenarios)
6. Risk warnings (correlated betas, Nifty weakness impact)

Be specific with prices. Use markdown formatting. No disclaimers.
Respond in 500-800 words. IMPORTANT: You MUST include ALL 6 numbered sections in the Detailed Analysis — do NOT skip sections 4, 5, or 6."""


def build_ai_context(phase, now_ist, ticker_states, position_states, nifty_state, config):
    """Build structured prompt for OpenAI."""
    lines = []
    lines.append(f"Time: {now_ist.strftime('%Y-%m-%d %H:%M')} IST")
    lines.append(f"Phase: {phase} ({PHASE_LABELS.get(phase, '')})")
    regime = nifty_state.get('regime', 'unknown')
    vix = nifty_state.get('vix')
    vix_regime = nifty_state.get('vix_regime', 'unknown')
    lines.append(f"Nifty: {'Above VWAP, not making lows' if nifty_state['ok'] else 'Making new lows / Below VWAP'} | Regime: {regime}")
    if vix:
        lines.append(f"India VIX: {vix} ({vix_regime})")
    regime_strength = nifty_state.get('regime_strength', 0)
    lines.append(f"Regime strength: {regime_strength:.0%}")
    # Edge decay warnings
    edge_decay = nifty_state.get('edge_decay', {})
    decaying = {sym: d for sym, d in edge_decay.items() if d.get("decaying")}
    if decaying:
        lines.append(f"EDGE DECAY: {', '.join(s.replace('.NS','') + f' (WR {d['recent_wr']:.0f}% vs {d['expected_wr']:.0f}%)' for s, d in decaying.items())}")
    lines.append("")

    # Ticker states
    for ts in ticker_states:
        if ts["signal"] == "DISABLED":
            continue
        lines.append(f"--- {ts['symbol']} ({ts['name']}) ---")
        vwap_dist = ts.get('vwap_dist_pct', 0)
        atr_rank = ts.get('atr_rank', 50)
        lines.append(f"  LTP: {fmt(ts['ltp'])} | Chg: {fmt(ts['change_pct'])}% | Gap: {ts['gap_type']} ({fmt(ts['gap_pct'])}%)")
        lines.append(f"  VWAP: {fmt(ts.get('vwap_val'))} (dist: {vwap_dist:+.2f}%) | ATR: {fmt(ts.get('atr_pct'))}% [P{atr_rank:.0f}]")
        lines.append(f"  Signal: {ts['signal']} | Conditions: {ts['conditions_met']}/{ts['conditions_total']}")
        lines.append(f"  Action: {ts['action_text']}")
        if ts["signal"] in ("ACTIVE", "WATCH"):
            lines.append(f"  Entry: {fmt(ts['entry_price'])} Target: {fmt(ts['target_price'])} Stop: {fmt(ts['stop_price'])}")
        if ts.get("next_phase_eligible"):
            np_label = PHASE_LABELS.get(ts["next_phase"], ts["next_phase"] or "")
            gap_ok = "gap OK" if ts.get("next_phase_gap_ok") else f"gap {ts.get('gap_type','?')} not preferred"
            lines.append(f"  Next window: {np_label} [{ts.get('next_phase_window', '')}] — eligible, {gap_ok}")
        # Config notes
        for tcfg in config.get("tickers", []):
            if tcfg["symbol"] == ts["symbol"]:
                lines.append(f"  Notes: {tcfg.get('notes', '').strip()}")
                # Cached stats
                cached = load_cached_stats(ts["symbol"], config["global"].get("output_dir", "output"))
                if cached.get("probability_summary"):
                    best = sorted(cached["probability_summary"], key=lambda x: -x["hit_rate"])[:3]
                    for b in best:
                        lines.append(f"  Hist: gap={b['gap_type']} +{b['target']}%/-{b['stop']}% hit={b['hit_rate']}% (N={b['n']})")
                break
        lines.append("")

    # Positions
    if position_states:
        lines.append("--- OPEN POSITIONS ---")
        for ps in position_states:
            lines.append(f"  {ps['symbol']}: {ps['direction']} {ps['quantity']}x @ {fmt(ps['entry_price'])}")
            lines.append(f"  LTP: {fmt(ps['ltp'])} P&L: {fmt(ps['pnl'])} ({fmt(ps['pnl_pct'])}%)")
            lines.append(f"  Stop: {fmt(ps['stop_loss'])} Target: {fmt(ps['target'])} Urgency: {ps['urgency']}")
    else:
        lines.append("--- NO OPEN POSITIONS ---")

    # Global risk
    g = config.get("global", {})
    lines.append(f"\nBook: {g.get('capital', 'N/A')} | Max trades: {g.get('max_open_trades', 3)}")
    lines.append(f"Max risk/trade: {g.get('max_risk_per_trade_pct', 0.5)}% | Max DD: {g.get('max_intraday_drawdown_pct', 1.5)}%")

    return "\n".join(lines)


def build_prep_context(now_ist, prep_data, nifty_state, config):
    """Build structured prompt for next-session prep advisory."""
    lines = []
    lines.append(f"Date: {now_ist.strftime('%Y-%m-%d %H:%M')} IST (preparing for next session)")
    lines.append(f"Today's Nifty: {'Closed above VWAP' if nifty_state.get('above_vwap') else 'Closed below VWAP'}")
    lines.append("")

    for p in prep_data:
        if not p["enabled"]:
            continue
        lines.append(f"=== {p['symbol']} ({p['name']}) | Edge: {p['edge_strength']}/5 ===")
        lines.append(f"  Close: {fmt(p['last_close'])} | ATR: {fmt(p['atr_val'])} ({fmt(p['atr_pct'])}%)")
        lines.append(f"  Today: {p['today_action']} | Volume: {p['volume_trend']}")
        if p["best_phase"]:
            lines.append(f"  Best window: {p['best_phase']} ({fmt(p['best_phase_wr'])}% WR)")

        # Key levels
        kl = p.get("key_levels", {})
        if kl:
            lines.append(f"  Levels: High={fmt(kl.get('today_high'))} Low={fmt(kl.get('today_low'))} VWAP={fmt(kl.get('today_vwap'))}")

        # Gap scenarios
        lines.append("  Tomorrow's scenarios:")
        for sc in p["gap_scenarios"]:
            trade_str = "TRADEABLE" if sc["tradeable"] else "skip"
            phases_str = ",".join(sc["tradeable_phases"]) if sc["tradeable_phases"] else "-"
            hit_str = f"{sc['hist_hit_rate']}% (N={sc['hist_n']})" if sc["hist_hit_rate"] is not None else "no data"
            lines.append(f"    {sc['gap_type']:12s} | prob {sc['probability']:4.1f}% | {trade_str} [{phases_str}]")
            lines.append(f"    {'':12s} | open ~{fmt(sc['probable_open'])} tgt {fmt(sc['target_price'])} stp {fmt(sc['stop_price'])} | hist: {hit_str}")

        # Config notes
        for tcfg in config.get("tickers", []):
            if tcfg["symbol"] == p["symbol"]:
                lines.append(f"  Strategy: {tcfg.get('notes', '').strip()[:200]}")
                break
        lines.append("")

    g = config.get("global", {})
    lines.append(f"Book: {g.get('capital', 'N/A')} | Max trades: {g.get('max_open_trades', 3)}")
    return "\n".join(lines)


def get_ai_advisory(context, config, prep_mode=False):
    """Call LLM for trading advisory via common.llm."""
    from common.llm import call_llm

    sys_prompt = AI_PREP_SYSTEM_PROMPT if prep_mode else AI_SYSTEM_PROMPT
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": context},
    ]
    return call_llm(messages)


# ── Dashboard Rendering ─────────────────────────────────────────────────────

def write_markdown_report(now_ist, phase, prep_data, ticker_states, position_states, nifty_state, ai_text, config, prep_mode,
                          portfolio_metrics=None):
    """Write a markdown report file for easy review."""
    SCALP_REPORT_DIR.mkdir(exist_ok=True)
    report_path = SCALP_REPORT_DIR / f"scalp_report_{now_ist.strftime('%Y-%m-%d_%H%M')}.md"

    lines = []
    date_str = now_ist.strftime('%Y-%m-%d %H:%M')
    nifty_str = "Above VWAP" if nifty_state.get("above_vwap") else "Below VWAP"

    if prep_mode:
        lines.append(f"# Next Session Prep — {date_str} IST")
        lines.append(f"\n**Nifty**: {nifty_str}\n")

        for p in sorted(prep_data, key=lambda x: -x["edge_strength"]):
            if not p["enabled"]:
                continue
            sym = p["symbol"].replace(".NS", "")
            lines.append(f"## {sym} — {p['name']}")
            lines.append(f"\n| Metric | Value |")
            lines.append(f"|--------|-------|")
            lines.append(f"| Close | {fmt(p['last_close'])} |")
            lines.append(f"| ATR(14) | {fmt(p['atr_val'])} ({fmt(p['atr_pct'])}%) |")
            lines.append(f"| Today | {p['today_action']} |")
            lines.append(f"| Volume | {p['volume_trend']} |")
            lines.append(f"| Edge Strength | {p['edge_strength']}/5 |")
            lines.append(f"| Best Window | {p['best_phase']} ({fmt(p['best_phase_wr'])}% WR) |")

            kl = p.get("key_levels", {})
            if kl:
                lines.append(f"| Today High | {fmt(kl.get('today_high'))} |")
                lines.append(f"| Today Low | {fmt(kl.get('today_low'))} |")
                lines.append(f"| Today VWAP | {fmt(kl.get('today_vwap'))} |")

            lines.append(f"\n### Gap Scenarios\n")
            lines.append("| Gap Type | Probability | Tradeable | Phases | Hit Rate | N | Entry | Target | Stop |")
            lines.append("|----------|-------------|-----------|--------|----------|---|-------|--------|------|")
            for sc in p["gap_scenarios"]:
                trade = "**YES**" if sc["tradeable"] else "no"
                phases = ", ".join(sc["tradeable_phases"]) if sc["tradeable_phases"] else "-"
                hit = f"{sc['hist_hit_rate']:.1f}%" if sc["hist_hit_rate"] is not None else "N/A"
                n = str(sc["hist_n"]) if sc["hist_n"] > 0 else "-"
                lines.append(
                    f"| {sc['gap_type']} | {sc['probability']:.1f}% | {trade} | {phases} | "
                    f"{hit} | {n} | {fmt(sc['probable_open'])} | {fmt(sc['target_price'])} | {fmt(sc['stop_price'])} |"
                )

            # Config notes
            for tcfg in config.get("tickers", []):
                if tcfg["symbol"] == p["symbol"]:
                    notes = tcfg.get("notes", "").strip()
                    if notes:
                        lines.append(f"\n> **Strategy**: {notes}\n")
                    break
    else:
        lines.append(f"# Scalp Scanner — {date_str} IST")
        lines.append(f"\n**Phase**: {PHASE_LABELS.get(phase, phase)}  |  **Nifty**: {nifty_str}\n")

        active = [s for s in ticker_states if s["signal"] == "ACTIVE"]
        watching = [s for s in ticker_states if s["signal"] == "WATCH"]
        others = [s for s in ticker_states if s["signal"] not in ("ACTIVE", "WATCH", "DISABLED")]

        # VIX info
        vix_val = nifty_state.get("vix")
        vix_regime = nifty_state.get("vix_regime", "unknown")
        if vix_val:
            lines.append(f"**VIX**: {vix_val} ({vix_regime.upper()})\n")

        if active:
            lines.append("## Active Signals\n")
            for s in sorted(active, key=lambda x: -x["edge_strength"]):
                chg = f"{s['change_pct']:+.2f}%" if not np.isnan(s["change_pct"]) else "N/A"
                vol_tag = s.get("vol_tag", "N/A")
                rs_tag = "Outperforming" if s.get("rs_positive") else "Underperforming"
                rr = s.get("rr_ratio", 0)
                lines.append(f"### {s['symbol']} — {s['action_text']}")
                lines.append(f"- **LTP**: {fmt(s['ltp'])} ({chg}) | **Gap**: {s['gap_type'].upper()} | **Edge**: {s['edge_strength']}/5")
                lines.append(f"- **Conditions**: {s['conditions_met']}/{s['conditions_total']} | **Volume**: {vol_tag} | **RS vs Nifty**: {rs_tag}")
                lines.append(f"- **Entry**: {fmt(s['entry_price'])} | **Target**: {fmt(s['target_price'])} (+{s['target_pct']}%) | **Stop**: {fmt(s['stop_price'])} (-{s['stop_pct']}%) | **RR**: {rr}")
                qty = s.get("recommended_qty", 0)
                risk = s.get("capital_at_risk", 0)
                risk_pct = s.get("risk_pct", 0)
                if qty > 0:
                    lines.append(f"- **Qty**: {qty} | **Risk**: {risk:,.0f} ({risk_pct:.2f}%)")
                lines.append("")

        upcoming = [s for s in watching if s.get("next_phase_eligible")]
        other_watch = [s for s in watching if not s.get("next_phase_eligible")]

        if upcoming:
            next_window = upcoming[0].get("next_phase_window", "")
            lines.append(f"## Upcoming ({next_window})\n")
            for s in sorted(upcoming, key=lambda x: -x["edge_strength"]):
                gap_ok = "gap OK" if s.get("next_phase_gap_ok") else f"gap {s.get('gap_type','?')} not preferred"
                lines.append(f"- **{s['symbol']}** {fmt(s['ltp'])} — Edge {s['edge_strength']}/5 | {gap_ok} | Entry ~{fmt(s['entry_price'])} Tgt {fmt(s['target_price'])} Stop {fmt(s['stop_price'])}")
            lines.append("")

        if other_watch:
            lines.append("## Watching\n")
            for s in other_watch:
                lines.append(f"- **{s['symbol']}** {fmt(s['ltp'])} — {s['action_text']}")
            lines.append("")

        if others:
            lines.append("## Not Active\n")
            for s in others:
                lines.append(f"- **{s['symbol']}** — {s['action_text']}")
            lines.append("")

        if position_states:
            lines.append("## Open Positions\n")
            lines.append("| Symbol | Dir | Qty | Entry | LTP | P&L | P&L% | Action |")
            lines.append("|--------|-----|-----|-------|-----|-----|------|--------|")
            for ps in position_states:
                pnl = f"{ps['pnl']:+.0f}" if not np.isnan(ps["pnl"]) else "N/A"
                pnl_pct = f"{ps['pnl_pct']:+.2f}%" if not np.isnan(ps["pnl_pct"]) else ""
                lines.append(f"| {ps['symbol']} | {ps['direction']} | {ps['quantity']} | {fmt(ps['entry_price'])} | {fmt(ps['ltp'])} | {pnl} | {pnl_pct} | {ps['urgency']} |")
            lines.append("")

    # Portfolio metrics
    if portfolio_metrics and portfolio_metrics.get("n_trades", 0) > 0:
        pm = portfolio_metrics
        lines.append("## Portfolio Metrics (30d)\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Trades | {pm['n_trades']} |")
        lines.append(f"| Win Rate | {pm['win_rate']}% |")
        lines.append(f"| Gross P&L | {pm['gross_pnl']:+,.2f} |")
        sharpe = f"{pm['sharpe']:.2f}" if pm['sharpe'] is not None else "N/A"
        sortino = f"{pm['sortino']:.2f}" if pm['sortino'] is not None else "N/A"
        lines.append(f"| Sharpe | {sharpe} |")
        lines.append(f"| Sortino | {sortino} |")
        lines.append(f"| Max Drawdown | {pm['max_drawdown_pct']}% |")
        lines.append(f"| Current Streak | {pm['current_streak']:+d} |")
        lines.append("")

    # AI advisory
    if ai_text:
        lines.append("---\n")
        lines.append("## AI Advisory\n")
        lines.append(ai_text)
        lines.append("")

    report_content = "\n".join(lines) + "\n"
    with open(report_path, "w") as f:
        f.write(report_content)
    return report_path, report_content


def _render_ai_section(lines, ai_text):
    """Render the AI advisory section into lines."""
    lines.append(box_mid())
    if ai_text:
        lines.append(box_line("AI ADVISORY"))
        lines.append(box_line())
        for al in ai_text.split("\n"):
            while len(al) > W - 4:
                lines.append(box_line(al[:W - 4]))
                al = al[W - 4:]
            lines.append(box_line(al))
        lines.append(box_line())
    else:
        lines.append(box_line("AI ADVISORY: Skipped (no LLM configured)"))
        lines.append(box_line())


def render_prep_dashboard(now_ist, phase, prep_data, nifty_state, ai_text):
    """Render next-session preparation dashboard."""
    lines = []
    lines.append(box_top())
    nifty_indicator = "Closed Above VWAP" if nifty_state.get("above_vwap") else "Closed Below VWAP"
    lines.append(box_line(f"NEXT SESSION PREP - {now_ist.strftime('%Y-%m-%d %H:%M')} IST"))
    lines.append(box_line(f"Nifty: {nifty_indicator}"))
    lines.append(box_mid())

    for p in sorted(prep_data, key=lambda x: -x["edge_strength"]):
        if not p["enabled"]:
            continue

        sym_short = p["symbol"].replace(".NS", "")
        lines.append(box_line(f"{sym_short}  Close: {fmt(p['last_close'])}  ATR: {fmt(p['atr_val'])} ({fmt(p['atr_pct'])}%)"))
        lines.append(box_line(f"  {p['today_action']}  |  Vol: {p['volume_trend']}  |  Edge: {p['edge_strength']}/5"))

        # Key levels
        kl = p.get("key_levels", {})
        if kl:
            parts = []
            if "today_high" in kl:
                parts.append(f"H:{fmt(kl['today_high'])}")
            if "today_low" in kl:
                parts.append(f"L:{fmt(kl['today_low'])}")
            if "today_vwap" in kl:
                parts.append(f"VWAP:{fmt(kl['today_vwap'])}")
            lines.append(box_line(f"  Levels: {' | '.join(parts)}"))

        # Best window
        if p["best_phase"]:
            lines.append(box_line(f"  Best window: {p['best_phase']} ({fmt(p['best_phase_wr'])}% WR)"))

        # Gap scenarios
        lines.append(box_line(f"  {'Gap':12s} {'Prob':>5s}  {'Trade?':8s}  {'Hit%':>5s}  {'N':>3s}  Levels"))
        lines.append(box_line(f"  {'----':12s} {'----':>5s}  {'------':8s}  {'----':>5s}  {'--':>3s}  ------"))
        for sc in p["gap_scenarios"]:
            trade_marker = ">>YES" if sc["tradeable"] else "  no"
            hit_str = f"{sc['hist_hit_rate']:5.1f}" if sc["hist_hit_rate"] is not None else "  N/A"
            n_str = f"{sc['hist_n']:3d}" if sc["hist_n"] > 0 else "  -"
            lvl = f"E:{fmt(sc['probable_open'],1)} T:{fmt(sc['target_price'],1)} S:{fmt(sc['stop_price'],1)}"
            lines.append(box_line(
                f"  {sc['gap_type']:12s} {sc['probability']:4.1f}%  {trade_marker:8s}  {hit_str}  {n_str}  {lvl}"
            ))

        lines.append(box_line())

    # Disabled tickers
    disabled = [p for p in prep_data if not p["enabled"]]
    if disabled:
        lines.append(box_line("MONITORING (disabled)"))
        for p in disabled:
            lines.append(box_line(f"  {p['symbol']} ({p['name']})"))
        lines.append(box_line())

    _render_ai_section(lines, ai_text)
    lines.append(box_bot())
    return "\n".join(lines)


def render_dashboard(now_ist, phase, ticker_states, position_states, nifty_state, ai_text,
                     portfolio_metrics=None):
    """Render live market dashboard."""
    lines = []
    lines.append(box_top())
    nifty_indicator = "Above VWAP" if nifty_state.get("above_vwap") else "Below VWAP"
    regime = nifty_state.get("regime", "unknown").upper()
    regime_strength = nifty_state.get("regime_strength", 0)
    vix_val = nifty_state.get("vix")
    vix_regime = nifty_state.get("vix_regime", "unknown")
    vix_str = f"VIX: {vix_val} ({vix_regime.upper()})" if vix_val else "VIX: N/A"
    lines.append(box_line(f"SCALP SCANNER - {now_ist.strftime('%Y-%m-%d %H:%M')} IST"))
    lines.append(box_line(f"Phase: {PHASE_LABELS.get(phase, phase)}   Nifty: {nifty_indicator} [{regime} {regime_strength:.0%}]"))
    lines.append(box_line(f"{vix_str}"))
    lines.append(box_mid())

    # Group by signal type
    active = [s for s in ticker_states if s["signal"] == "ACTIVE"]
    watching = [s for s in ticker_states if s["signal"] == "WATCH"]
    no_trade = [s for s in ticker_states if s["signal"] in ("NO_TRADE", "STAND_ASIDE", "AVOID", "EXIT")]
    disabled = [s for s in ticker_states if s["signal"] == "DISABLED"]

    # Active signals
    if active:
        lines.append(box_line("ACTIVE SIGNALS"))
        lines.append(box_line())
        for s in sorted(active, key=lambda x: -x["edge_strength"]):
            chg = f"{s['change_pct']:+.2f}%" if not np.isnan(s["change_pct"]) else "N/A"
            vol_info = f"Vol: {s.get('vol_tag', 'N/A')}"
            if not np.isnan(s.get("vol_ratio", np.nan)):
                vol_info += f" ({s['vol_ratio']:.1f}x)"
            rs_info = "RS+" if s.get("rs_positive") else "RS-"
            lines.append(box_line(f"  {s['symbol']}  {fmt(s['ltp'])} ({chg})  Gap: {s['gap_type'].upper()}  Edge: {s['edge_strength']}/5"))
            lines.append(box_line(f"  Conditions: {s['conditions_met']}/{s['conditions_total']}  |  {vol_info}  |  {rs_info}"))
            rr = s.get("rr_ratio", 0)
            lines.append(box_line(f"  Entry {fmt(s['entry_price'])} -> Tgt {fmt(s['target_price'])} (+{s['target_pct']}%) / Stop {fmt(s['stop_price'])} (-{s['stop_pct']}%)  RR: {rr}"))
            qty = s.get("recommended_qty", 0)
            risk = s.get("capital_at_risk", 0)
            risk_pct = s.get("risk_pct", 0)
            if qty > 0:
                lines.append(box_line(f"  Qty: {qty}  |  Risk: {risk:,.0f} ({risk_pct:.2f}%)"))
            lines.append(box_line(f"  {s['action_text']}"))
            lines.append(box_line())
    else:
        lines.append(box_line("ACTIVE SIGNALS: None"))
        lines.append(box_line())

    # Watching — split into "upcoming next window" and "other watching"
    upcoming = [s for s in watching if s.get("next_phase_eligible")]
    other_watch = [s for s in watching if not s.get("next_phase_eligible")]

    if upcoming:
        next_label = upcoming[0].get("next_phase_window", "")
        header = f"UPCOMING ({next_label})" if next_label else "UPCOMING"
        lines.append(box_line(header))
        for s in sorted(upcoming, key=lambda x: -x["edge_strength"]):
            chg = f"{s['change_pct']:+.2f}%" if not np.isnan(s["change_pct"]) else ""
            gap_note = f"  Gap: {s['gap_type'].upper()}" if s.get("gap_type", "unknown") != "unknown" else ""
            gap_ok = "OK" if s.get("next_phase_gap_ok") else "not preferred"
            lines.append(box_line(f"  {s['symbol']} {fmt(s['ltp'])} {chg}{gap_note} [{gap_ok}]  Edge: {s['edge_strength']}/5"))
            lines.append(box_line(f"    Entry ~{fmt(s['entry_price'])} / Tgt {fmt(s['target_price'])} / Stop {fmt(s['stop_price'])}  RR: {s.get('rr_ratio', 0)}"))
        lines.append(box_line())

    if other_watch:
        lines.append(box_line("WATCHING"))
        for s in other_watch:
            chg = f"{s['change_pct']:+.2f}%" if not np.isnan(s["change_pct"]) else ""
            vol_tag = s.get("vol_tag", "")
            rs_tag = "RS+" if s.get("rs_positive") else ""
            extras = f"  [{vol_tag}]" if vol_tag else ""
            extras += f" [{rs_tag}]" if rs_tag else ""
            lines.append(box_line(f"  {s['symbol']} {fmt(s['ltp'])} {chg}{extras} - {s['action_text']}"))
        lines.append(box_line())

    # Not active
    if no_trade:
        lines.append(box_line("NOT ACTIVE"))
        for s in no_trade:
            lines.append(box_line(f"  {s['symbol']} - {s['action_text']}"))
        lines.append(box_line())

    # Disabled
    if disabled:
        lines.append(box_line("MONITORING (disabled)"))
        for s in disabled:
            lines.append(box_line(f"  {s['symbol']} ({s['name']})"))
        lines.append(box_line())

    # Positions
    lines.append(box_mid())
    if position_states:
        lines.append(box_line("OPEN POSITIONS"))
        for ps in position_states:
            pnl_str = f"{ps['pnl']:+.0f}" if not np.isnan(ps["pnl"]) else "N/A"
            pnl_pct_str = f"{ps['pnl_pct']:+.2f}%" if not np.isnan(ps["pnl_pct"]) else ""
            lines.append(box_line(f"  {ps['symbol']} {ps['direction'].upper()} {ps['quantity']}x @ {fmt(ps['entry_price'])}"))
            lines.append(box_line(f"  LTP: {fmt(ps['ltp'])}  P&L: {pnl_str} ({pnl_pct_str})"))
            lines.append(box_line(f"  >> {ps['urgency']}"))
            lines.append(box_line())
    else:
        lines.append(box_line("OPEN POSITIONS: None"))
        lines.append(box_line())

    # Portfolio metrics section
    if portfolio_metrics and portfolio_metrics.get("n_trades", 0) > 0:
        lines.append(box_mid())
        pm = portfolio_metrics
        lines.append(box_line("PORTFOLIO METRICS (30d)"))
        lines.append(box_line(f"  Trades: {pm['n_trades']}  |  Win Rate: {pm['win_rate']}%  |  P&L: {pm['gross_pnl']:+,.0f}"))
        sharpe = f"{pm['sharpe']:.2f}" if pm['sharpe'] is not None else "N/A"
        sortino = f"{pm['sortino']:.2f}" if pm['sortino'] is not None else "N/A"
        lines.append(box_line(f"  Sharpe: {sharpe}  |  Sortino: {sortino}  |  Max DD: {pm['max_drawdown_pct']}%"))
        lines.append(box_line(f"  Streak: {pm['current_streak']:+d}  |  Best: {pm['max_win_streak']}W  |  Worst: {pm['max_loss_streak']}L"))
        lines.append(box_line())

    _render_ai_section(lines, ai_text)
    lines.append(box_bot())
    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    # Load config
    if not CONFIG_PATH.exists():
        print(f"  [ERROR] Config not found: {CONFIG_PATH}")
        return

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    g = config.get("global", {})
    phases_cfg = g.get("phases", {})
    data_cfg = g.get("data_periods", {})
    benchmark = g.get("benchmark", "^NSEI")
    tickers_cfg = config.get("tickers", [])
    positions_cfg = config.get("positions") or []
    ranking_cfg = config.get("ranking", {})

    capital = g.get("capital", 1000000)

    now_ist = datetime.now(IST)
    t = now_ist.time()
    phase = get_phase(t, phases_cfg)

    print(f"\n  Scalp Scanner - {now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"  Phase: {PHASE_LABELS.get(phase, phase)}")
    print(f"  Tickers: {sum(1 for tc in tickers_cfg if tc.get('enabled', True))}/{len(tickers_cfg)} enabled")
    print(f"  Capital: {capital:,.0f}\n")

    # Weekend check
    if now_ist.weekday() >= 5:
        print("  Market closed (weekend). Run on a weekday.")
        return

    # Feature 3: Fetch India VIX
    print("  Fetching India VIX...")
    vix_val, vix_regime = fetch_india_vix()
    vix_scale = vix_position_scale(vix_val)
    if vix_val:
        print(f"  VIX: {vix_val} ({vix_regime}) | Position scale: {vix_scale:.3f}x")
    else:
        # Conservative default when VIX unavailable — don't trade full size blind
        vix_scale = 0.7
        print(f"  VIX: unavailable — using conservative {vix_scale}x position scale")

    # Fetch benchmark
    print("  Fetching benchmark data...")
    nifty_intra = fetch_yf(benchmark, period=data_cfg.get("intraday", "5d"),
                           interval=data_cfg.get("intraday_interval", "5m"))
    nifty_ist = compute_vwap(_to_ist(nifty_intra)) if not nifty_intra.empty else pd.DataFrame()

    nifty_new_lows = nifty_making_new_lows(nifty_ist) if not nifty_ist.empty else True
    nifty_ok = not nifty_new_lows

    nifty_above_vwap = False
    if not nifty_ist.empty and "vwap" in nifty_ist.columns:
        nifty_today = nifty_ist[nifty_ist.index.date == now_ist.date()]
        if not nifty_today.empty:
            nifty_above_vwap = bool(nifty_today["Close"].iloc[-1] > nifty_today["vwap"].iloc[-1])

    nifty_state = {"ok": nifty_ok, "above_vwap": nifty_above_vwap, "new_lows": nifty_new_lows,
                   "vix": vix_val, "vix_regime": vix_regime}

    # Fetch all ticker data (parallel)
    symbols = list({tc["symbol"] for tc in tickers_cfg})
    print(f"  Fetching {len(symbols)} tickers in parallel...")
    all_data = fetch_bulk(symbols, {
        "intra": (data_cfg.get("intraday", "5d"), data_cfg.get("intraday_interval", "5m")),
        "daily": (data_cfg.get("daily", "1mo"), data_cfg.get("daily_interval", "1d")),
    }, max_workers=10, label="Scalp")

    # Compute next phase for lookahead
    next_phase_info = get_next_phase(phase, phases_cfg)

    # Evaluate each ticker
    ticker_states = []
    for tc in tickers_cfg:
        sym = tc["symbol"]
        d = all_data.get(sym, {"intra": pd.DataFrame(), "daily": pd.DataFrame()})
        state = evaluate_ticker(tc, d["intra"], d["daily"], nifty_ist, nifty_ok, phase, now_ist,
                                next_phase_info=next_phase_info)
        ticker_states.append(state)

    # Rank active signals by edge_strength then weighted_score
    for s in ticker_states:
        ratio = s["conditions_met"] / s["conditions_total"] if s["conditions_total"] > 0 else 0
        s["score"] = s["edge_strength"] * 10 + s.get("weighted_score", ratio) * 5

    # Evaluate positions (with time-exit and trailing stop logic)
    position_states = evaluate_positions(positions_cfg, ticker_states, phase, now_ist)

    # Re-entry cooldown: block ACTIVE signals on recently exited symbols
    recently_exited = set()
    for pos in positions_cfg:
        if pos.get("status") == "closed" and pos.get("exit_time"):
            try:
                exit_time = datetime.strptime(pos["exit_time"], "%Y-%m-%d %H:%M")
                exit_time = exit_time.replace(tzinfo=IST)
                minutes_since = (now_ist - exit_time).total_seconds() / 60
                if 0 <= minutes_since <= REENTRY_COOLDOWN_MINUTES:
                    recently_exited.add(pos["symbol"])
            except (ValueError, TypeError):
                pass
    if recently_exited:
        for s in ticker_states:
            if s["symbol"] in recently_exited and s["signal"] == "ACTIVE":
                s["signal"] = "WATCH"
                s["action_text"] = f"Cooldown — re-entry blocked {REENTRY_COOLDOWN_MINUTES} min after exit"

    # ── Portfolio-level risk management ──
    # Detect Nifty regime
    nifty_daily = fetch_yf(benchmark, period="2mo", interval="1d")
    nifty_regime, beta_scale, regime_strength = detect_nifty_regime(nifty_daily)
    nifty_state["regime"] = nifty_regime
    nifty_state["beta_scale"] = beta_scale
    nifty_state["regime_strength"] = regime_strength

    # Regime-conditioned target scaling: tighten targets in adverse regimes
    regime_target_scale = {"bullish": 1.0, "range": 0.90, "bearish": 0.80}.get(nifty_regime, 0.90)
    if regime_target_scale < 1.0:
        for s in ticker_states:
            if s.get("target_pct") and s.get("entry_price") and not np.isnan(s["entry_price"]):
                s["target_pct"] = round(s["target_pct"] * regime_target_scale, 2)
                s["target_price"] = s["entry_price"] * (1 + s["target_pct"] / 100)
                s["rr_ratio"] = round(s["target_pct"] / s["stop_pct"], 2) if s.get("stop_pct", 0) > 0 else 0

    # Feature 3: VIX stress — force STAND_ASIDE if stress
    if vix_regime == "stress":
        for s in ticker_states:
            if s["signal"] == "ACTIVE":
                s["signal"] = "STAND_ASIDE"
                s["action_text"] = f"VIX STRESS ({vix_val}) — all entries suspended"

    # Daily drawdown check — open positions + today's realized P&L
    open_pnl_pct = sum(ps.get("pnl_pct", 0) for ps in position_states) if position_states else 0
    realized_pnl = 0
    try:
        from common.db import get_today_realized_pnl
        realized_pnl = get_today_realized_pnl(scanner_type="scalp") or 0
    except Exception:
        pass
    total_pnl_pct = open_pnl_pct + (realized_pnl / capital * 100 if capital > 0 else 0)
    daily_dd_breached = total_pnl_pct < -MAX_DAILY_DRAWDOWN_PCT

    # Sector concentration check
    active_signals = [s for s in ticker_states if s["signal"] == "ACTIVE"]
    open_sectors = {}
    for ps in position_states:
        for tc in tickers_cfg:
            if tc["symbol"] == ps["symbol"]:
                for tag in tc.get("regime_tags", []):
                    open_sectors[tag] = open_sectors.get(tag, 0) + 1

    # Feature 9: Correlation clusters
    print("  Computing correlation clusters...")
    daily_data_dict = {sym: all_data[sym]["daily"] for sym in symbols if not all_data[sym]["daily"].empty}
    corr_clusters = compute_correlation_clusters(daily_data_dict)

    # Build reverse lookup: symbol -> cluster_id
    sym_to_cluster = {}
    for cid, syms in corr_clusters.items():
        for sym in syms:
            sym_to_cluster[sym] = cid

    # Count open positions per cluster
    open_cluster_counts = {}
    for ps in position_states:
        cid = sym_to_cluster.get(ps["symbol"])
        if cid is not None:
            open_cluster_counts[cid] = open_cluster_counts.get(cid, 0) + 1

    # Feature 10: Earnings proximity check
    print("  Checking earnings calendar...")
    earnings_warnings = {}
    for tc in tickers_cfg:
        if tc.get("enabled", True):
            near, edate = check_earnings_proximity(tc["symbol"])
            if near:
                earnings_warnings[tc["symbol"]] = edate

    # Edge decay monitoring — compare recent performance vs config expectation
    print("  Checking edge decay...")
    edge_decay = {}
    try:
        edge_decay = compute_edge_decay(tickers_cfg)
        decaying_syms = {sym for sym, d in edge_decay.items() if d["decaying"]}
        if decaying_syms:
            short_names = [s.replace(".NS", "") for s in decaying_syms]
            print(f"  EDGE DECAY WARNING: {', '.join(short_names)}")
    except Exception:
        pass
    nifty_state["edge_decay"] = edge_decay

    # Apply portfolio risk filters to active signals
    for s in active_signals:
        # Daily drawdown hard stop
        if daily_dd_breached:
            s["signal"] = "STAND_ASIDE"
            s["action_text"] = f"DAILY DD LIMIT — Total P&L {total_pnl_pct:+.2f}% exceeds -{MAX_DAILY_DRAWDOWN_PCT}%"
            continue

        # Nifty regime filter: disable high-beta in bearish regime
        if nifty_regime == "bearish":
            for tc in tickers_cfg:
                if tc["symbol"] == s["symbol"] and "high_beta" in tc.get("regime_tags", []):
                    s["signal"] = "STAND_ASIDE"
                    s["action_text"] = f"Nifty BEARISH — high-beta disabled (regime: {nifty_regime})"
                    break

        # Sector concentration limit
        for tc in tickers_cfg:
            if tc["symbol"] == s["symbol"]:
                for tag in tc.get("regime_tags", []):
                    if open_sectors.get(tag, 0) >= MAX_SECTOR_EXPOSURE:
                        s["signal"] = "WATCH"
                        s["action_text"] = f"Sector limit — {tag} already has {open_sectors[tag]} positions"
                        break
                break

        # Feature 10: Earnings warning
        if s["symbol"] in earnings_warnings:
            s["signal"] = "WATCH"
            s["action_text"] = f"EARNINGS WARNING — results on {earnings_warnings[s['symbol']]}"

        # Feature 7: DOW avoid check
        for tc in tickers_cfg:
            if tc["symbol"] == s["symbol"]:
                avoid_days = tc.get("avoid_days", [])
                if now_ist.weekday() in avoid_days:
                    s["signal"] = "WATCH"
                    s["action_text"] = f"DOW AVOID — poor win rate on {now_ist.strftime('%A')}s"
                break

        # Edge decay warning — downgrade if recent win rate significantly below expected
        decay_info = edge_decay.get(s["symbol"])
        if decay_info and decay_info["decaying"]:
            s["signal"] = "WATCH"
            s["action_text"] = (
                f"EDGE DECAY — recent WR {decay_info['recent_wr']:.0f}% vs "
                f"expected {decay_info['expected_wr']:.0f}% (N={decay_info['n_recent']})"
            )

        # Feature 9: Correlation cluster limit
        cid = sym_to_cluster.get(s["symbol"])
        if cid is not None and open_cluster_counts.get(cid, 0) >= MAX_SECTOR_EXPOSURE:
            s["signal"] = "WATCH"
            cluster_syms = corr_clusters.get(cid, [])
            s["action_text"] = f"Correlation limit — cluster ({', '.join(s.replace('.NS','') for s in cluster_syms[:3])}) at max"

    # Feature 2: Compute position sizing for remaining ACTIVE signals
    active_signals = [s for s in ticker_states if s["signal"] == "ACTIVE"]
    for s in active_signals:
        for tc in tickers_cfg:
            if tc["symbol"] == s["symbol"]:
                kelly = tc.get("risk", {}).get("kelly_fraction", 0.1)
                pos_size = compute_position_size(
                    capital=capital,
                    kelly_fraction=kelly,
                    entry_price=s["entry_price"],
                    stop_pct=s["stop_pct"],
                    vix_scale=vix_scale,
                    beta_scale=beta_scale,
                )
                s["recommended_qty"] = pos_size["quantity"]
                s["capital_allocated"] = pos_size["capital_allocated"]
                s["capital_at_risk"] = pos_size["capital_at_risk"]
                s["risk_pct"] = pos_size["risk_pct"]
                s["effective_kelly"] = pos_size["effective_kelly"]
                break

    # Feature 5: Auto-log ACTIVE signals to Supabase
    try:
        import json as _json
        from common.db import _insert
        for s in active_signals:
            row = {
                "symbol": s["symbol"],
                "direction": s.get("direction", "long"),
                "phase": phase,
                "strategy": "scalp_gap",
                "edge_strength": s["edge_strength"],
                "vix_at_signal": vix_val,
                "nifty_regime": nifty_regime,
                "conditions_met": s["conditions_met"],
                "conditions_total": s["conditions_total"],
                "weighted_score": s.get("weighted_score", 0),
                "entry_price": s["entry_price"],
                "target_price": s["target_price"],
                "stop_price": s["stop_price"],
                "recommended_qty": s.get("recommended_qty", 0),
                "capital_at_risk": s.get("capital_at_risk", 0),
                "status": "signal",
                "rr_ratio": s.get("rr_ratio", 0),
                "target_pct": s.get("target_pct", 0),
                "stop_pct": s.get("stop_pct", 0),
                "conditions": _json.dumps(s.get("conditions", {})),
                "ltp": s.get("ltp"),
                "change_pct": s.get("change_pct"),
                "scanner_type": "scalp",
                "gap_pct": s.get("gap_pct", 0),
            }
            _insert("trades", row)
        if active_signals:
            print(f"  Logged {len(active_signals)} signal(s) to Supabase")
    except Exception as e:
        print(f"  [WARN] Supabase logging failed: {e}")

    # Load portfolio metrics
    portfolio_metrics = None
    try:
        from common.db import get_portfolio_metrics_supa
        portfolio_metrics = get_portfolio_metrics_supa(days=30, scanner_type="scalp")
    except Exception:
        pass

    prep_mode = phase in ("PRE_MARKET", "POST_MARKET")

    if prep_mode:
        # Compute next-session prep for all tickers
        print("  Computing next-session prep...")
        output_dir = g.get("output_dir", "output")
        prep_data = []
        for tc in tickers_cfg:
            sym = tc["symbol"]
            d = all_data.get(sym, {"intra": pd.DataFrame(), "daily": pd.DataFrame()})
            intra_ist = compute_vwap(_to_ist(d["intra"])) if not d["intra"].empty else pd.DataFrame()
            prep = compute_session_prep(tc, d["daily"], intra_ist, output_dir)
            prep_data.append(prep)

        # AI advisory (prep mode)
        print("  Generating AI prep briefing...")
        ai_context = build_prep_context(now_ist, prep_data, nifty_state, config)
        ai_text = get_ai_advisory(ai_context, config, prep_mode=True)

        dashboard = render_prep_dashboard(now_ist, phase, prep_data, nifty_state, ai_text)
    else:
        # Live market mode
        print("  Generating AI advisory...")
        ai_context = build_ai_context(phase, now_ist, ticker_states, position_states, nifty_state, config)
        ai_text = get_ai_advisory(ai_context, config)

        dashboard = render_dashboard(now_ist, phase, ticker_states, position_states, nifty_state, ai_text,
                                     portfolio_metrics=portfolio_metrics)

    # Write markdown report
    report_path, report_content = write_markdown_report(
        now_ist, phase,
        prep_data if prep_mode else [],
        ticker_states,
        position_states,
        nifty_state,
        ai_text,
        config,
        prep_mode,
        portfolio_metrics=portfolio_metrics,
    )
    print(f"  Report saved: {report_path}\n")

    # Log scan run to Supabase
    try:
        from common.db import log_scan_run
        active_count = sum(1 for s in ticker_states if s["signal"] == "ACTIVE")
        log_scan_run(
            scanner_type="scalp",
            vix_val=vix_val,
            vix_regime=vix_regime,
            nifty_regime=nifty_regime,
            dow=now_ist.strftime("%A"),
            total_candidates=len(ticker_states),
            active_count=active_count,
            report_markdown=report_content,
            ai_advisory=ai_text,
        )
    except Exception as e:
        print(f"  [WARN] Scan run logging failed: {e}")

    print(dashboard)


if __name__ == "__main__":
    main()
