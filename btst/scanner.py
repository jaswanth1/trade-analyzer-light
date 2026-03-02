"""
BTST (Buy Today Sell Tomorrow) Scanner

Identifies stocks closing near day high with volume surge for overnight hold.
Entry timing: last 90 minutes (2:30-3:15 PM).
Hold: overnight to max 2 trading days. Target 3-5%, Stop 1.5-2%.

Enhanced with composite scoring (regime + convergence + historical hit rate),
rich educational reports, and slim console output.

Usage:
    python -m btst.scanner           # runs after 2:30 PM only
    python -m btst.scanner --force   # runs anytime (manual override)
"""

import argparse
import warnings
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from common.data import (
    fetch_yf, TICKERS, BENCHMARK, CONFIG_PATH, PROJECT_ROOT,
)
from common.indicators import (
    compute_atr, compute_vwap, _to_ist, classify_gaps,
)
from common.market import (
    fetch_india_vix, vix_position_scale, detect_nifty_regime,
    check_earnings_proximity, nifty_making_new_lows, higher_lows_pattern,
    outperforming_nifty, estimate_institutional_flow,
)
from common.news import get_news_and_sentiment
from common.risk import compute_position_size, compute_correlation_clusters, compute_individual_beta_scale
from common.display import fmt, box_top, box_mid, box_bot, box_line, W
from intraday.regime import classify_symbol_regime, classify_month_period, DOW_NAMES
from intraday.explanations import _compute_stock_profile, _format_rupee
from btst.convergence import compute_daily_convergence, compute_overnight_hit_rate
from btst.explanations import (
    generate_btst_explanation, generate_btst_llm_explanation,
    BTST_STRATEGY_DESCRIPTIONS,
)
from btst.regime import compute_overnight_dow_month_stats

warnings.filterwarnings("ignore")

IST = ZoneInfo("Asia/Kolkata")

# ── BTST Constants ─────────────────────────────────────────────────────────

BTST_TARGET_PCT_DEFAULT = 3.0
BTST_STOP_PCT_DEFAULT = 2.0
MAX_BTST_POSITIONS = 3
MAX_BTST_CAPITAL_PCT = 30.0
MAX_HOLD_DAYS = 2
MIN_RUN_TIME = dtime(14, 30)
MIN_RR_RATIO = 1.2
BTST_REPORT_DIR = PROJECT_ROOT / "btst" / "reports"
LONG_ONLY = True

BTST_CONDITION_WEIGHTS = {
    "volume_surge": 3.0,
    "closing_near_high": 2.5,
    "above_vwap": 2.5,
    "higher_low_pattern": 2.0,
    "rs_vs_nifty": 2.0,
    "not_overextended": 1.5,
    "atr_range_ok": 1.5,
    "no_earnings_near": 1.5,
    "bullish_close": 1.0,
    "sector_momentum": 1.0,
}

BTST_MUST_HAVE = ["closing_near_high", "above_vwap", "nifty_ok"]


# ── Overnight Statistics ───────────────────────────────────────────────────

def compute_overnight_stats(daily_df, gap_df):
    """Compute overnight return statistics for days where close > open and close near high.

    Returns dict keyed by gap_type with stats:
        win_rate, avg_pos_return, avg_neg_return, median_target, p90_stop, n_samples
    Also includes DOW-level stats.
    """
    if daily_df.empty or len(daily_df) < 10:
        return {}

    df = daily_df.copy()
    df["day_range"] = df["High"] - df["Low"]
    df["close_position"] = np.where(
        df["day_range"] > 0,
        (df["Close"] - df["Low"]) / df["day_range"],
        0.5,
    )
    df["bullish_close"] = (df["Close"] > df["Open"]) & (df["close_position"] >= 0.8)
    df["next_close"] = df["Close"].shift(-1)
    df["overnight_return"] = (df["next_close"] - df["Close"]) / df["Close"] * 100
    df = df.dropna(subset=["overnight_return"])

    # Merge gap info
    if not gap_df.empty:
        gap_info = gap_df[["gap_type", "day_of_week"]].copy()
        gap_info.index = gap_info.index.normalize() if hasattr(gap_info.index, 'normalize') else gap_info.index
        df.index = df.index.normalize() if hasattr(df.index, 'normalize') else df.index
        df = df.join(gap_info, how="left", rsuffix="_gap")
        if "gap_type_gap" in df.columns:
            df["gap_type"] = df["gap_type_gap"]
        if "day_of_week_gap" in df.columns:
            df["day_of_week"] = df["day_of_week_gap"]

    if "gap_type" not in df.columns:
        df["gap_type"] = "unknown"
    if "day_of_week" not in df.columns:
        df["day_of_week"] = df.index.dayofweek

    # Filter to bullish close days
    bullish_days = df[df["bullish_close"]]
    if bullish_days.empty:
        return {}

    stats = {}

    # Overall stats
    stats["all"] = _compute_group_stats(bullish_days)

    # Per gap type
    for gt in bullish_days["gap_type"].dropna().unique():
        subset = bullish_days[bullish_days["gap_type"] == gt]
        if len(subset) >= 3:
            stats[f"gap_{gt}"] = _compute_group_stats(subset)

    # Per DOW
    for dow in range(5):
        subset = bullish_days[bullish_days["day_of_week"] == dow]
        if len(subset) >= 3:
            dow_name = ["Mon", "Tue", "Wed", "Thu", "Fri"][dow]
            stats[f"dow_{dow_name}"] = _compute_group_stats(subset)

    return stats


def _compute_group_stats(df):
    """Compute overnight stats for a group of days."""
    returns = df["overnight_return"]
    positive = returns[returns > 0]
    negative = returns[returns <= 0]

    win_rate = len(positive) / len(returns) * 100 if len(returns) > 0 else 0
    avg_pos = positive.mean() if not positive.empty else 0
    avg_neg = negative.mean() if not negative.empty else 0
    median_target = positive.median() if not positive.empty else 0
    p90_stop = negative.quantile(0.10) if len(negative) >= 3 else avg_neg

    return {
        "win_rate": round(win_rate, 1),
        "avg_pos_return": round(avg_pos, 3),
        "avg_neg_return": round(avg_neg, 3),
        "median_target": round(median_target, 3),
        "p90_stop": round(abs(p90_stop), 3),
        "n_samples": len(returns),
    }


# ── Closing Strength ──────────────────────────────────────────────────────

def compute_closing_strength(intra_today, daily_df):
    """Compute closing strength metrics from today's intraday data.

    Returns dict with: close_position, above_vwap, volume_surge_ratio,
    bullish_candle, day_range_pct, ltp, day_open, day_high, day_low.
    """
    if intra_today.empty:
        return {}

    ltp = intra_today["Close"].iloc[-1]
    day_open = intra_today["Open"].iloc[0]
    day_high = intra_today["High"].max()
    day_low = intra_today["Low"].min()
    day_range = day_high - day_low

    close_position = (ltp - day_low) / day_range if day_range > 0 else 0.5

    vwap_val = intra_today["vwap"].iloc[-1] if "vwap" in intra_today.columns else np.nan
    above_vwap = ltp > vwap_val if not np.isnan(vwap_val) else False

    n_bars_1hr = 12
    if len(intra_today) >= n_bars_1hr:
        last_hr_vol = intra_today["Volume"].iloc[-n_bars_1hr:].sum()
        prior_same_window_vol = _get_window_median_volume(intra_today, daily_df, n_bars_1hr)
        volume_surge_ratio = last_hr_vol / prior_same_window_vol if prior_same_window_vol > 0 else 1.0
    else:
        last_hr_vol = intra_today["Volume"].sum()
        volume_surge_ratio = 1.0

    bullish_candle = ltp > day_open
    day_range_pct = (day_range / day_open * 100) if day_open > 0 else 0

    return {
        "close_position": round(close_position, 3),
        "above_vwap": above_vwap,
        "vwap_val": vwap_val,
        "volume_surge_ratio": round(volume_surge_ratio, 2),
        "bullish_candle": bullish_candle,
        "day_range_pct": round(day_range_pct, 3),
        "ltp": ltp,
        "day_open": day_open,
        "day_high": day_high,
        "day_low": day_low,
        "last_hr_vol": last_hr_vol if len(intra_today) >= n_bars_1hr else intra_today["Volume"].sum(),
    }


def _get_window_median_volume(intra_today, daily_df, n_bars):
    """Get median volume for the last n bars window from historical daily volume."""
    if daily_df.empty or len(daily_df) < 5:
        return 1.0
    fraction = n_bars / 75.0
    median_daily_vol = daily_df["Volume"].iloc[-21:-1].median() if len(daily_df) > 20 else daily_df["Volume"].median()
    return median_daily_vol * fraction if not np.isnan(median_daily_vol) else 1.0


# ── Target/Stop Computation ──────────────────────────────────────────────

def compute_btst_targets(overnight_stats, atr_pct):
    """Compute BTST target and stop percentages.

    Target: max(median_positive_overnight_return * 1.2, 0.7 * ATR%), capped at 5%
    Stop: max(p90_negative_overnight_return, 0.5 * ATR%), capped at 2%
    """
    if not overnight_stats or "all" not in overnight_stats:
        return BTST_TARGET_PCT_DEFAULT, BTST_STOP_PCT_DEFAULT

    stats = overnight_stats["all"]
    if stats["n_samples"] < 10:
        return BTST_TARGET_PCT_DEFAULT, BTST_STOP_PCT_DEFAULT

    median_pos = stats["median_target"]
    p90_neg = stats["p90_stop"]

    if median_pos > 0:
        target = max(median_pos * 1.2, 0.7 * atr_pct if not np.isnan(atr_pct) else 0)
    else:
        target = 0.7 * atr_pct if not np.isnan(atr_pct) else BTST_TARGET_PCT_DEFAULT
    target = min(target, 5.0)
    target = max(target, 1.0)

    if p90_neg > 0:
        stop = max(p90_neg, 0.5 * atr_pct if not np.isnan(atr_pct) else 0)
    else:
        stop = 0.5 * atr_pct if not np.isnan(atr_pct) else BTST_STOP_PCT_DEFAULT
    stop = min(stop, 2.0)
    stop = max(stop, 0.5)

    return round(target, 2), round(stop, 2)


# ── Evaluate BTST ─────────────────────────────────────────────────────────

def evaluate_btst(symbol, intra_df, daily_df, nifty_state, vix_info, sector_data,
                  nifty_daily=None, news_sentiment=None):
    """Evaluate BTST conditions for a single symbol.

    Enhanced with composite scoring: regime alignment, convergence,
    historical hit rate, and news sentiment.
    """
    cfg = TICKERS.get(symbol, {"name": symbol, "sector": ""})
    result = {
        "symbol": symbol,
        "name": cfg.get("name", symbol),
        "sector": cfg.get("sector", ""),
        "signal": "NO_DATA",
        "conditions": {},
        "weighted_score": 0.0,
        "composite_score": 0.0,
        "ltp": np.nan,
        "change_pct": np.nan,
        "entry_price": np.nan,
        "target_price": np.nan,
        "stop_price": np.nan,
        "target_pct": BTST_TARGET_PCT_DEFAULT,
        "stop_pct": BTST_STOP_PCT_DEFAULT,
        "overnight_stats": {},
        "overnight_wr": 0.0,
        "closing_strength": {},
        "action_text": "",
        "recommended_qty": 0,
        "capital_at_risk": 0,
        "risk_pct": 0,
        "atr_pct": np.nan,
        # New enhanced fields
        "symbol_regime": {},
        "convergence_score": 0,
        "convergence_detail": "",
        "convergence_aligned": [],
        "convergence_conflicting": [],
        "historical_hit_rate": 0,
        "historical_sample_size": 0,
        "historical_context": "",
        "dow_month_stats": {},
        "dow_wr": 0,
        "month_period": "",
        "month_period_wr": 0,
        "news_sentiment": 0,
        "news_summary": "",
        "has_material_event": False,
        "inst_flow": "",
        "regime_alignment_score": 0,
    }

    if intra_df.empty or daily_df.empty:
        result["action_text"] = "No data available"
        return result

    # Convert to IST + compute VWAP
    intra_ist = compute_vwap(_to_ist(intra_df))
    now = datetime.now(IST)
    today = now.date()
    intra_today = intra_ist[intra_ist.index.date == today]
    if intra_today.empty:
        intra_today = intra_ist.tail(20)
    if intra_today.empty:
        result["action_text"] = "No intraday bars"
        return result

    # Basic price
    ltp = intra_today["Close"].iloc[-1]
    day_open = intra_today["Open"].iloc[0]
    change_pct = (ltp / day_open - 1) * 100 if day_open > 0 else 0
    result["ltp"] = ltp
    result["change_pct"] = change_pct

    # ATR
    atr_val = compute_atr(daily_df) if len(daily_df) >= 14 else np.nan
    atr_pct = atr_val / ltp * 100 if not np.isnan(atr_val) and ltp > 0 else np.nan
    result["atr_pct"] = atr_pct

    # Closing strength
    closing = compute_closing_strength(intra_today, daily_df)
    result["closing_strength"] = closing
    if not closing:
        result["action_text"] = "Cannot compute closing strength"
        return result

    # Overnight stats from historical data (cached per symbol per day)
    from common.analysis_cache import get_cached, set_cached, TTL_DAILY
    cached_overnight = get_cached("overnight_stats", symbol=symbol, max_age_seconds=TTL_DAILY)
    if cached_overnight is not None:
        overnight = cached_overnight
    else:
        gap_df = classify_gaps(daily_df)
        overnight = compute_overnight_stats(daily_df, gap_df)
        set_cached("overnight_stats", overnight, symbol=symbol)
    result["overnight_stats"] = overnight
    all_stats = overnight.get("all", {})
    result["overnight_wr"] = all_stats.get("win_rate", 0)

    # Compute adaptive targets
    target_pct, stop_pct = compute_btst_targets(overnight, atr_pct)
    result["target_pct"] = target_pct
    result["stop_pct"] = stop_pct
    result["entry_price"] = ltp
    result["target_price"] = ltp * (1 + target_pct / 100)
    result["stop_price"] = ltp * (1 - stop_pct / 100)

    # ── MIN RR gate ──
    rr_ratio = target_pct / stop_pct if stop_pct > 0 else 0
    if rr_ratio < MIN_RR_RATIO:
        result["signal"] = "AVOID"
        result["action_text"] = f"RR too low ({rr_ratio:.1f} < {MIN_RR_RATIO}) — skip"
        return result

    # ── Evaluate conditions ──
    conditions = {}

    # Must-have gates
    conditions["closing_near_high"] = closing["close_position"] >= 0.80
    conditions["above_vwap"] = closing["above_vwap"]
    nifty_regime = nifty_state.get("regime", "unknown")
    nifty_new_lows = nifty_state.get("new_lows", False)
    conditions["nifty_ok"] = (nifty_regime != "bearish") and (not nifty_new_lows)

    # Weighted conditions
    conditions["volume_surge"] = closing["volume_surge_ratio"] >= 1.3
    conditions["higher_low_pattern"] = higher_lows_pattern(intra_ist) if len(intra_ist) >= 9 else False

    nifty_ist = nifty_state.get("nifty_ist", pd.DataFrame())
    conditions["rs_vs_nifty"] = outperforming_nifty(intra_ist, nifty_ist) if not nifty_ist.empty else False

    if not np.isnan(atr_pct) and atr_pct > 0:
        move_from_open = abs(change_pct)
        conditions["not_overextended"] = move_from_open <= 1.5 * atr_pct
    else:
        conditions["not_overextended"] = True

    if not np.isnan(atr_pct) and atr_pct > 0:
        conditions["atr_range_ok"] = closing["day_range_pct"] >= 0.5 * atr_pct
    else:
        conditions["atr_range_ok"] = True

    near_earnings, earnings_date = check_earnings_proximity(symbol, days_ahead=3)
    conditions["no_earnings_near"] = not near_earnings
    if near_earnings:
        result["earnings_date"] = earnings_date

    conditions["bullish_close"] = closing["bullish_candle"]

    sector_sym = cfg.get("sector", "")
    if sector_data and sector_sym in sector_data:
        sector_df = sector_data[sector_sym]
        if not sector_df.empty and len(sector_df) >= 2:
            sector_ret = (sector_df["Close"].iloc[-1] / sector_df["Close"].iloc[-2] - 1) * 100
            conditions["sector_momentum"] = sector_ret > 0
        else:
            conditions["sector_momentum"] = True
    else:
        conditions["sector_momentum"] = True

    result["conditions"] = conditions

    # ── Weighted scoring (base score) ──
    total_weight = sum(BTST_CONDITION_WEIGHTS.get(k, 1.0) for k in conditions if k not in BTST_MUST_HAVE)
    weighted_hit = sum(
        BTST_CONDITION_WEIGHTS.get(k, 1.0)
        for k, v in conditions.items()
        if v and k not in BTST_MUST_HAVE
    )
    weighted_score = weighted_hit / total_weight if total_weight > 0 else 0
    result["weighted_score"] = round(weighted_score, 3)

    # ── Must-have gate check ──
    must_have_pass = all(conditions.get(k, False) for k in BTST_MUST_HAVE)

    # ── VIX stress override ──
    vix_val, vix_regime = vix_info
    if vix_regime == "stress":
        result["signal"] = "AVOID"
        result["action_text"] = f"VIX STRESS ({vix_val}) — BTST suspended"
        return result

    # ── Earnings override ──
    if near_earnings:
        result["signal"] = "AVOID"
        result["action_text"] = f"Earnings on {earnings_date} — BTST avoided"
        return result

    # ── NEW: Enhanced evaluation ──

    # 1. Symbol regime
    symbol_regime = classify_symbol_regime(daily_df, intra_ist, nifty_daily)
    result["symbol_regime"] = symbol_regime

    # Regime alignment score for long direction
    regime_score = 0.0
    trend = symbol_regime.get("trend", "sideways")
    if trend in ("strong_up", "mild_up"):
        regime_score += 0.4
    elif trend == "sideways":
        regime_score += 0.15
    weekly = symbol_regime.get("weekly_trend", "sideways")
    if weekly == "up":
        regime_score += 0.3
    elif weekly == "sideways":
        regime_score += 0.1
    momentum = symbol_regime.get("momentum", "steady")
    if momentum == "accelerating":
        regime_score += 0.2
    elif momentum == "steady":
        regime_score += 0.05
    rs = symbol_regime.get("relative_strength", "inline")
    if rs == "outperforming":
        regime_score += 0.1
    result["regime_alignment_score"] = round(regime_score, 3)

    # 2. Daily convergence
    conv = compute_daily_convergence(daily_df, symbol_regime, nifty_daily)
    result["convergence_score"] = conv["score"]
    result["convergence_aligned"] = conv["aligned"]
    result["convergence_conflicting"] = conv["conflicting"]
    if conv["aligned"] or conv["conflicting"]:
        result["convergence_detail"] = (
            f"{conv['n_aligned']}/{conv['total']} aligned: "
            f"{', '.join(conv['aligned'][:4]) if conv['aligned'] else 'none'}"
        )

    # 3. DOW + month-period
    today_dow = now.weekday()
    month_period = classify_month_period(now)
    result["month_period"] = month_period

    dow_month = compute_overnight_dow_month_stats(daily_df)
    result["dow_month_stats"] = dow_month
    dow_name = DOW_NAMES.get(today_dow, "Unknown")
    dow_data = dow_month.get(dow_name, {})
    dow_all = dow_data.get("all", {})
    result["dow_wr"] = dow_all.get("win_rate", 0)
    mp_data = dow_data.get(month_period, {})
    result["month_period_wr"] = mp_data.get("win_rate", 0)

    # 4. Historical hit rate
    hist = compute_overnight_hit_rate(daily_df, today_dow, month_period)
    result["historical_hit_rate"] = hist["hit_rate"]
    result["historical_sample_size"] = hist["sample_size"]
    result["historical_context"] = hist["context"]

    # 5. News sentiment
    if news_sentiment and symbol in news_sentiment:
        ns = news_sentiment[symbol]
        result["news_sentiment"] = ns.get("sentiment", 0)
        result["news_summary"] = ns.get("summary", "")
        result["has_material_event"] = ns.get("has_material_event", False)

    # ── Material event override ──
    if result["has_material_event"]:
        result["signal"] = "AVOID"
        result["action_text"] = "Material event detected — BTST avoided"
        return result

    # 6. Composite scoring
    base_score = weighted_score
    convergence_norm = conv["score"] / 100  # 0-1
    hist_rate_norm = hist["hit_rate"] / 100 if hist["hit_rate"] > 0 else 0.5  # default neutral
    regime_norm = regime_score  # already 0-1

    # News modifier: ±5%
    news_mod = 0.0
    ns_val = result["news_sentiment"]
    if ns_val > 0.3:
        news_mod = 0.05
    elif ns_val < -0.3:
        news_mod = -0.05

    composite = (
        0.40 * base_score
        + 0.25 * convergence_norm
        + 0.20 * hist_rate_norm
        + 0.15 * regime_norm
        + news_mod
    )
    composite = max(0.0, min(1.0, composite))
    result["composite_score"] = round(composite, 3)

    # ── Signal classification using composite ──
    overnight_wr = result["overnight_wr"]
    if must_have_pass and composite >= 0.80 and overnight_wr > 55:
        result["signal"] = "STRONG_BUY"
        result["action_text"] = f"BTST STRONG — composite {composite:.0%}, overnight WR {overnight_wr:.0f}%"
    elif must_have_pass and composite >= 0.65:
        result["signal"] = "BUY"
        result["action_text"] = f"BTST BUY — composite {composite:.0%}"
    elif must_have_pass:
        result["signal"] = "WATCH"
        failed = [k for k, v in conditions.items() if not v and k not in BTST_MUST_HAVE]
        result["action_text"] = f"BTST watch — composite {composite:.0%}, missing: {', '.join(failed[:3])}"
    else:
        failed_gates = [k for k in BTST_MUST_HAVE if not conditions.get(k, False)]
        result["signal"] = "AVOID"
        result["action_text"] = f"Gate blocked: {', '.join(failed_gates)}"

    # Override: downtrend + weekly down → demote BUY to WATCH
    if result["signal"] == "BUY" and trend in ("mild_down", "strong_down") and weekly == "down":
        result["signal"] = "WATCH"
        result["action_text"] = f"Demoted — downtrend + weekly down (composite {composite:.0%})"

    return result


# ── Signal Ranking ────────────────────────────────────────────────────────

def rank_btst_signals(ticker_states):
    """Rank BTST signals by composite_score.

    Priority: STRONG_BUY > BUY > WATCH > AVOID
    Within tier: by composite_score descending, then overnight_wr.
    """
    signal_order = {"STRONG_BUY": 0, "BUY": 1, "WATCH": 2, "AVOID": 3, "NO_DATA": 4}

    return sorted(
        ticker_states,
        key=lambda s: (
            signal_order.get(s["signal"], 5),
            -s["composite_score"],
            -s["overnight_wr"],
        ),
    )


# ── Dashboard Rendering (SLIM — Console = Summary Only) ──────────────────

def render_btst_dashboard(signals, nifty_state, vix_info, inst_flow, dow_name,
                          month_period, report_path, metrics=None):
    """Render slim BTST terminal dashboard — summary only, detail in report."""
    vix_val, vix_regime = vix_info
    now = datetime.now(IST)
    lines = []

    lines.append(box_top())
    regime = nifty_state.get("regime", "unknown").upper()
    vix_str = f"VIX: {vix_val} ({vix_regime.upper()})" if vix_val else "VIX: N/A"
    lines.append(box_line(f"BTST SCANNER — {now.strftime('%Y-%m-%d %H:%M')} IST"))
    lines.append(box_line(f"Nifty: {regime} | {vix_str} | Flow: {inst_flow}"))
    lines.append(box_line(f"DOW: {dow_name} | Period: {month_period} | Max slots: {MAX_BTST_POSITIONS}"))
    lines.append(box_mid())

    # Compact signal table
    active = [s for s in signals if s["signal"] in ("STRONG_BUY", "BUY")]
    watching = [s for s in signals if s["signal"] == "WATCH"]
    avoided = [s for s in signals if s["signal"] == "AVOID"]

    if active:
        lines.append(box_line(f"{'Symbol':<14} {'Signal':<12} {'Composite':>9} {'Action':<30}"))
        lines.append(box_line("─" * (W - 4)))
        for s in active:
            sym = s["symbol"].replace(".NS", "")
            sig = s["signal"]
            comp = f"{s['composite_score']:.0%}"
            act = f"BUY @ {fmt(s['entry_price'])} → {fmt(s['target_price'])}"
            lines.append(box_line(f"{sym:<14} {sig:<12} {comp:>9} {act:<30}"))
        lines.append(box_line())
    else:
        lines.append(box_line("No BUY signals today."))
        lines.append(box_line())

    # Count summary
    n_strong = sum(1 for s in signals if s["signal"] == "STRONG_BUY")
    n_buy = sum(1 for s in signals if s["signal"] == "BUY")
    n_watch = len(watching)
    n_avoid = len(avoided)
    lines.append(box_line(f"STRONG: {n_strong} | BUY: {n_buy} | WATCH: {n_watch} | AVOID: {n_avoid}"))

    # Portfolio metrics
    if metrics and metrics.get("n_trades", 0) > 0:
        pm = metrics
        lines.append(box_line(f"30d: {pm['n_trades']} trades | WR: {pm['win_rate']}% | P&L: {pm['gross_pnl']:+,.0f}"))

    lines.append(box_mid())
    lines.append(box_line(f"Report saved: {report_path}"))
    lines.append(box_bot())

    return "\n".join(lines)


# ── Markdown Report (COMPLETE REWRITE — Report = All Detail) ─────────────

def write_btst_report(signals, nifty_state, vix_info, inst_flow, dow_name,
                      month_period, all_data, nifty_daily, news_data):
    """Write rich educational BTST report."""
    BTST_REPORT_DIR.mkdir(exist_ok=True)
    now = datetime.now(IST)
    report_path = BTST_REPORT_DIR / f"btst_{now.strftime('%Y-%m-%d')}.md"

    vix_val, vix_regime = vix_info
    nifty_regime = nifty_state.get("regime", "unknown")

    strong = [s for s in signals if s["signal"] == "STRONG_BUY"]
    buys = [s for s in signals if s["signal"] == "BUY"]
    watching = [s for s in signals if s["signal"] == "WATCH"]
    avoided = [s for s in signals if s["signal"] in ("AVOID", "NO_DATA")]
    active = strong + buys

    lines = []

    # ── 1. Header ──
    lines.append(f"# BTST Scanner — {now.strftime('%Y-%m-%d %H:%M')} IST\n")
    lines.append(f"**Nifty**: {nifty_regime.upper()} | "
                 f"**VIX**: {vix_val} ({vix_regime}) | "
                 f"**Institutional Flow**: {inst_flow}")
    lines.append(f"**DOW**: {dow_name} | **Period**: {month_period} | "
                 f"**Max BTST Slots**: {MAX_BTST_POSITIONS}\n")

    # ── 2. How to Read This Report ──
    lines.append("## How to Read This Report\n")
    lines.append("| Term | Meaning |")
    lines.append("|------|---------|")
    lines.append("| **Composite Score** | Weighted blend: 40% condition score + 25% convergence + 20% historical hit rate + 15% regime alignment ± news |")
    lines.append("| **Convergence** | How many daily indicators (EMA, RSI, MACD, volume, weekly trend, RS) agree with the trade |")
    lines.append("| **Overnight WR** | Historical win rate when this stock had a bullish close pattern — next day close profitable |")
    lines.append("| **Close Position** | Where price closed in today's range (1.0 = at high, 0.0 = at low). BTST needs ≥ 0.80 |")
    lines.append("| **STRONG_BUY** | Composite ≥ 80% + overnight WR > 55% — highest conviction overnight hold |")
    lines.append("| **BUY** | Composite ≥ 65% — good overnight edge, normal position size |")
    lines.append("| **All signals are BUY only** | BTST is always long — buy today, sell tomorrow. No shorting. |")
    lines.append("")

    # ── 3. Market Context ──
    lines.append("## Market Context\n")
    market_news = news_data.get("_market", "") if news_data else ""
    if market_news:
        lines.append(market_news)
        lines.append("")
    lines.append(f"- Nifty regime: **{nifty_regime.upper()}** — "
                 f"{'favourable for overnight longs' if nifty_regime in ('bullish', 'range') else 'caution for overnight holds'}")
    if vix_val:
        if vix_regime == "low_vol":
            lines.append(f"- VIX at {vix_val} (low) — calm overnight environment, smaller gaps expected")
        elif vix_regime == "normal":
            lines.append(f"- VIX at {vix_val} (normal) — standard overnight risk")
        elif vix_regime == "elevated":
            lines.append(f"- VIX at {vix_val} (elevated) — wider overnight gaps possible, size down")
    lines.append(f"- Institutional flow: **{inst_flow}**")
    lines.append("")

    # ── 4. Recommended Trades summary table ──
    if active:
        lines.append("## Recommended Trades\n")
        lines.append("| # | Symbol | Composite | Entry | Target | Stop | RR | OvernightWR | Risk/₹1L | Signal |")
        lines.append("|---|--------|-----------|-------|--------|------|----|-------------|----------|--------|")
        for i, s in enumerate(active, 1):
            sym = s["symbol"].replace(".NS", "")
            rr = s["target_pct"] / s["stop_pct"] if s["stop_pct"] > 0 else 0
            profile = _compute_stock_profile(s, all_data.get(s["symbol"], {}).get("daily", pd.DataFrame()), nifty_daily)
            risk_1l = _format_rupee(profile["risk_per_lakh"]) if profile["risk_per_lakh"] > 0 else "N/A"
            lines.append(
                f"| {i} | {sym} | {s['composite_score']:.0%} | "
                f"{fmt(s['entry_price'])} | {fmt(s['target_price'])} | {fmt(s['stop_price'])} | "
                f"{rr:.1f}:1 | {s['overnight_wr']:.0f}% | {risk_1l} | {s['signal']} |"
            )
        lines.append("")

    # ── 5. Quick Action Plan ──
    if active:
        lines.append("## Quick Action Plan\n")
        for i, s in enumerate(active[:3], 1):
            sym = s["symbol"].replace(".NS", "")
            lines.append(f"**{i}. {sym}** — BUY @ {fmt(s['entry_price'])}, "
                         f"Target {fmt(s['target_price'])} (+{s['target_pct']:.1f}%), "
                         f"Stop {fmt(s['stop_price'])} (-{s['stop_pct']:.1f}%)")
            lines.append(f"   Exit next day by 10:30 AM or at target. "
                         f"Trail stop after +1.5%. Max hold: {MAX_HOLD_DAYS} days.\n")

    # ── 6. Detailed Setups ──
    if active:
        lines.append("## Detailed Setups\n")
        for s in active:
            sym = s["symbol"].replace(".NS", "")
            chg = f"{s['change_pct']:+.2f}%" if not np.isnan(s["change_pct"]) else "N/A"
            cs = s.get("closing_strength", {})
            regime = s.get("symbol_regime", {})

            lines.append(f"### {sym} — {s['name']} | {s['signal']}\n")

            # Strategy explanation
            vol_surge = cs.get("volume_surge_ratio", 1.0)
            trend = regime.get("trend", "sideways")
            weekly = regime.get("weekly_trend", "sideways")
            if vol_surge >= 1.5:
                strat_key = "volume_breakout"
            elif trend in ("strong_up", "mild_up") and weekly == "up":
                strat_key = "trend_continuation"
            else:
                strat_key = "closing_strength"
            strat_desc = BTST_STRATEGY_DESCRIPTIONS.get(strat_key, "")
            if strat_desc:
                lines.append(f"> {strat_desc}\n")

            # Action
            rr = s["target_pct"] / s["stop_pct"] if s["stop_pct"] > 0 else 0
            lines.append(f"**Action**: BUY (buy today, sell tomorrow) @ **{fmt(s['entry_price'])}** ({chg})")
            lines.append(f"- Target: {fmt(s['target_price'])} (+{s['target_pct']:.1f}%) | "
                         f"Stop: {fmt(s['stop_price'])} (-{s['stop_pct']:.1f}%) | "
                         f"RR: {rr:.1f}:1")

            # Context: regime
            lines.append(f"\n**Context**: "
                         f"Trend: {regime.get('trend', 'N/A')}, "
                         f"Volatility: {regime.get('volatility', 'N/A')}, "
                         f"Momentum: {regime.get('momentum', 'N/A')}, "
                         f"Weekly: {regime.get('weekly_trend', 'N/A')}, "
                         f"RS: {regime.get('relative_strength', 'N/A')}")

            # Convergence detail
            conv_aligned = s.get("convergence_aligned", [])
            conv_conflicting = s.get("convergence_conflicting", [])
            lines.append(f"\n**Convergence**: {s.get('convergence_score', 0)}%")
            if conv_aligned:
                lines.append(f"- Aligned: {', '.join(conv_aligned)}")
            if conv_conflicting:
                lines.append(f"- Conflicting: {', '.join(conv_conflicting)}")

            # Per-₹1L capital
            daily_df = all_data.get(s["symbol"], {}).get("daily", pd.DataFrame())
            profile = _compute_stock_profile(s, daily_df, nifty_daily)
            if profile["shares_per_lakh"] > 0:
                lines.append(f"\n**Per ₹1L capital**: ~{profile['shares_per_lakh']} shares | "
                             f"Risk: {_format_rupee(profile['risk_per_lakh'])} | "
                             f"Reward: {_format_rupee(profile['reward_per_lakh'])}")

            # Overnight stats table by gap type
            o_stats = s.get("overnight_stats", {})
            gap_types = [k for k in o_stats if k.startswith("gap_")]
            if gap_types or "all" in o_stats:
                lines.append("\n**Overnight Stats by Gap Type**:\n")
                lines.append("| Gap Type | WR | Avg+ | Avg- | N |")
                lines.append("|----------|----|------|------|---|")
                if "all" in o_stats:
                    st = o_stats["all"]
                    lines.append(f"| All | {st['win_rate']}% | +{st['avg_pos_return']:.2f}% | "
                                 f"{st['avg_neg_return']:.2f}% | {st['n_samples']} |")
                for gt in sorted(gap_types):
                    st = o_stats[gt]
                    gt_label = gt.replace("gap_", "").replace("_", " ").title()
                    lines.append(f"| {gt_label} | {st['win_rate']}% | +{st['avg_pos_return']:.2f}% | "
                                 f"{st['avg_neg_return']:.2f}% | {st['n_samples']} |")
                lines.append("")

            # DOW + month-period win rates
            lines.append(f"**DOW/Period**: {dow_name} overnight WR: {s.get('dow_wr', 0):.0f}% | "
                         f"{month_period} period WR: {s.get('month_period_wr', 0):.0f}%")

            # Historical hit rate
            hist_ctx = s.get("historical_context", "")
            if hist_ctx:
                lines.append(f"\n**Historical**: {hist_ctx}")

            # News summary
            news_sum = s.get("news_summary", "")
            if news_sum:
                lines.append(f"\n**News**: {news_sum}")

            # Risks
            risks = _collect_risks(s, profile)
            if risks:
                lines.append("\n**Risks**:")
                for r in risks:
                    lines.append(f"- {r}")

            # Conditions table
            lines.append(f"\n| Condition | Weight | Met |")
            lines.append(f"|-----------|--------|-----|")
            for k, v in s["conditions"].items():
                weight = BTST_CONDITION_WEIGHTS.get(k, 1.0)
                marker = "Yes" if v else "**No**"
                lines.append(f"| {k} | {weight} | {marker} |")

            # Verdict
            composite = s.get("composite_score", 0)
            if s["signal"] == "STRONG_BUY":
                lines.append(f"\n**Verdict**: HIGH CONVICTION (composite {composite:.0%}) — "
                             f"multiple factors align. Full size.\n")
            else:
                lines.append(f"\n**Verdict**: GOOD SETUP (composite {composite:.0%}) — "
                             f"edge is there but not overwhelming. Normal size.\n")

            lines.append("---\n")

    # ── 7. Watch List — Detailed ──
    if watching:
        lines.append("## Watch List — Detailed\n")
        lines.append("> These setups have potential but one or more gates failed. "
                     "Monitor and enter only if conditions improve.\n")
        for s in watching:
            sym = s["symbol"].replace(".NS", "")
            chg = f"{s['change_pct']:+.2f}%" if not np.isnan(s["change_pct"]) else "N/A"
            cs = s.get("closing_strength", {})
            regime = s.get("symbol_regime", {})

            lines.append(f"### {sym} — {s['name']} | WATCH\n")
            lines.append(f"**Why watching**: {s['action_text']}\n")

            # Strategy explanation
            vol_surge = cs.get("volume_surge_ratio", 1.0)
            trend = regime.get("trend", "sideways")
            weekly = regime.get("weekly_trend", "sideways")
            if vol_surge >= 1.5:
                strat_key = "volume_breakout"
            elif trend in ("strong_up", "mild_up") and weekly == "up":
                strat_key = "trend_continuation"
            else:
                strat_key = "closing_strength"
            strat_desc = BTST_STRATEGY_DESCRIPTIONS.get(strat_key, "")
            if strat_desc:
                lines.append(f"> {strat_desc}\n")

            # Action
            rr = s["target_pct"] / s["stop_pct"] if s.get("stop_pct", 0) > 0 else 0
            lines.append(f"**If conditions improve**: BUY @ **{fmt(s['entry_price'])}** ({chg})")
            lines.append(f"- Target: {fmt(s['target_price'])} (+{s['target_pct']:.1f}%) | "
                         f"Stop: {fmt(s['stop_price'])} (-{s['stop_pct']:.1f}%) | "
                         f"RR: {rr:.1f}:1")

            # Context: regime
            lines.append(f"\n**Context**: "
                         f"Trend: {regime.get('trend', 'N/A')}, "
                         f"Volatility: {regime.get('volatility', 'N/A')}, "
                         f"Momentum: {regime.get('momentum', 'N/A')}, "
                         f"Weekly: {regime.get('weekly_trend', 'N/A')}, "
                         f"RS: {regime.get('relative_strength', 'N/A')}")

            # Convergence
            conv_aligned = s.get("convergence_aligned", [])
            conv_conflicting = s.get("convergence_conflicting", [])
            lines.append(f"\n**Convergence**: {s.get('convergence_score', 0)}%")
            if conv_aligned:
                lines.append(f"- Aligned: {', '.join(conv_aligned)}")
            if conv_conflicting:
                lines.append(f"- Conflicting: {', '.join(conv_conflicting)}")

            # Per-₹1L capital
            daily_df_w = all_data.get(s["symbol"], {}).get("daily", pd.DataFrame())
            profile_w = _compute_stock_profile(s, daily_df_w, nifty_daily)
            if profile_w["shares_per_lakh"] > 0:
                lines.append(f"\n**Per ₹1L capital**: ~{profile_w['shares_per_lakh']} shares | "
                             f"Risk: {_format_rupee(profile_w['risk_per_lakh'])} | "
                             f"Reward: {_format_rupee(profile_w['reward_per_lakh'])}")

            # DOW + month-period win rates
            lines.append(f"\n**DOW/Period**: {dow_name} overnight WR: {s.get('dow_wr', 0):.0f}% | "
                         f"{month_period} period WR: {s.get('month_period_wr', 0):.0f}%")

            # Historical hit rate
            hist_ctx = s.get("historical_context", "")
            if hist_ctx:
                lines.append(f"\n**Historical**: {hist_ctx}")

            # Conditions table
            if s.get("conditions"):
                lines.append(f"\n| Condition | Weight | Met |")
                lines.append(f"|-----------|--------|-----|")
                for k, v in s["conditions"].items():
                    weight = BTST_CONDITION_WEIGHTS.get(k, 1.0)
                    marker = "Yes" if v else "**No**"
                    lines.append(f"| {k} | {weight} | {marker} |")

            # Risks
            risks = _collect_risks(s, profile_w)
            if risks:
                lines.append("\n**Risks**:")
                for r in risks:
                    lines.append(f"- {r}")

            lines.append(f"\n**Verdict**: WATCHLIST ONLY — composite {s.get('composite_score', 0):.0%}. "
                         f"Monitor but don't enter until conditions improve.\n")
            lines.append("---\n")
        lines.append("")

    # ── 8. Why No Signals (educational when nothing triggers) ──
    if not active and not watching:
        lines.append("## Why No Signals Today\n")
        lines.append("> **This is a feature, not a bug.** The scanner's job is to protect capital "
                     "as much as to find trades. Not trading on bad days is the edge.\n")

        # Analyze common avoid reasons
        avoid_reasons = {}
        for s in signals:
            reason = s.get("action_text", "")
            if reason:
                # Group similar reasons
                if "Gate blocked" in reason:
                    key = "Gate blocked"
                elif "VIX STRESS" in reason:
                    key = "VIX stress"
                elif "Earnings" in reason:
                    key = "Earnings proximity"
                elif "Material event" in reason:
                    key = "Material event"
                elif "RR too low" in reason:
                    key = "Risk-reward too low"
                else:
                    key = reason[:50]
                avoid_reasons[key] = avoid_reasons.get(key, 0) + 1

        if avoid_reasons:
            lines.append("| Reason | Count |")
            lines.append("|--------|-------|")
            for reason, count in sorted(avoid_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"| {reason} | {count} |")
            lines.append("")

        # Contextual education
        if nifty_regime == "bearish":
            lines.append("**Nifty is BEARISH** — the `nifty_ok` gate blocks all BTST signals "
                         "when Nifty is in a confirmed downtrend. BTST is inherently long-only, "
                         "so holding overnight in a bearish market exposes you to gap-down risk "
                         "without a directional edge.\n")
        if dow_name == "Friday":
            lines.append("**Friday BTST carries weekend risk** — you're holding through Saturday "
                         "and Sunday where global events can move markets. Any news over the "
                         "weekend affects Monday's opening gap. Historical data shows Friday BTST "
                         "setups have lower win rates.\n")
        if month_period == "expiry_week":
            lines.append("**Expiry week** — options expiry creates erratic moves and wider spreads. "
                         "Overnight gaps tend to be wider during expiry week due to derivatives "
                         "unwinding.\n")
        if vix_regime == "elevated":
            lines.append("**VIX elevated** — high VIX means wider overnight gaps (both up and down). "
                         "Even if a setup looks good, the stop could be hit at the gap itself.\n")

        lines.append("### What to Learn\n")
        lines.append("- Not every day is a BTST day. The best traders wait for high-probability setups.")
        lines.append("- Track these \"no-trade\" days and compare next-day gaps. "
                     "How many would have been gap-downs? That's your saved capital.")
        lines.append("- The conditions that matter most: Nifty direction (must not be bearish), "
                     "closing strength (close near high), and volume confirmation (institutional interest).")
        lines.append("")

    # ── 9. Avoided Signals Summary (educational) ──
    if avoided:
        lines.append("## Avoided Signals — Summary\n")
        lines.append("> Understanding WHY these were avoided helps you recognize bad setups.\n")
        lines.append("| Symbol | LTP | Change | Reason |")
        lines.append("|--------|-----|--------|--------|")
        for s in avoided[:15]:  # show top 15
            sym = s["symbol"].replace(".NS", "")
            chg = f"{s['change_pct']:+.2f}%" if not np.isnan(s["change_pct"]) else "N/A"
            reason = s.get("action_text", "N/A")
            lines.append(f"| {sym} | {fmt(s['ltp'])} | {chg} | {reason} |")
        if len(avoided) > 15:
            lines.append(f"| ... | | | +{len(avoided) - 15} more |")
        lines.append("")

    # ── 10. Learning Points ──
    lines.append("## Learning Points\n")
    lines.append("### BTST Strategy Primer\n")
    lines.append("BTST (Buy Today Sell Tomorrow) captures overnight momentum:\n")
    lines.append("1. **Entry**: Buy in the last 90 minutes when the stock closes near its high "
                 "with above-average volume")
    lines.append("2. **Edge**: Institutional accumulation that pushed price to close near the high "
                 "often carries into the next session")
    lines.append("3. **Risk**: Overnight gap-down (the single biggest risk) — global events, "
                 "sector news, or broad market selloffs can gap the stock below your stop at open")
    lines.append("4. **Exit**: Next day by 10:30 AM or at target. Trail stop after +1.5%. "
                 f"Max hold: {MAX_HOLD_DAYS} days.\n")

    lines.append("### Key Filters Explained\n")
    lines.append("| Filter | Why It Matters |")
    lines.append("|--------|----------------|")
    lines.append("| Close position ≥ 0.80 | Stock closed in top 20% of range = strong demand into close |")
    lines.append("| Above VWAP | Institutional buyers were net-positive today = bullish bias |")
    lines.append("| Nifty not bearish | Broad market trend must support overnight longs |")
    lines.append("| Volume surge > 1.3× | Higher than normal volume confirms institutional interest |")
    lines.append("| Not overextended | Stock hasn't already moved too far — room for overnight continuation |")
    lines.append(f"| RR ≥ {MIN_RR_RATIO} | Minimum risk-reward ratio to ensure edge survives transaction costs |")
    lines.append("")

    # Today's stats
    n_total = len(signals)
    n_bullish = sum(1 for s in signals
                    if s.get("closing_strength", {}).get("close_position", 0) >= 0.8)
    n_above_vwap = sum(1 for s in signals
                       if s.get("closing_strength", {}).get("above_vwap", False))
    n_volume = sum(1 for s in signals
                   if s.get("closing_strength", {}).get("volume_surge_ratio", 0) >= 1.3)
    lines.append("### Today's Filter Stats\n")
    lines.append(f"- Stocks scanned: {n_total}")
    lines.append(f"- Closing near high (≥0.80): {n_bullish}/{n_total}")
    lines.append(f"- Above VWAP: {n_above_vwap}/{n_total}")
    lines.append(f"- Volume surge (≥1.3×): {n_volume}/{n_total}")
    lines.append(f"- Nifty regime: {nifty_regime.upper()} "
                 f"({'✅ allows BTST' if nifty_regime != 'bearish' else '❌ blocks all BTST'})")
    lines.append("")

    # ── 11. AI Advisory ──
    print("  Generating AI advisory...")
    market_context = {
        "nifty_regime": nifty_regime,
        "vix_val": vix_val,
        "vix_regime": vix_regime,
        "inst_flow": inst_flow,
        "dow_name": dow_name,
        "month_period": month_period,
        "market_news": news_data.get("_market", "") if news_data else "",
    }
    ai_text = None
    if active:
        ai_text = generate_btst_llm_explanation(active, market_context)

    if ai_text:
        lines.append("---\n")
        lines.append("## AI Advisory\n")
        lines.append(ai_text)
        lines.append("")

    report_content = "\n".join(lines) + "\n"
    with open(report_path, "w") as f:
        f.write(report_content)
    return report_path


def _collect_risks(signal, profile):
    """Collect risk factors for a signal."""
    risks = []
    regime = signal.get("symbol_regime", {})
    weekly = regime.get("weekly_trend", "sideways")
    trend = regime.get("trend", "sideways")

    ns_val = signal.get("news_sentiment", 0)
    if ns_val < -0.3:
        risks.append("Negative news sentiment opposes overnight long direction")
    if signal.get("has_material_event", False):
        risks.append("Material event detected — increased overnight gap risk")
    if trend in ("mild_down", "strong_down"):
        risks.append(f"Daily trend is {trend} — counter-trend overnight hold")
    if weekly == "down":
        risks.append("Weekly trend is down — fighting the bigger picture")
    beta = profile.get("beta", 1)
    if beta > 1.5:
        risks.append(f"High-beta stock ({beta:.1f}) — expect sharp overnight moves both ways")
    if regime.get("volatility") == "expanded":
        risks.append("Expanded volatility — wider stops needed, smaller size")
    conv_score = signal.get("convergence_score", 0)
    conv_conf = signal.get("convergence_conflicting", [])
    if conv_score < 50 and conv_conf:
        risks.append(f"Convergence weak ({conv_score}%) — {', '.join(conv_conf[:2])} conflicting")
    if signal.get("overnight_wr", 0) < 50 and signal.get("overnight_stats", {}).get("all", {}).get("n_samples", 0) >= 10:
        risks.append(f"Overnight WR below 50% ({signal['overnight_wr']:.0f}%) on sufficient samples")

    return risks


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BTST Scanner")
    parser.add_argument("--force", action="store_true",
                        help="Run anytime (override 2:30 PM time check)")
    args = parser.parse_args()

    now_ist = datetime.now(IST)
    t = now_ist.time()

    print(f"\n  BTST Scanner - {now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"  Tickers: {len(TICKERS)}")

    # Time check
    if not args.force and t < MIN_RUN_TIME:
        print(f"  Too early for BTST scan (before {MIN_RUN_TIME.strftime('%H:%M')}). Use --force to override.")
        return

    # Weekend check
    if now_ist.weekday() >= 5 and not args.force:
        print("  Market closed (weekend). Use --force to override.")
        return

    # Load config for LLM and capital
    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}
    g = config.get("global", {})
    capital = g.get("capital", 1000000)
    btst_capital = capital * MAX_BTST_CAPITAL_PCT / 100

    print(f"  Capital: {capital:,.0f} | BTST allocation: {btst_capital:,.0f} ({MAX_BTST_CAPITAL_PCT}%)")

    # Fetch VIX
    print("  Fetching India VIX...")
    vix_val, vix_regime = fetch_india_vix()
    vix_info = (vix_val, vix_regime)
    vix_scale = vix_position_scale(vix_val)
    if vix_val:
        print(f"  VIX: {vix_val} ({vix_regime}) | Scale: {vix_scale}x")
    else:
        print("  VIX: unavailable")

    # Fetch benchmark
    print("  Fetching benchmark data...")
    nifty_intra = fetch_yf(BENCHMARK, period="5d", interval="5m")
    nifty_ist = compute_vwap(_to_ist(nifty_intra)) if not nifty_intra.empty else pd.DataFrame()

    nifty_new_lows = nifty_making_new_lows(nifty_ist) if not nifty_ist.empty else True

    nifty_daily = fetch_yf(BENCHMARK, period="6mo", interval="1d")
    nifty_regime, beta_scale, _regime_strength = detect_nifty_regime(nifty_daily)

    nifty_state = {
        "regime": nifty_regime,
        "new_lows": nifty_new_lows,
        "beta_scale": beta_scale,
        "nifty_ist": nifty_ist,
    }
    print(f"  Nifty: {nifty_regime.upper()} | Making new lows: {nifty_new_lows}")

    # DOW + month period
    today_dow = now_ist.weekday()
    dow_name = DOW_NAMES.get(today_dow, "Weekend")
    month_period = classify_month_period(now_ist)
    print(f"  DOW: {dow_name} | Period: {month_period}")

    # Institutional flow
    print("  Estimating institutional flow...")
    inst_flow = estimate_institutional_flow()
    print(f"  Institutional flow: {inst_flow}")

    # Fetch sector indices
    print("  Fetching sector indices...")
    sectors = set(cfg["sector"] for cfg in TICKERS.values() if cfg.get("sector"))
    sector_data = {}
    for sec in sectors:
        sector_data[sec] = fetch_yf(sec, period="5d", interval="1d")

    # Fetch all ticker data
    all_data = {}
    symbols = list(TICKERS.keys())
    for sym in symbols:
        print(f"  Fetching {sym}...")
        all_data[sym] = {
            "intra": fetch_yf(sym, period="5d", interval="5m"),
            "daily": fetch_yf(sym, period="6mo", interval="1d"),
        }

    # Fetch news & sentiment
    print("  Fetching news & sentiment...")
    news_data = get_news_and_sentiment(symbols)

    # Evaluate each ticker
    print("  Evaluating BTST conditions...")
    ticker_states = []
    for sym in symbols:
        d = all_data.get(sym, {"intra": pd.DataFrame(), "daily": pd.DataFrame()})
        state = evaluate_btst(
            sym, d["intra"], d["daily"], nifty_state, vix_info, sector_data,
            nifty_daily=nifty_daily, news_sentiment=news_data,
        )
        ticker_states.append(state)

    # Rank signals
    ticker_states = rank_btst_signals(ticker_states)

    # ── Portfolio risk filters ──
    # Correlation clusters
    print("  Computing correlation clusters...")
    daily_data_dict = {sym: all_data[sym]["daily"] for sym in symbols if not all_data[sym]["daily"].empty}
    corr_clusters = compute_correlation_clusters(daily_data_dict)

    sym_to_cluster = {}
    for cid, syms in corr_clusters.items():
        for sym in syms:
            sym_to_cluster[sym] = cid

    # Apply cluster limit (max 2 from same cluster)
    cluster_counts = {}
    for s in ticker_states:
        if s["signal"] in ("STRONG_BUY", "BUY"):
            cid = sym_to_cluster.get(s["symbol"])
            if cid is not None:
                cluster_counts[cid] = cluster_counts.get(cid, 0) + 1
                if cluster_counts[cid] > 2:
                    s["signal"] = "WATCH"
                    cluster_syms = corr_clusters.get(cid, [])
                    s["action_text"] = f"Correlation limit — cluster ({', '.join(s2.replace('.NS','') for s2 in cluster_syms[:3])}) at max"

    # DOW avoid check
    for s in ticker_states:
        if s["signal"] in ("STRONG_BUY", "BUY"):
            stats = s["overnight_stats"]
            short_dow = ["Mon", "Tue", "Wed", "Thu", "Fri"][today_dow] if today_dow < 5 else "Weekend"
            dow_stats = stats.get(f"dow_{short_dow}", {})
            if dow_stats and dow_stats.get("win_rate", 100) < 40 and dow_stats.get("n_samples", 0) >= 5:
                s["signal"] = "WATCH"
                s["action_text"] = f"DOW avoid — {dow_name} overnight WR {dow_stats['win_rate']:.0f}%"

    # Limit to MAX_BTST_POSITIONS
    active_count = 0
    for s in ticker_states:
        if s["signal"] in ("STRONG_BUY", "BUY"):
            active_count += 1
            if active_count > MAX_BTST_POSITIONS:
                s["signal"] = "WATCH"
                s["action_text"] = f"Position limit — max {MAX_BTST_POSITIONS} BTST slots"

    # Position sizing for active signals (with individual beta scaling)
    for s in ticker_states:
        if s["signal"] in ("STRONG_BUY", "BUY"):
            wr = s["overnight_wr"] / 100 if s["overnight_wr"] > 0 else 0.5
            rr = s["target_pct"] / s["stop_pct"] if s["stop_pct"] > 0 else 1.5
            kelly = max(0, (wr * rr - (1 - wr)) / rr) * 0.5

            # Individual beta scaling: high-beta stocks get smaller positions
            daily_df = all_data.get(s["symbol"], {}).get("daily", pd.DataFrame())
            stock_beta = 1.0
            if not daily_df.empty and not nifty_daily.empty and len(daily_df) >= 20:
                try:
                    stock_ret = daily_df["Close"].tail(20).pct_change().dropna()
                    nifty_ret = nifty_daily["Close"].tail(20).pct_change().dropna()
                    if len(stock_ret) >= 15 and len(nifty_ret) >= 15:
                        aligned = pd.concat([stock_ret, nifty_ret], axis=1).dropna()
                        if len(aligned) >= 10:
                            cov = np.cov(aligned.iloc[:, 0], aligned.iloc[:, 1])
                            stock_beta = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else 1.0
                except Exception:
                    pass
            ind_beta_scale = compute_individual_beta_scale(stock_beta)

            pos_size = compute_position_size(
                capital=btst_capital,
                kelly_fraction=max(kelly, 0.05),
                entry_price=s["entry_price"],
                stop_pct=s["stop_pct"],
                vix_scale=vix_scale,
                beta_scale=beta_scale * ind_beta_scale,
            )
            s["recommended_qty"] = pos_size["quantity"]
            s["capital_allocated"] = pos_size["capital_allocated"]
            s["capital_at_risk"] = pos_size["capital_at_risk"]
            s["risk_pct"] = pos_size["risk_pct"]

    # Auto-log signals to Supabase
    try:
        from common.db import _insert
        import json as _json
        active_signals = [s for s in ticker_states if s["signal"] in ("STRONG_BUY", "BUY")]
        for s in active_signals:
            row = {
                "symbol": s["symbol"],
                "direction": "long",
                "phase": "BTST",
                "strategy": "btst",
                "edge_strength": 5 if s["signal"] == "STRONG_BUY" else 4,
                "vix_at_signal": vix_val,
                "nifty_regime": nifty_regime,
                "conditions_met": sum(s["conditions"].values()),
                "conditions_total": len(s["conditions"]),
                "weighted_score": s["weighted_score"],
                "entry_price": s["entry_price"],
                "target_price": s["target_price"],
                "stop_price": s["stop_price"],
                "recommended_qty": s.get("recommended_qty", 0),
                "capital_at_risk": s.get("capital_at_risk", 0),
                "status": "signal",
                "conditions": _json.dumps(s.get("conditions", {})),
                "scanner_type": "btst",
            }
            _insert("trades", row)
        if active_signals:
            print(f"  Logged {len(active_signals)} BTST signal(s) to Supabase")
    except Exception as e:
        print(f"  [WARN] Supabase logging failed: {e}")

    # Portfolio metrics
    portfolio_metrics = None
    try:
        from common.db import get_portfolio_metrics_supa
        portfolio_metrics = get_portfolio_metrics_supa(days=30, scanner_type="btst")
    except Exception:
        pass

    # Write report (includes AI advisory)
    report_path = write_btst_report(
        ticker_states, nifty_state, vix_info, inst_flow, dow_name,
        month_period, all_data, nifty_daily, news_data,
    )
    print(f"  Report saved: {report_path}")

    # Render slim dashboard
    dashboard = render_btst_dashboard(
        ticker_states, nifty_state, vix_info, inst_flow, dow_name,
        month_period, report_path, portfolio_metrics,
    )
    print()
    print(dashboard)


if __name__ == "__main__":
    main()
