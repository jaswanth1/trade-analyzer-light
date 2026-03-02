"""
Intraday Scanner — main orchestrator (institutional grade).

Evaluates all tickers across multiple intraday strategies, ranks signals,
applies portfolio risk overlays, renders dashboard, generates reports,
and calls LLM for advisory.

Time-aware: auto-detects market phase and adapts behavior:
  PRE_MARKET  (before 9:00)  — conditional IF-THEN gap-scenario setups
  PRE_LIVE    (9:00-9:15)    — pre-market auction data → refine scenarios
  LIVE        (9:15-15:15)   — full scanner with time-relevance per strategy
  POST_MARKET (after 15:15)  — session review + tomorrow's watchlist

Bug fixes over v1:
- above_vwap gate is direction-aware (longs above, shorts below)
- Daily drawdown limit enforced (skip new signals if >= 2% loss today)
- VIX fetch failure defaults to conservative 0.7x scale (not 1.0)
- Minimum RR gate (1.2) applied before scoring

New:
- Supabase persistence
- Portfolio heat tracking
- Net direction cap (max 4 same direction)
- Per-stock beta in position sizing
- Mid-session day-type re-classification
- Time-aware phase detection (no more --force for off-hours)
- Educational explanations per setup

Usage:
    python -m intraday.scanner            # auto-detects phase and runs
    python -m intraday.scanner --force    # force LIVE mode anytime (testing)
    python -m intraday.scanner --manage   # position management only
"""

import argparse
import os
import warnings
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
import yfinance as yf
from zoneinfo import ZoneInfo

from common.data import (
    fetch_yf, TICKERS, BENCHMARK, PROJECT_ROOT, CONFIG_PATH,
)
from common.indicators import (
    compute_atr, compute_beta, compute_vwap, _to_ist, classify_gaps,
)
from common.market import (
    fetch_india_vix, vix_position_scale, detect_nifty_regime,
    check_earnings_proximity, nifty_making_new_lows,
    estimate_institutional_flow,
)
from common.risk import (
    compute_position_size, compute_correlation_clusters,
    compute_portfolio_heat, compute_individual_beta_scale,
    NSE_ROUND_TRIP_COST_PCT, MAX_SAME_DIRECTION,
)
from common.news import get_news_and_sentiment
from intraday.convergence import compute_convergence_score, compute_historical_hit_rate
from common.display import fmt, box_top, box_mid, box_bot, box_line, W

from intraday.features import (
    compute_ema, compute_rsi, compute_bollinger, compute_keltner,
    compute_squeeze, compute_opening_range, compute_intraday_levels,
    compute_volume_ratio, compute_cumulative_return_from_open,
    compute_vwap_bands, compute_cumulative_rvol, compute_candle_imbalance,
)
from intraday.regime import (
    classify_day_type, reclassify_day_type, classify_symbol_regime,
    classify_month_period, compute_dow_month_stats,
    get_eligible_strategies, DOW_NAMES,
)
from intraday.strategies import (
    evaluate_orb, evaluate_pullback, evaluate_compression,
    evaluate_mean_revert, evaluate_swing, evaluate_mlr,
)
from intraday.explanations import (
    generate_setup_explanation, generate_scenario_explanation,
    generate_llm_explanation, _compute_stock_profile, _format_rupee,
    _action_label, STRATEGY_DESCRIPTIONS,
)

warnings.filterwarnings("ignore")

IST = ZoneInfo("Asia/Kolkata")

# ── Constants ─────────────────────────────────────────────────────────────

MAX_INTRADAY_POSITIONS = 5
MAX_INTRADAY_CAPITAL_PCT = 50.0
MAX_SECTOR_EXPOSURE = 2
MAX_DAILY_DRAWDOWN_PCT = 2.0
MIN_RR_RATIO = 1.2  # minimum RR gate — discard below this
LONG_ONLY = True  # equity cash segment — BUY only, no short selling
ENTRY_WINDOW = (dtime(9, 15), dtime(14, 30))
EXIT_DEADLINE = dtime(15, 0)
LUNCH_WINDOW = (dtime(12, 0), dtime(13, 0))
INTRADAY_REPORT_DIR = PROJECT_ROOT / "intraday" / "reports"

# Phase 2 hook: ML model path (not used yet)
ML_MODEL_PATH = PROJECT_ROOT / "models" / "intraday_scorer.joblib"

SIGNAL_TIERS = {
    "STRONG": "score >= 0.80, RR >= 2.0, regime aligned, DOW+month favorable",
    "ACTIVE": "score >= 0.65, RR >= 1.5, regime compatible",
    "WATCH":  "score 0.50-0.65 or one gate failed",
    "AVOID":  "VIX stress, earnings, illiquid, regime mismatch",
}

STRATEGY_DAILY_LOSS_BUDGET = {
    "orb": 0.5,         # max 0.5% of capital lost on ORB today
    "pullback": 0.5,
    "compression": 0.3,
    "mean_revert": 0.3,
    "swing": 0.5,
    "mlr": 0.5,
}

INTRADAY_CONDITION_WEIGHTS = {
    "vwap_gate": 2.5,
    "nifty_ok": 2.5,
    "not_illiquid": 2.0,
    "rr_ratio": 2.0,
    "strategy_confidence": 3.0,
    "volume_ok": 1.5,
    "regime_aligned": 1.5,
    "dow_favorable": 1.0,
    "month_favorable": 1.0,
}

MUST_HAVE_GATES = ["vwap_gate", "nifty_ok", "not_illiquid"]

# ── Strategy Time Windows (LIVE mode) ────────────────────────────────────

STRATEGY_TIME_WINDOWS = {
    "orb":         (dtime(9, 15), dtime(12, 0)),
    "pullback":    (dtime(9, 30), dtime(14, 30)),
    "compression": (dtime(10, 0), dtime(14, 0)),
    "mean_revert": (dtime(10, 0), dtime(14, 30)),
    "swing":       (dtime(9, 15), dtime(15, 0)),
    "mlr":         (dtime(9, 30), dtime(11, 30)),
}

STRATEGY_FN_MAP = {
    "orb": evaluate_orb,
    "pullback": evaluate_pullback,
    "compression": evaluate_compression,
    "mean_revert": evaluate_mean_revert,
    "swing": evaluate_swing,
    "mlr": evaluate_mlr,
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

INTRADAY_AI_SYSTEM_PROMPT = """You are a professional intraday trading advisor for Indian equity markets (NSE).
You receive structured data about stocks being evaluated across multiple strategies:
ORB (opening range breakout), pullback, compression squeeze, mean-reversion, swing, and MLR (morning low recovery).

Your job:
1. RANK the STRONG and ACTIVE signals by conviction (max 5 trades)
2. For each, explain WHY the setup is valid given market regime and day-type
3. Flag conflicts: correlated positions, overexposure to one direction/sector
4. Comment on DOW/month-period seasonality impact
5. Give specific entry/target/stop levels and which strategies to prioritize

6. Consider news sentiment — if negative news conflicts with a long signal, flag it
7. Weight convergence score — prefer signals with 5+ indicators aligned
8. Reference historical hit rates when available
9. If institutional flow is "net_selling", be more cautious on longs

Be concise. Bullet points. No disclaimers. Assume experienced trader.
Respond in 250-400 words max."""


# ── Phase Detection ──────────────────────────────────────────────────────

def detect_market_phase(now_ist=None):
    """Auto-detect market phase based on IST clock.

    Returns: "pre_market" | "pre_live" | "live" | "post_market"
    """
    if now_ist is None:
        now_ist = datetime.now(IST)
    t = now_ist.time()
    if t < dtime(9, 0):
        return "pre_market"
    if t < dtime(9, 15):
        return "pre_live"
    if t <= dtime(15, 15):
        return "live"
    return "post_market"


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


# ── Pre-Market Scan (before 9:00) ───────────────────────────────────────

def _build_gap_scenarios(symbol, daily_df, nifty_daily, dow_month_stats,
                         symbol_regime, news_data=None):
    """Build conditional gap-scenario setups for a symbol.

    Returns list of scenario dicts (gap_up, gap_down, flat).
    """
    if daily_df.empty or len(daily_df) < 20:
        return []

    cfg = TICKERS.get(symbol, {"name": symbol, "sector": ""})
    prev_close = float(daily_df["Close"].iloc[-1])
    if prev_close <= 0:
        return []

    atr_raw = compute_atr(daily_df)
    atr_val = float(atr_raw) if atr_raw is not None and not np.isnan(atr_raw) else prev_close * 0.02
    atr_pct = atr_val / prev_close * 100

    trend = symbol_regime.get("trend", "sideways")
    momentum = symbol_regime.get("momentum", "steady")

    # Historical gap stats from classify_gaps
    try:
        gap_df = classify_gaps(daily_df)
    except Exception:
        gap_df = pd.DataFrame()

    scenarios = []

    # Gap-up scenario
    gap_up_entry = round(prev_close * 1.005 + atr_val * 0.1, 2)  # OR high estimate
    gap_up_target = round(gap_up_entry + atr_val * 0.8, 2)
    gap_up_stop = round(prev_close * 1.002, 2)  # just above prev close

    # Historical hit rate for gap-up days
    gap_up_prob = 50.0
    gap_up_hist = ""
    if not gap_df.empty:
        gap_ups = gap_df[gap_df["gap_type"].isin(["small_up", "large_up"])]
        if len(gap_ups) >= 5:
            continuation = (gap_ups["open_to_close_pct"] > 0).sum()
            gap_up_prob = round(continuation / len(gap_ups) * 100, 0)
            gap_up_hist = f"{gap_up_prob:.0f}% of {len(gap_ups)} gap-up days saw continuation"

    # Adjust probability by regime alignment
    if trend in ("strong_up", "mild_up") and momentum == "accelerating":
        gap_up_prob = min(90, gap_up_prob + 10)
    elif trend in ("strong_down", "mild_down"):
        gap_up_prob = max(10, gap_up_prob - 15)

    rr_up = round((gap_up_target - gap_up_entry) / max(gap_up_entry - gap_up_stop, 0.01), 1)
    scenarios.append({
        "type": "gap_up",
        "gap_threshold": 0.5,
        "strategy": "orb",
        "direction": "long",
        "entry": gap_up_entry,
        "target": gap_up_target,
        "stop": gap_up_stop,
        "probability": gap_up_prob,
        "rr": rr_up,
        "historical_context": gap_up_hist,
        "conditions_to_watch": ["RVOL > 1.2 at 9:30", "price above VWAP",
                                 "no immediate reversal in first 5 min"],
    })

    # Gap-down scenario
    gap_dn_entry = round(prev_close * 0.995 - atr_val * 0.1, 2)
    gap_dn_target = round(gap_dn_entry - atr_val * 0.8, 2)
    gap_dn_stop = round(prev_close * 0.998, 2)

    gap_dn_prob = 50.0
    gap_dn_hist = ""
    if not gap_df.empty:
        gap_dns = gap_df[gap_df["gap_type"].isin(["small_down", "large_down"])]
        if len(gap_dns) >= 5:
            continuation = (gap_dns["open_to_close_pct"] < 0).sum()
            gap_dn_prob = round(continuation / len(gap_dns) * 100, 0)
            gap_dn_hist = f"{gap_dn_prob:.0f}% of {len(gap_dns)} gap-down days saw continuation"

    if trend in ("strong_down", "mild_down") and momentum == "decelerating":
        gap_dn_prob = min(90, gap_dn_prob + 10)
    elif trend in ("strong_up", "mild_up"):
        gap_dn_prob = max(10, gap_dn_prob - 15)

    rr_dn = round((gap_dn_entry - gap_dn_target) / max(gap_dn_stop - gap_dn_entry, 0.01), 1)
    scenarios.append({
        "type": "gap_down",
        "gap_threshold": 0.5,
        "strategy": "orb",
        "direction": "short",
        "entry": gap_dn_entry,
        "target": gap_dn_target,
        "stop": gap_dn_stop,
        "probability": gap_dn_prob,
        "rr": rr_dn,
        "historical_context": gap_dn_hist,
        "conditions_to_watch": ["RVOL > 1.2 at 9:30", "price below VWAP",
                                 "selling pressure sustained after first 5 min"],
    })

    # Flat-open scenario — pullback or compression strategy
    flat_strategy = "pullback" if trend in ("mild_up", "strong_up", "mild_down", "strong_down") else "compression"
    flat_dir = "long" if trend in ("mild_up", "strong_up") else "short" if trend in ("mild_down", "strong_down") else "long"
    flat_entry = round(prev_close, 2)
    flat_target = round(flat_entry + (atr_val * 0.5 if flat_dir == "long" else -atr_val * 0.5), 2)
    flat_stop = round(flat_entry + (-atr_val * 0.3 if flat_dir == "long" else atr_val * 0.3), 2)

    flat_prob = 45.0
    flat_hist = ""
    if not gap_df.empty:
        flats = gap_df[gap_df["gap_type"] == "flat"]
        if len(flats) >= 5:
            wins = (flats["open_to_close_pct"] > 0).sum() if flat_dir == "long" else (flats["open_to_close_pct"] < 0).sum()
            flat_prob = round(wins / len(flats) * 100, 0)
            flat_hist = f"{flat_prob:.0f}% of {len(flats)} flat-open days moved {flat_dir}"

    rr_flat = round(abs(flat_target - flat_entry) / max(abs(flat_stop - flat_entry), 0.01), 1)
    scenarios.append({
        "type": "flat",
        "gap_threshold": 0.3,
        "strategy": flat_strategy,
        "direction": flat_dir,
        "entry": flat_entry,
        "target": flat_target,
        "stop": flat_stop,
        "probability": flat_prob,
        "rr": rr_flat,
        "historical_context": flat_hist,
        "conditions_to_watch": ["Wait for 30-min range to form", "RVOL > 1.0",
                                 f"{'higher lows' if flat_dir == 'long' else 'lower highs'} in first 30 min"],
    })

    # Filter out short setups in long-only mode
    if LONG_ONLY:
        scenarios = [s for s in scenarios if s["direction"] == "long"]

    return scenarios


def run_pre_market_scan(config, symbols, now_ist=None, data_override=None,
                        skip_llm=False):
    """Pre-market scan (before 9:00): conditional IF-THEN gap scenarios.

    Uses daily data only. Returns list of setup dicts ranked by probability.

    Args:
        data_override: dict {symbol: {"daily": df}, "_nifty": {"daily": df}}
        skip_llm: if True, skip LLM calls and educational explanations
    """
    now_ist = now_ist or datetime.now(IST)
    capital = config.get("global", {}).get("capital", 1000000)

    if not data_override:
        print("  [PRE-MARKET] Generating conditional gap-scenario setups...")
        print(f"  Time: {now_ist.strftime('%H:%M')} IST — market opens at 9:15")

    # Fetch VIX + Nifty regime
    if data_override:
        vix_val, vix_regime = data_override.get("_vix", (None, "normal"))
        vix_info = (vix_val, vix_regime)
        nifty_daily = data_override.get("_nifty", {}).get("daily", pd.DataFrame())
        inst_flow = data_override.get("_inst_flow", "neutral")
        news_data = data_override.get("_news", {})
    else:
        print("  Fetching India VIX...")
        vix_val, vix_regime = fetch_india_vix()
        vix_info = (vix_val, vix_regime)
        if vix_val:
            print(f"  VIX: {vix_val} ({vix_regime})")
        else:
            print("  VIX: unavailable")

        print("  Fetching Nifty daily data...")
        nifty_daily = fetch_yf(BENCHMARK, period="6mo", interval="1d")

        # Institutional flow estimate
        inst_flow = estimate_institutional_flow()
        print(f"  Institutional flow (yesterday): {inst_flow}")

        # News
        print("  Fetching overnight news & sentiment...")
        try:
            news_data = get_news_and_sentiment(symbols)
        except Exception:
            news_data = {}

    nifty_regime, beta_scale = detect_nifty_regime(nifty_daily)
    if not data_override:
        print(f"  Nifty regime: {nifty_regime.upper()}")

    # DOW / month period
    dow = now_ist.weekday()
    dow_name = DOW_NAMES.get(dow, "Unknown")
    month_period = classify_month_period(now_ist)
    if not data_override:
        print(f"  DOW: {dow_name} | Period: {month_period}")

    # Fetch daily data and build scenarios for each ticker
    all_setups = []
    for sym in symbols:
        if data_override:
            daily_df = data_override.get(sym, {}).get("daily", pd.DataFrame())
        else:
            print(f"  Fetching {sym}...")
            daily_df = fetch_yf(sym, period="6mo", interval="1d")
        if daily_df.empty:
            continue

        symbol_regime = classify_symbol_regime(daily_df, pd.DataFrame(), nifty_daily=nifty_daily)
        dow_month_stats = compute_dow_month_stats(daily_df)

        scenarios = _build_gap_scenarios(
            sym, daily_df, nifty_daily, dow_month_stats,
            symbol_regime, news_data=news_data,
        )

        if not scenarios:
            continue

        cfg = TICKERS.get(sym, {"name": sym, "sector": ""})
        profile = _compute_stock_profile(
            {"entry_price": float(daily_df["Close"].iloc[-1]),
             "stop_pct": 1.0, "target_pct": 1.5},
            daily_df, nifty_daily,
        )

        # Convergence from daily data only (5/7 dimensions — no VWAP or candle imbalance)
        conv_score = 0
        conv_aligned = []
        close = daily_df["Close"]
        from intraday.features import compute_ema, compute_rsi, compute_macd

        rsi = compute_rsi(close, 14)
        if not rsi.empty and not np.isnan(rsi.iloc[-1]):
            rsi_val = float(rsi.iloc[-1])
            if 40 <= rsi_val <= 70:
                conv_aligned.append("RSI")

        macd = compute_macd(close)
        if len(macd["histogram"]) >= 2:
            h = macd["histogram"]
            if not h.iloc[-2:].isna().any():
                if float(h.iloc[-1]) > float(h.iloc[-2]):
                    conv_aligned.append("MACD")

        if len(close) >= 50:
            ema9 = float(compute_ema(close, 9).iloc[-1])
            ema20 = float(compute_ema(close, 20).iloc[-1])
            ema50 = float(compute_ema(close, 50).iloc[-1])
            if ema9 > ema20 > ema50:
                conv_aligned.append("EMA_align")

        rs = symbol_regime.get("relative_strength", "inline")
        if rs == "outperforming":
            conv_aligned.append("rel_strength")

        vol_regime = symbol_regime.get("volatility", "normal")
        if vol_regime != "expanded":
            conv_aligned.append("vol_ok")

        conv_score = round(len(conv_aligned) / 5 * 100) if conv_aligned else 0

        # Best scenario by probability
        best_scenario = max(scenarios, key=lambda s: s["probability"])

        # News sentiment
        sym_news = news_data.get(sym, {})

        setup = {
            "symbol": sym,
            "name": cfg.get("name", sym),
            "sector": cfg.get("sector", ""),
            "prev_close": float(daily_df["Close"].iloc[-1]),
            "symbol_regime": symbol_regime,
            "gap_scenarios": scenarios,
            "best_scenario": best_scenario,
            "convergence_score": conv_score,
            "convergence_detail": f"{len(conv_aligned)}/5 ({', '.join(conv_aligned)})",
            "dow_name": dow_name,
            "dow_wr": dow_month_stats.get(dow_name, {}).get("all", {}).get("win_rate", 50),
            "month_period": month_period,
            "news_sentiment": sym_news.get("sentiment", 0),
            "news_summary": sym_news.get("summary", ""),
            "profile": profile,
            # Fields for compatibility with generate_setup_explanation
            "strategy": best_scenario["strategy"],
            "direction": best_scenario["direction"],
            "entry_price": best_scenario["entry"],
            "target_price": best_scenario["target"],
            "stop_price": best_scenario["stop"],
            "stop_pct": round(abs(best_scenario["stop"] - best_scenario["entry"]) / best_scenario["entry"] * 100, 2) if best_scenario["entry"] > 0 else 1.0,
            "target_pct": round(abs(best_scenario["target"] - best_scenario["entry"]) / best_scenario["entry"] * 100, 2) if best_scenario["entry"] > 0 else 1.0,
            "rr_ratio": best_scenario["rr"],
            "score": best_scenario["probability"] / 100,
            "confidence": best_scenario["probability"] / 100,
            "signal": "STRONG" if best_scenario["probability"] >= 65 and best_scenario["rr"] >= 2.0
                      else "ACTIVE" if best_scenario["probability"] >= 50 and best_scenario["rr"] >= 1.5
                      else "WATCH",
        }
        all_setups.append(setup)

    # Rank by best scenario probability
    all_setups.sort(key=lambda s: -s["best_scenario"]["probability"])

    # Render pre-market output (skip during backtest)
    if not skip_llm:
        _render_pre_market_output(all_setups, nifty_regime, vix_info, inst_flow,
                                  dow_name, month_period, news_data, nifty_daily)

    return all_setups


def _render_pre_market_output(setups, nifty_regime, vix_info, inst_flow,
                               dow_name, month_period, news_data, nifty_daily):
    """Render pre-market conditional setups to terminal and markdown report."""
    now_ist = datetime.now(IST)
    vix_val, vix_regime = vix_info

    lines = []
    lines.append(box_top())
    lines.append(box_line(f"PRE-MARKET SCANNER — {now_ist.strftime('%Y-%m-%d %H:%M')} IST"))
    lines.append(box_line(f"Nifty: {nifty_regime.upper()} | VIX: {vix_val or 'N/A'} ({vix_regime}) | Flow: {inst_flow}"))
    lines.append(box_line(f"DOW: {dow_name} | Period: {month_period}"))
    lines.append(box_line(f"Mode: CONDITIONAL — setups activate at market open"))
    lines.append(box_mid())

    # Market news
    market_ctx = (news_data or {}).get("_market", "")
    if market_ctx:
        lines.append(box_line("OVERNIGHT CONTEXT"))
        for ml in market_ctx.split("\n")[:5]:
            lines.append(box_line(f"  {ml}"))
        lines.append(box_line())
        lines.append(box_mid())

    actionable = [s for s in setups if s.get("signal") in ("STRONG", "ACTIVE")]
    watchlist = [s for s in setups if s.get("signal") == "WATCH"]

    if not actionable and not watchlist:
        lines.append(box_line("No qualifying setups for today."))
        lines.append(box_line("Check back at 9:00 for pre-live data or 9:15 for live scan."))
        lines.append(box_bot())
        print("\n".join(lines))
        return

    # Actionable setups with IF-THEN format
    if actionable:
        lines.append(box_line(f"CONDITIONAL SETUPS ({len(actionable)} stocks)"))
        lines.append(box_line())

        for setup in actionable[:10]:
            sym = setup["symbol"].replace(".NS", "")
            name = setup["name"]
            regime = setup["symbol_regime"]
            lines.append(box_line(f"  {sym} ({name}) — {regime.get('trend', 'N/A')} trend, "
                                  f"{regime.get('momentum', 'N/A')} momentum"))

            for scenario in setup["gap_scenarios"]:
                prob = scenario["probability"]
                rr = scenario["rr"]
                marker = "*" if scenario == setup["best_scenario"] else " "

                if scenario["type"] == "gap_up":
                    label = f"Gap-up (>{scenario['gap_threshold']}%)"
                elif scenario["type"] == "gap_down":
                    label = f"Gap-down (>{scenario['gap_threshold']}%)"
                else:
                    label = "Flat (±0.3%)"

                lines.append(box_line(
                    f"  {marker} IF {label}: {scenario['strategy'].upper()} "
                    f"{_action_label(scenario['direction'])} | Prob: {prob:.0f}% | RR: {rr:.1f}:1"
                ))

            # Convergence
            lines.append(box_line(f"    Convergence: {setup['convergence_score']}% — {setup['convergence_detail']}"))

            # News
            if setup.get("news_summary"):
                lines.append(box_line(f"    News: {setup['news_summary']}"))
            lines.append(box_line())

    # Watch list
    if watchlist:
        lines.append(box_line(f"WATCHLIST ({len(watchlist)} stocks)"))
        for s in watchlist[:5]:
            sym = s["symbol"].replace(".NS", "")
            best = s["best_scenario"]
            lines.append(box_line(f"  {sym}: best={best['type']} {best['strategy']} "
                                  f"(prob: {best['probability']:.0f}%, RR: {best['rr']:.1f})"))
        lines.append(box_line())

    lines.append(box_bot())
    print("\n".join(lines))

    # Write markdown report (with educational content)
    _write_pre_market_report(setups, nifty_regime, vix_info, inst_flow,
                              dow_name, month_period, news_data, nifty_daily)


def _write_pre_market_report(setups, nifty_regime, vix_info, inst_flow,
                              dow_name, month_period, news_data, nifty_daily=None):
    """Write pre-market report as markdown with full educational content."""
    INTRADAY_REPORT_DIR.mkdir(exist_ok=True)
    now = datetime.now(IST)
    path = INTRADAY_REPORT_DIR / f"pre_market_{now.strftime('%Y-%m-%d_%H%M')}.md"

    vix_val, vix_regime = vix_info
    lines = []
    lines.append(f"# Pre-Market Scanner — {now.strftime('%Y-%m-%d %H:%M')} IST\n")
    lines.append(f"**Nifty**: {nifty_regime.upper()} | **VIX**: {vix_val or 'N/A'} ({vix_regime}) | "
                 f"**Inst Flow**: {inst_flow}")
    lines.append(f"**DOW**: {dow_name} | **Period**: {month_period}\n")

    # How to Read
    lines.append("## How to Read This Report\n")
    lines.append("- **BUY** = Buy shares first, sell later for profit (price expected to go UP)")
    lines.append("- **SELL** = Sell shares first, buy back later for profit (price expected to go DOWN)")
    lines.append("- **Gap-up/Gap-down/Flat** = How the stock opens relative to yesterday's close")
    lines.append("- **Prob** = Historical probability this scenario plays out")
    lines.append("- **RR** = Risk-Reward ratio (e.g., 3.0 means you gain ₹3 for every ₹1 risked)")
    lines.append("- **Convergence** = How many technical indicators agree on the direction")
    lines.append("- These are **CONDITIONAL** setups — wait for market open to see which gap scenario plays out\n")

    # Overnight context
    market_ctx = (news_data or {}).get("_market", "")
    if market_ctx:
        lines.append(f"## Overnight Context\n\n{market_ctx}\n")

    actionable = [s for s in setups if s.get("signal") in ("STRONG", "ACTIVE")]

    # Recommended Trades summary
    if actionable:
        strong = [s for s in actionable if s.get("signal") == "STRONG"]
        active = [s for s in actionable if s.get("signal") == "ACTIVE"]

        lines.append("## Recommended Trades\n")
        lines.append("Ranked by probability and conviction. Execute in order of priority — "
                     "**wait for market open** to confirm which gap scenario plays out.\n")

        lines.append("| # | Symbol | IF opens | Action | Entry | Target | Stop | Prob | RR | Risk/₹1L | Signal |")
        lines.append("|---|--------|----------|--------|-------|--------|------|------|----|----------|--------|")

        rank = 0
        for setup in strong + active:
            rank += 1
            sym = setup["symbol"].replace(".NS", "")
            best = setup["best_scenario"]
            direction_label = _action_label(best["direction"])
            gap_label = best["type"].replace("_", " ")
            entry = best.get("entry", 0)
            stop = best.get("stop", 0)
            risk_per_lakh = ""
            if entry > 0:
                shares = int(100_000 / entry)
                risk_per_lakh = f"₹{abs(entry - stop) * shares:,.0f}"
            lines.append(
                f"| {rank} | **{sym}** | {gap_label} | {best['strategy'].upper()} {direction_label} | "
                f"₹{entry:,.0f} | ₹{best.get('target', 0):,.0f} | ₹{stop:,.0f} | "
                f"{best['probability']:.0f}% | {best['rr']:.1f} | {risk_per_lakh} | "
                f"{setup.get('signal', '')} |"
            )
        lines.append("")

        # Quick action plan
        top3 = (strong + active)[:3]
        if top3:
            lines.append("### Quick Action Plan\n")
            for i, setup in enumerate(top3, 1):
                sym = setup["symbol"].replace(".NS", "")
                best = setup["best_scenario"]
                direction_label = _action_label(best["direction"])
                gap_label = best["type"].replace("_", " ")
                watch_items = best.get("conditions_to_watch", [])
                watch_str = f" Confirm: {watch_items[0]}" if watch_items else ""
                lines.append(f"{i}. **{sym}** — IF {gap_label} → {best['strategy'].upper()} "
                             f"{direction_label} @ ₹{best['entry']:,.0f} | "
                             f"Stop ₹{best['stop']:,.0f} | Target ₹{best['target']:,.0f}.{watch_str}")
            lines.append("")
            lines.append(f"> **Max positions**: Pick top 2-3. Don't overload — "
                         f"today is {dow_name}, {month_period}.\n")

        lines.append("---\n")

    if actionable:
        lines.append("## Detailed Setups\n")
        for setup in actionable:
            sym = setup["symbol"].replace(".NS", "")
            name = setup["name"]
            regime = setup["symbol_regime"]
            best = setup["best_scenario"]

            lines.append(f"### {sym} — {name}\n")
            lines.append(f"**Signal**: {setup.get('signal', 'ACTIVE')} | "
                         f"**Best scenario**: {best['type']} → {best['strategy'].upper()} "
                         f"{_action_label(best['direction'])} (Prob: {best['probability']:.0f}%, "
                         f"RR: {best['rr']:.1f}:1)\n")

            # Strategy explanation
            strat = best.get("strategy", "")
            strat_desc = STRATEGY_DESCRIPTIONS.get(strat, "")
            if strat_desc:
                lines.append(f"**Strategy**: {strat.upper()} — {strat_desc}\n")

            # Stock context
            trend = regime.get("trend", "N/A")
            vol = regime.get("volatility", "N/A")
            momentum = regime.get("momentum", "N/A")
            weekly = regime.get("weekly_trend", "N/A")
            lines.append(f"**Context**: {trend} trend, {vol} volatility, {momentum} momentum, weekly: {weekly}")
            lines.append(f"- **Convergence**: {setup['convergence_score']}% — {setup['convergence_detail']}")
            if setup.get("news_summary"):
                lines.append(f"- **News**: {setup['news_summary']}")
            lines.append("")

            # Risk per ₹1L on best scenario
            entry = best.get("entry", 0)
            stop = best.get("stop", 0)
            target = best.get("target", 0)
            if entry > 0:
                shares = int(100_000 / entry)
                risk_amt = abs(entry - stop) * shares
                reward_amt = abs(target - entry) * shares
                lines.append(f"**Per ₹1L capital** (best scenario): ~{shares} shares | "
                             f"Risk: ₹{risk_amt:,.0f} | Reward: ₹{reward_amt:,.0f}\n")

            # Gap scenarios as IF-THEN
            lines.append("**Scenarios**:\n")
            for sc in setup["gap_scenarios"]:
                direction_label = _action_label(sc["direction"])
                if sc["type"] == "gap_up":
                    label = f"opens gap-up (>{sc['gap_threshold']}% above prev close)"
                elif sc["type"] == "gap_down":
                    label = f"opens gap-down (>{sc['gap_threshold']}% below prev close)"
                else:
                    label = "opens flat (within ±0.3% of prev close)"

                is_best = " **← BEST**" if sc == best else ""
                lines.append(f"**IF** {sym} {label}:{is_best}")
                lines.append(f"- → {sc['strategy'].upper()} {direction_label} | "
                             f"Entry: ₹{sc['entry']:,.2f} | Target: ₹{sc['target']:,.2f} | "
                             f"Stop: ₹{sc['stop']:,.2f}")
                lines.append(f"- → Probability: {sc['probability']:.0f}% | RR: {sc['rr']:.1f}:1")

                hist = sc.get("historical_context", "")
                if hist:
                    lines.append(f"- → History: {hist}")

                watch_items = sc.get("conditions_to_watch", [])
                if watch_items:
                    lines.append(f"- → Watch for: {', '.join(watch_items)}")
                lines.append("")

            # Risks
            risks = []
            direction = best.get("direction", "long")
            if direction == "long" and weekly in ("mild_down", "strong_down"):
                risks.append("Weekly trend is down — fighting the bigger picture")
            elif direction == "short" and weekly in ("mild_up", "strong_up"):
                risks.append("Weekly trend is up — shorting into strength")
            if regime.get("volatility") == "expanded":
                risks.append("Expanded volatility — wider stops needed, smaller size")
            if risks:
                lines.append("**Risks**:")
                for r in risks:
                    lines.append(f"- {r}")
                lines.append("")

            # Verdict
            signal = setup.get("signal", "WATCH")
            if signal == "STRONG":
                lines.append("**Verdict**: HIGH CONVICTION — multiple factors align. Full position size.\n")
            elif signal == "ACTIVE":
                lines.append("**Verdict**: GOOD SETUP — edge is present but not overwhelming. Normal position size.\n")

            lines.append("---\n")

        # LLM advisory in report
        market_context = {
            "nifty_regime": nifty_regime,
            "vix_val": vix_val,
            "vix_regime": vix_regime,
            "inst_flow": inst_flow,
            "market_news": (news_data or {}).get("_market", ""),
        }
        llm_text = generate_llm_explanation(actionable[:3], "pre_market", market_context)
        if llm_text:
            lines.append("## AI Advisory\n")
            lines.append(llm_text)
            lines.append("")
    else:
        lines.append("## No qualifying setups today.\n")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Report saved: {path}")


# ── Pre-Live Scan (9:00-9:15) ───────────────────────────────────────────

def run_pre_live_scan(config, symbols):
    """Pre-live scan (9:00-9:15): refine scenarios with pre-market auction data.

    Fetches pre-market data (yfinance period=1d with prepost=True),
    determines which gap scenario is playing out, and re-ranks setups.
    """
    now_ist = datetime.now(IST)
    capital = config.get("global", {}).get("capital", 1000000)

    print("  [PRE-LIVE] Pre-market session active (9:00-9:15)...")
    print(f"  Time: {now_ist.strftime('%H:%M')} IST — institutional auction in progress")

    # Fetch VIX + Nifty
    vix_val, vix_regime = fetch_india_vix()
    vix_info = (vix_val, vix_regime)
    nifty_daily = fetch_yf(BENCHMARK, period="6mo", interval="1d")
    nifty_regime, _ = detect_nifty_regime(nifty_daily)
    inst_flow = estimate_institutional_flow()

    # News
    try:
        news_data = get_news_and_sentiment(symbols)
    except Exception:
        news_data = {}

    dow = now_ist.weekday()
    dow_name = DOW_NAMES.get(dow, "Unknown")
    month_period = classify_month_period(now_ist)

    print(f"  Nifty: {nifty_regime.upper()} | VIX: {vix_val or 'N/A'} | Flow: {inst_flow}")

    all_setups = []
    high_premarket_vol = []

    for sym in symbols:
        print(f"  Fetching pre-market data for {sym}...")
        cfg = TICKERS.get(sym, {"name": sym, "sector": ""})

        # Fetch with prepost=True to get pre-market session data
        try:
            ticker = yf.Ticker(sym.replace(".NS", "") + ".NS" if not sym.endswith(".NS") else sym)
            pm_df = ticker.history(period="1d", interval="1m", prepost=True)
        except Exception:
            pm_df = pd.DataFrame()

        daily_df = fetch_yf(sym, period="6mo", interval="1d")
        if daily_df.empty:
            continue

        prev_close = float(daily_df["Close"].iloc[-1])
        if prev_close <= 0:
            continue

        # Determine indicated open from pre-market data
        indicated_open = prev_close
        pre_vol = 0
        avg_daily_vol = float(daily_df["Volume"].tail(20).mean()) if "Volume" in daily_df.columns else 1

        if not pm_df.empty and len(pm_df) > 0:
            indicated_open = float(pm_df["Close"].iloc[-1])
            pre_vol = float(pm_df["Volume"].sum())

            # Flag high pre-market volume (institutional interest)
            # If pre-market vol > 5% of average daily volume, that's significant
            if avg_daily_vol > 0 and pre_vol / avg_daily_vol > 0.05:
                high_premarket_vol.append({
                    "symbol": sym,
                    "name": cfg.get("name", sym),
                    "pre_vol_pct": round(pre_vol / avg_daily_vol * 100, 1),
                    "indicated_open": indicated_open,
                    "gap_pct": round((indicated_open - prev_close) / prev_close * 100, 2),
                })

        # Determine actual gap scenario
        gap_pct = (indicated_open - prev_close) / prev_close * 100
        if gap_pct > 0.3:
            actual_scenario = "gap_up"
        elif gap_pct < -0.3:
            actual_scenario = "gap_down"
        else:
            actual_scenario = "flat"

        # Build scenarios (same as pre-market) but narrow to actual scenario
        symbol_regime = classify_symbol_regime(daily_df, pd.DataFrame(), nifty_daily=nifty_daily)
        dow_month_stats = compute_dow_month_stats(daily_df)
        scenarios = _build_gap_scenarios(sym, daily_df, nifty_daily, dow_month_stats,
                                         symbol_regime, news_data=news_data)
        if not scenarios:
            continue

        # Find the matching scenario and boost its probability
        confirmed = None
        for sc in scenarios:
            if sc["type"] == actual_scenario:
                confirmed = sc
                # Refine entry based on actual indicated open
                if actual_scenario == "gap_up":
                    confirmed["entry"] = round(indicated_open + float(compute_atr(daily_df)) * 0.05, 2)
                elif actual_scenario == "gap_down":
                    confirmed["entry"] = round(indicated_open - float(compute_atr(daily_df)) * 0.05, 2)
                else:
                    confirmed["entry"] = round(indicated_open, 2)
                # Boost probability since scenario is confirmed
                confirmed["probability"] = min(95, confirmed["probability"] + 10)
                break

        if confirmed is None:
            confirmed = scenarios[0]

        sym_news = news_data.get(sym, {})
        profile = _compute_stock_profile(
            {"entry_price": indicated_open, "stop_pct": 1.0, "target_pct": 1.5},
            daily_df, nifty_daily,
        )

        setup = {
            "symbol": sym,
            "name": cfg.get("name", sym),
            "sector": cfg.get("sector", ""),
            "prev_close": prev_close,
            "indicated_open": indicated_open,
            "gap_pct": round(gap_pct, 2),
            "actual_scenario": actual_scenario,
            "pre_market_vol": pre_vol,
            "symbol_regime": symbol_regime,
            "gap_scenarios": scenarios,
            "confirmed_scenario": confirmed,
            "profile": profile,
            "news_sentiment": sym_news.get("sentiment", 0),
            "news_summary": sym_news.get("summary", ""),
            # Compatibility fields
            "strategy": confirmed["strategy"],
            "direction": confirmed["direction"],
            "entry_price": confirmed["entry"],
            "target_price": confirmed["target"],
            "stop_price": confirmed["stop"],
            "stop_pct": round(abs(confirmed["stop"] - confirmed["entry"]) / max(confirmed["entry"], 1) * 100, 2),
            "target_pct": round(abs(confirmed["target"] - confirmed["entry"]) / max(confirmed["entry"], 1) * 100, 2),
            "rr_ratio": confirmed["rr"],
            "score": confirmed["probability"] / 100,
            "confidence": confirmed["probability"] / 100,
            "signal": "STRONG" if confirmed["probability"] >= 65 and confirmed["rr"] >= 2.0
                      else "ACTIVE" if confirmed["probability"] >= 50 and confirmed["rr"] >= 1.5
                      else "WATCH",
        }
        all_setups.append(setup)

    # Rank by confirmed scenario probability
    all_setups.sort(key=lambda s: -s["confirmed_scenario"]["probability"])

    # Render
    _render_pre_live_output(all_setups, high_premarket_vol, nifty_regime, vix_info,
                             inst_flow, dow_name, month_period, news_data, nifty_daily)

    return all_setups


def _render_pre_live_output(setups, high_vol_stocks, nifty_regime, vix_info,
                             inst_flow, dow_name, month_period, news_data, nifty_daily):
    """Render pre-live output to terminal."""
    now_ist = datetime.now(IST)
    vix_val, vix_regime = vix_info

    lines = []
    lines.append(box_top())
    lines.append(box_line(f"PRE-LIVE SCANNER — {now_ist.strftime('%Y-%m-%d %H:%M')} IST"))
    lines.append(box_line(f"Nifty: {nifty_regime.upper()} | VIX: {vix_val or 'N/A'} ({vix_regime}) | Flow: {inst_flow}"))
    lines.append(box_line(f"DOW: {dow_name} | Period: {month_period}"))
    lines.append(box_line("Pre-market auction active — scenarios narrowed to actual gaps"))
    lines.append(box_mid())

    # High pre-market volume stocks (institutional activity)
    if high_vol_stocks:
        lines.append(box_line("INSTITUTIONAL ACTIVITY (high pre-market volume)"))
        for hv in sorted(high_vol_stocks, key=lambda x: -x["pre_vol_pct"])[:5]:
            sym = hv["symbol"].replace(".NS", "")
            lines.append(box_line(
                f"  {sym} ({hv['name']}): pre-vol {hv['pre_vol_pct']:.1f}% of daily avg "
                f"| gap: {hv['gap_pct']:+.2f}% | open: ~{_format_rupee(hv['indicated_open'])}"
            ))
        lines.append(box_line())
        lines.append(box_mid())

    actionable = [s for s in setups if s.get("signal") in ("STRONG", "ACTIVE")]
    watchlist = [s for s in setups if s.get("signal") == "WATCH"]

    if not actionable and not watchlist:
        lines.append(box_line("No qualifying setups. Wait for 9:15 live scan."))
        lines.append(box_bot())
        print("\n".join(lines))
        return

    if actionable:
        lines.append(box_line(f"CONFIRMED SETUPS ({len(actionable)} stocks)"))
        lines.append(box_line())

        for setup in actionable[:10]:
            sym = setup["symbol"].replace(".NS", "")
            confirmed = setup["confirmed_scenario"]
            gap_pct = setup["gap_pct"]
            scenario_type = setup["actual_scenario"].replace("_", " ").upper()

            lines.append(box_line(
                f"  {sym} ({setup['name']}) — {scenario_type} ({gap_pct:+.2f}%)"
            ))
            lines.append(box_line(
                f"    {confirmed['strategy'].upper()} {_action_label(confirmed['direction'])} | "
                f"Entry: ~{_format_rupee(confirmed['entry'])} | "
                f"Tgt: {_format_rupee(confirmed['target'])} | "
                f"SL: {_format_rupee(confirmed['stop'])}"
            ))
            lines.append(box_line(
                f"    Prob: {confirmed['probability']:.0f}% | RR: {confirmed['rr']:.1f}:1 | "
                f"Signal: {setup['signal']}"
            ))
            if setup.get("news_summary"):
                lines.append(box_line(f"    News: {setup['news_summary']}"))
            lines.append(box_line())

    if watchlist:
        lines.append(box_line(f"WATCHLIST ({len(watchlist)})"))
        for s in watchlist[:5]:
            sym = s["symbol"].replace(".NS", "")
            sc = s["confirmed_scenario"]
            lines.append(box_line(f"  {sym}: {sc['strategy']} {sc['direction']} "
                                  f"(prob: {sc['probability']:.0f}%)"))
        lines.append(box_line())

    lines.append(box_bot())
    print("\n".join(lines))

    # Write enriched report
    INTRADAY_REPORT_DIR.mkdir(exist_ok=True)
    path = INTRADAY_REPORT_DIR / f"pre_live_{now_ist.strftime('%Y-%m-%d_%H%M')}.md"
    md = [f"# Pre-Live Scanner — {now_ist.strftime('%Y-%m-%d %H:%M')} IST\n"]
    md.append(f"**Nifty**: {nifty_regime.upper()} | **VIX**: {vix_val or 'N/A'} ({vix_regime}) | "
              f"**Flow**: {inst_flow}")
    md.append(f"**DOW**: {dow_name} | **Period**: {month_period}\n")

    # How to Read
    md.append("## How to Read This Report\n")
    md.append("- **BUY** = Buy shares first, sell later for profit (price expected to go UP)")
    md.append("- **SELL** = Sell shares first, buy back later for profit (price expected to go DOWN)")
    md.append("- Pre-market auction data is now available — gap scenarios are **confirmed**")
    md.append("- **High pre-market volume** = Institutional interest before market open\n")

    if high_vol_stocks:
        md.append("## Institutional Activity\n")
        md.append("Stocks with unusually high pre-market volume — signals institutional positioning:\n")
        md.append("| Symbol | Pre-Vol % | Gap | Indicated Open |")
        md.append("|--------|-----------|-----|----------------|")
        for hv in high_vol_stocks:
            md.append(f"| {hv['symbol'].replace('.NS', '')} | {hv['pre_vol_pct']:.1f}% | "
                      f"{hv['gap_pct']:+.2f}% | {hv['indicated_open']:.2f} |")
        md.append("")

    if actionable:
        md.append("## Confirmed Setups\n")
        for s in actionable:
            sym = s["symbol"].replace(".NS", "")
            sc = s["confirmed_scenario"]
            regime = s.get("symbol_regime", {})
            scenario_type = s["actual_scenario"].replace("_", " ").title()
            direction_label = _action_label(sc["direction"])
            direction_explain = _action_label(sc["direction"], explain=True)

            md.append(f"### {sym} — {s.get('name', sym)}\n")
            md.append(f"**Confirmed scenario**: {scenario_type} ({s['gap_pct']:+.2f}%) | "
                      f"**Signal**: {s.get('signal', 'ACTIVE')}\n")

            # Strategy explanation
            strat = sc.get("strategy", "")
            strat_desc = STRATEGY_DESCRIPTIONS.get(strat, "")
            if strat_desc:
                md.append(f"**Strategy**: {strat.upper()} — {strat_desc}\n")

            # Action + levels
            md.append(f"**Action**: {direction_explain}")
            md.append(f"- Entry: ~₹{sc['entry']:,.2f}")
            md.append(f"- Target: ₹{sc['target']:,.2f}")
            md.append(f"- Stop-loss: ₹{sc['stop']:,.2f}")
            md.append(f"- Probability: {sc['probability']:.0f}% | RR: {sc['rr']:.1f}:1\n")

            # Risk per ₹1L
            entry = sc.get("entry", 0)
            stop = sc.get("stop", 0)
            target = sc.get("target", 0)
            if entry > 0:
                shares = int(100_000 / entry)
                risk_amt = abs(entry - stop) * shares
                reward_amt = abs(target - entry) * shares
                md.append(f"**Per ₹1L capital**: ~{shares} shares | "
                          f"Risk: ₹{risk_amt:,.0f} | Reward: ₹{reward_amt:,.0f}\n")

            # Conditions to watch
            watch_items = sc.get("conditions_to_watch", [])
            if watch_items:
                md.append("**Watch at 9:15 open**:")
                for w in watch_items:
                    md.append(f"- {w}")
                md.append("")

            # Historical context
            hist = sc.get("historical_context", "")
            if hist:
                md.append(f"**History**: {hist}\n")

            if s.get("news_summary"):
                md.append(f"**News**: {s['news_summary']}\n")

            # Verdict
            signal = s.get("signal", "WATCH")
            if signal == "STRONG":
                md.append("**Verdict**: HIGH CONVICTION — multiple factors align. Full position size.\n")
            elif signal == "ACTIVE":
                md.append("**Verdict**: GOOD SETUP — edge is present but not overwhelming. Normal position size.\n")

            md.append("---\n")

        # LLM advisory in report
        market_context = {
            "nifty_regime": nifty_regime,
            "vix_val": vix_val,
            "vix_regime": vix_regime,
            "inst_flow": inst_flow,
            "market_news": (news_data or {}).get("_market", ""),
        }
        llm_text = generate_llm_explanation(actionable[:3], "pre_live", market_context)
        if llm_text:
            md.append("## AI Advisory\n")
            md.append(llm_text)
            md.append("")

    with open(path, "w") as f:
        f.write("\n".join(md) + "\n")
    print(f"  Report saved: {path}")


# ── Post-Market Scan (after 15:15) ──────────────────────────────────────

def run_post_market_scan(config, symbols, now_ist=None, data_override=None,
                         skip_llm=False):
    """Post-market scan: session review + tomorrow's watchlist.

    Fetches today's full intraday data, classifies the day,
    and projects tomorrow morning setups using IF-THEN format.

    Args:
        data_override: dict {symbol: {"daily": df, "intra": df}, "_nifty": {"daily": df, "intra": df}}
        skip_llm: if True, skip LLM calls, rendering, and report writing
    """
    now_ist = now_ist or datetime.now(IST)
    capital = config.get("global", {}).get("capital", 1000000)

    if not data_override:
        print("  [POST-MARKET] Session review + tomorrow's watchlist...")
        print(f"  Time: {now_ist.strftime('%H:%M')} IST — market closed")

    # Fetch VIX + Nifty
    if data_override:
        vix_val, vix_regime = data_override.get("_vix", (None, "normal"))
        vix_info = (vix_val, vix_regime)
        nifty_daily = data_override.get("_nifty", {}).get("daily", pd.DataFrame())
        nifty_intra = data_override.get("_nifty", {}).get("intra", pd.DataFrame())
        inst_flow = data_override.get("_inst_flow", "neutral")
        news_data = data_override.get("_news", {})
    else:
        vix_val, vix_regime = fetch_india_vix()
        vix_info = (vix_val, vix_regime)
        nifty_daily = fetch_yf(BENCHMARK, period="6mo", interval="1d")
        nifty_intra = fetch_yf(BENCHMARK, period="5d", interval="5m")
        inst_flow = estimate_institutional_flow()
        try:
            news_data = get_news_and_sentiment(symbols)
        except Exception:
            news_data = {}

    nifty_regime, _ = detect_nifty_regime(nifty_daily)
    nifty_ist = compute_vwap(_to_ist(nifty_intra)) if not nifty_intra.empty else pd.DataFrame()

    # Classify today's day type with full session data
    day_type_info = reclassify_day_type(nifty_ist, nifty_daily) if not nifty_ist.empty else {
        "type": "range_bound", "confidence": 0.3, "detail": "No data"}

    dow = now_ist.weekday()
    dow_name = DOW_NAMES.get(dow, "Unknown")
    month_period = classify_month_period(now_ist)

    # Tomorrow's DOW
    tomorrow = now_ist + timedelta(days=1)
    if tomorrow.weekday() >= 5:  # skip weekends
        tomorrow = now_ist + timedelta(days=(7 - now_ist.weekday()))
    tomorrow_dow = DOW_NAMES.get(tomorrow.weekday(), "Unknown")

    if not data_override:
        print(f"  Today: {dow_name} ({day_type_info['type']}) | Tomorrow: {tomorrow_dow}")
        print(f"  Nifty: {nifty_regime.upper()} | VIX: {vix_val or 'N/A'} | Flow: {inst_flow}")

    # ── Section 1: Session Review ──
    session_summaries = []
    for sym in symbols:
        if data_override:
            intra_df = data_override.get(sym, {}).get("intra", pd.DataFrame())
            daily_df = data_override.get(sym, {}).get("daily", pd.DataFrame())
        else:
            intra_df = fetch_yf(sym, period="5d", interval="5m")
            daily_df = fetch_yf(sym, period="6mo", interval="1d")
        if intra_df.empty or daily_df.empty:
            continue

        cfg = TICKERS.get(sym, {"name": sym, "sector": ""})
        intra_ist = _to_ist(intra_df)
        today = intra_ist.index[-1].date()
        today_bars = intra_ist[intra_ist.index.date == today]
        if today_bars.empty:
            continue

        day_open = float(today_bars["Open"].iloc[0])
        day_close = float(today_bars["Close"].iloc[-1])
        day_high = float(today_bars["High"].max())
        day_low = float(today_bars["Low"].min())
        day_range = day_high - day_low
        day_return = (day_close - day_open) / day_open * 100 if day_open > 0 else 0

        prev_close = float(daily_df["Close"].iloc[-2]) if len(daily_df) >= 2 else day_open
        gap_pct = (day_open - prev_close) / prev_close * 100 if prev_close > 0 else 0

        session_summaries.append({
            "symbol": sym,
            "name": cfg.get("name", sym),
            "open": day_open,
            "close": day_close,
            "high": day_high,
            "low": day_low,
            "range": day_range,
            "return_pct": day_return,
            "gap_pct": gap_pct,
            "trend": "up" if day_return > 0.3 else "down" if day_return < -0.3 else "flat",
        })

    # ── Section 2: Tomorrow's Watchlist ──
    tomorrow_setups = []
    for sym in symbols:
        if data_override:
            daily_df = data_override.get(sym, {}).get("daily", pd.DataFrame())
        else:
            daily_df = fetch_yf(sym, period="6mo", interval="1d")
        if daily_df.empty:
            continue

        cfg = TICKERS.get(sym, {"name": sym, "sector": ""})
        symbol_regime = classify_symbol_regime(daily_df, pd.DataFrame(), nifty_daily=nifty_daily)
        dow_month_stats = compute_dow_month_stats(daily_df)

        # Filter: only stocks with strong daily + weekly alignment
        trend = symbol_regime.get("trend", "sideways")
        weekly = symbol_regime.get("weekly_trend", "sideways")
        if trend == "sideways" and weekly == "sideways":
            continue  # skip directionless stocks

        # Check tomorrow's DOW stats
        tomorrow_wr = dow_month_stats.get(tomorrow_dow, {}).get("all", {}).get("win_rate", 50)
        if tomorrow_wr < 40:
            continue  # unfavorable DOW

        scenarios = _build_gap_scenarios(sym, daily_df, nifty_daily, dow_month_stats,
                                         symbol_regime, news_data=news_data)
        if not scenarios:
            continue

        best = max(scenarios, key=lambda s: s["probability"])
        if best["probability"] < 45:
            continue

        sym_news = news_data.get(sym, {})
        tomorrow_setups.append({
            "symbol": sym,
            "name": cfg.get("name", sym),
            "symbol_regime": symbol_regime,
            "gap_scenarios": scenarios,
            "best_scenario": best,
            "tomorrow_dow": tomorrow_dow,
            "tomorrow_wr": tomorrow_wr,
            "news_sentiment": sym_news.get("sentiment", 0),
            "news_summary": sym_news.get("summary", ""),
            # Compatibility
            "strategy": best["strategy"],
            "direction": best["direction"],
            "entry_price": best["entry"],
            "target_price": best["target"],
            "stop_price": best["stop"],
            "rr_ratio": best["rr"],
            "score": best["probability"] / 100,
            "signal": "STRONG" if best["probability"] >= 65 else "ACTIVE" if best["probability"] >= 50 else "WATCH",
        })

    tomorrow_setups.sort(key=lambda s: -s["best_scenario"]["probability"])

    # Render (skip during backtest)
    if not skip_llm:
        _render_post_market_output(session_summaries, tomorrow_setups, day_type_info,
                                    nifty_regime, vix_info, inst_flow, dow_name,
                                    tomorrow_dow, month_period, news_data, nifty_daily)

    return session_summaries, tomorrow_setups


def _render_post_market_output(summaries, tomorrow_setups, day_type_info,
                                nifty_regime, vix_info, inst_flow, dow_name,
                                tomorrow_dow, month_period, news_data, nifty_daily):
    """Render post-market output."""
    now_ist = datetime.now(IST)
    vix_val, vix_regime = vix_info

    lines = []
    lines.append(box_top())
    lines.append(box_line(f"POST-MARKET REVIEW — {now_ist.strftime('%Y-%m-%d %H:%M')} IST"))
    lines.append(box_line(f"Nifty: {nifty_regime.upper()} | VIX: {vix_val or 'N/A'} ({vix_regime})"))
    lines.append(box_line(f"Day type: {day_type_info.get('type', 'N/A')} "
                          f"(conf: {day_type_info.get('confidence', 0):.0%})"))
    lines.append(box_mid())

    # Session review
    lines.append(box_line("SESSION REVIEW"))
    lines.append(box_line())

    # Sort by absolute return
    summaries.sort(key=lambda s: -abs(s["return_pct"]))
    movers_up = [s for s in summaries if s["return_pct"] > 0.3]
    movers_dn = [s for s in summaries if s["return_pct"] < -0.3]

    if movers_up:
        lines.append(box_line(f"  Top gainers ({len(movers_up)}):"))
        for s in movers_up[:5]:
            sym = s["symbol"].replace(".NS", "")
            lines.append(box_line(
                f"    {sym}: {s['return_pct']:+.2f}% | {_format_rupee(s['close'])} "
                f"| Range: {_format_rupee(s['range'])} | Gap: {s['gap_pct']:+.2f}%"
            ))
    if movers_dn:
        lines.append(box_line(f"  Top losers ({len(movers_dn)}):"))
        for s in movers_dn[:5]:
            sym = s["symbol"].replace(".NS", "")
            lines.append(box_line(
                f"    {sym}: {s['return_pct']:+.2f}% | {_format_rupee(s['close'])} "
                f"| Range: {_format_rupee(s['range'])} | Gap: {s['gap_pct']:+.2f}%"
            ))

    flat = [s for s in summaries if abs(s["return_pct"]) <= 0.3]
    if flat:
        lines.append(box_line(f"  Flat: {len(flat)} stocks"))
    lines.append(box_line())

    # Trade review from Supabase
    try:
        from common.db import get_today_trades
        today_trades = get_today_trades(scanner_type="intraday")
        if today_trades:
            lines.append(box_mid())
            lines.append(box_line("TODAY'S TRADES"))
            total_pnl = sum(t.get("pnl", 0) for t in today_trades)
            wins = sum(1 for t in today_trades if t.get("pnl", 0) > 0)
            lines.append(box_line(f"  Trades: {len(today_trades)} | Wins: {wins} | P&L: {_format_rupee(total_pnl)}"))
            lines.append(box_line())
    except Exception:
        pass

    # Tomorrow's watchlist
    lines.append(box_mid())
    lines.append(box_line(f"TOMORROW'S WATCHLIST ({tomorrow_dow})"))
    lines.append(box_line())

    actionable_tomorrow = [s for s in tomorrow_setups if s.get("signal") in ("STRONG", "ACTIVE")]
    if actionable_tomorrow:
        for setup in actionable_tomorrow[:8]:
            sym = setup["symbol"].replace(".NS", "")
            best = setup["best_scenario"]
            regime = setup["symbol_regime"]
            lines.append(box_line(
                f"  {sym} ({setup['name']}) — {regime.get('trend', 'N/A')} trend"
            ))
            lines.append(box_line(
                f"    Best: {best['type']} → {best['strategy'].upper()} {_action_label(best['direction'])} "
                f"| Prob: {best['probability']:.0f}% | RR: {best['rr']:.1f}"
            ))
            lines.append(box_line(
                f"    {tomorrow_dow} WR: {setup['tomorrow_wr']:.0f}%"
            ))
            lines.append(box_line())
    else:
        lines.append(box_line("  No strong setups for tomorrow."))
        lines.append(box_line())

    lines.append(box_bot())
    print("\n".join(lines))

    # Write report
    INTRADAY_REPORT_DIR.mkdir(exist_ok=True)
    path = INTRADAY_REPORT_DIR / f"post_market_{now_ist.strftime('%Y-%m-%d_%H%M')}.md"
    md = [f"# Post-Market Review — {now_ist.strftime('%Y-%m-%d %H:%M')} IST\n"]
    md.append(f"**Day type**: {day_type_info.get('type', 'N/A')} | **Nifty**: {nifty_regime.upper()} | "
              f"**VIX**: {vix_val or 'N/A'}\n")

    md.append("## Session Summary\n")
    md.append("| Symbol | Return | Close | Range | Gap |")
    md.append("|--------|--------|-------|-------|-----|")
    for s in summaries[:15]:
        sym = s["symbol"].replace(".NS", "")
        md.append(f"| {sym} | {s['return_pct']:+.2f}% | {s['close']:.2f} | "
                  f"{s['range']:.2f} | {s['gap_pct']:+.2f}% |")
    md.append("")

    # How to Read This Report
    md.append("## How to Read This Report\n")
    md.append("- **BUY** = Buy shares first, sell later for profit (price expected to go UP)")
    md.append("- **SELL** = Sell shares first, buy back later for profit (price expected to go DOWN)")
    md.append("- **Gap-up/Gap-down/Flat** = How the stock opens tomorrow relative to today's close")
    md.append("- **Prob** = Historical probability this scenario plays out")
    md.append("- **RR** = Risk-Reward ratio (e.g., 3.0 means you gain ₹3 for every ₹1 risked)")
    md.append("- **DOW WR** = Win rate on this day of the week historically")
    md.append("- **STRONG** = High conviction, full position size | **ACTIVE** = Good setup, normal size\n")

    # Market Context
    _day_type = day_type_info.get("type", "N/A")
    md.append("## Market Context\n")
    md.append(f"Today was a **{_day_type}** session. Nifty stayed in a {nifty_regime.upper()} regime "
              f"with VIX at {vix_val or 'N/A'} ({vix_regime} volatility). "
              f"This shapes tomorrow's setups — "
              + ("expect momentum and breakout strategies to work best."
                 if _day_type in ("trending_up", "trending_down")
                 else "mean-reversion and pullback strategies may be more effective than aggressive breakouts.")
              + "\n")

    if actionable_tomorrow:
        # Recommended Trades summary
        strong_tm = [s for s in actionable_tomorrow if s.get("signal") == "STRONG"]
        active_tm = [s for s in actionable_tomorrow if s.get("signal") == "ACTIVE"]

        md.append(f"## Recommended Trades for {tomorrow_dow}\n")
        md.append("Ranked by probability and conviction. These are **conditional** — "
                  "check pre-market data at 9:00 to confirm which scenario plays out.\n")

        md.append("| # | Symbol | IF opens | Action | Entry | Target | Stop | Prob | RR | Risk/₹1L | Signal |")
        md.append("|---|--------|----------|--------|-------|--------|------|------|----|----------|--------|")

        rank = 0
        for setup in strong_tm + active_tm:
            rank += 1
            sym = setup["symbol"].replace(".NS", "")
            best = setup["best_scenario"]
            direction_label = _action_label(best["direction"])
            gap_label = best["type"].replace("_", " ")
            entry = best.get("entry", 0)
            stop = best.get("stop", 0)
            risk_per_lakh = ""
            if entry > 0:
                shares = int(100_000 / entry)
                risk_per_lakh = f"₹{abs(entry - stop) * shares:,.0f}"
            md.append(
                f"| {rank} | **{sym}** | {gap_label} | {best['strategy'].upper()} {direction_label} | "
                f"₹{entry:,.0f} | ₹{best.get('target', 0):,.0f} | ₹{stop:,.0f} | "
                f"{best['probability']:.0f}% | {best['rr']:.1f} | {risk_per_lakh} | "
                f"{setup.get('signal', '')} |"
            )
        md.append("")

        # Quick action plan
        top3 = (strong_tm + active_tm)[:3]
        if top3:
            md.append("### Quick Action Plan\n")
            for i, setup in enumerate(top3, 1):
                sym = setup["symbol"].replace(".NS", "")
                best = setup["best_scenario"]
                direction_label = _action_label(best["direction"])
                gap_label = best["type"].replace("_", " ")
                watch_items = best.get("conditions_to_watch", [])
                watch_str = f" Confirm: {watch_items[0]}" if watch_items else ""
                md.append(f"{i}. **{sym}** — IF {gap_label} → {best['strategy'].upper()} "
                          f"{direction_label} @ ₹{best['entry']:,.0f} | "
                          f"Stop ₹{best['stop']:,.0f} | Target ₹{best['target']:,.0f}.{watch_str}")
            md.append("")
            md.append(f"> **Max positions**: Pick top 2-3. Don't overload.\n")

        md.append("---\n")

        md.append(f"## Detailed Setups ({tomorrow_dow})\n")
        for setup in actionable_tomorrow:
            sym = setup["symbol"].replace(".NS", "")
            best = setup["best_scenario"]
            regime = setup["symbol_regime"]
            direction_label = _action_label(best["direction"])
            direction_explain = _action_label(best["direction"], explain=True)

            md.append(f"### {sym} — {setup['name']}\n")
            md.append(f"**Signal**: {setup.get('signal', 'ACTIVE')} | "
                      f"**Probability**: {best['probability']:.0f}% | "
                      f"**RR**: {best['rr']:.1f}:1\n")

            # Strategy explanation
            strat_desc = STRATEGY_DESCRIPTIONS.get(best["strategy"], "")
            if strat_desc:
                md.append(f"**Strategy**: {best['strategy'].upper()} — {strat_desc}\n")

            # Action + levels
            md.append(f"**Action**: {direction_explain}")
            md.append(f"- Entry: ~₹{best['entry']:,.2f}")
            md.append(f"- Target: ₹{best['target']:,.2f}")
            md.append(f"- Stop-loss: ₹{best['stop']:,.2f}\n")

            # Stock context
            trend = regime.get("trend", "sideways")
            vol = regime.get("volatility", "normal")
            momentum = regime.get("momentum", "neutral")
            md.append(f"**Context**: {trend} trend, {vol} volatility, {momentum} momentum")
            md.append(f"- DOW win rate for {tomorrow_dow}: {setup['tomorrow_wr']:.0f}%\n")

            # Risk per ₹1L capital
            entry = best["entry"]
            stop = best["stop"]
            target = best["target"]
            if entry > 0:
                shares = int(100_000 / entry)
                risk_amt = abs(entry - stop) * shares
                reward_amt = abs(target - entry) * shares
                md.append(f"**Per ₹1L capital**: ~{shares} shares | "
                          f"Risk: ₹{risk_amt:,.0f} | Reward: ₹{reward_amt:,.0f}\n")

            # Conditions to watch
            conds = best.get("conditions_to_watch", [])
            if conds:
                md.append("**Watch for at open**:")
                for c in conds:
                    md.append(f"- {c}")
                md.append("")

            # Historical context
            hist = best.get("historical_context", "")
            if hist:
                md.append(f"**History**: {hist}\n")

            # Risks
            risks = []
            if best.get("direction") == "long" and regime.get("weekly_trend") in ("mild_down", "strong_down"):
                risks.append("Weekly trend is down — fighting the bigger picture")
            elif best.get("direction") == "short" and regime.get("weekly_trend") in ("mild_up", "strong_up"):
                risks.append("Weekly trend is up — shorting into strength")
            if regime.get("volatility") == "expanded":
                risks.append("Expanded volatility — wider stops needed, smaller size")
            if risks:
                md.append("**Risks**:")
                for r in risks:
                    md.append(f"- {r}")
                md.append("")

            # Verdict
            signal = setup.get("signal", "WATCH")
            if signal == "STRONG":
                md.append("**Verdict**: HIGH CONVICTION — multiple factors align. Full position size.\n")
            elif signal == "ACTIVE":
                md.append("**Verdict**: GOOD SETUP — edge is present but not overwhelming. Normal position size.\n")

            md.append("---\n")

        # LLM advisory in report
        market_context = {
            "nifty_regime": nifty_regime,
            "vix_val": vix_val,
            "vix_regime": vix_regime,
            "inst_flow": inst_flow,
            "day_type": day_type_info.get("type", "N/A"),
            "market_news": (news_data or {}).get("_market", ""),
        }
        llm_text = generate_llm_explanation(actionable_tomorrow[:3], "post_market", market_context)
        if llm_text:
            md.append("## AI Advisory\n")
            md.append(llm_text)
            md.append("")

    with open(path, "w") as f:
        f.write("\n".join(md) + "\n")
    print(f"  Report saved: {path}")


# ── Core Evaluation ──────────────────────────────────────────────────────

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

        # MLR is exempt from nifty_ok gate — it works in bearish markets
        is_mlr = candidate["strategy"] == "mlr"

        gates = {
            "vwap_gate": vwap_gate,
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


# ── AI Context Builder ──────────────────────────────────────────────────

def build_intraday_context(candidates, nifty_state, vix_info, day_type_info,
                           dow_name, month_period, news_data=None):
    """Build LLM context string with market state and per-candidate details."""
    vix_val, vix_regime = vix_info
    lines = []
    now = datetime.now(IST)
    lines.append(f"Time: {now.strftime('%Y-%m-%d %H:%M')} IST")
    lines.append(f"Nifty regime: {nifty_state.get('regime', 'unknown')} | "
                 f"Making new lows: {nifty_state.get('new_lows', False)}")
    lines.append(f"VIX: {vix_val} ({vix_regime})")
    lines.append(f"Day type: {day_type_info.get('type', 'unknown')} "
                 f"(conf: {day_type_info.get('confidence', 0):.0%}) — {day_type_info.get('detail', '')}")
    lines.append(f"DOW: {dow_name} | Month period: {month_period}")
    inst_flow = nifty_state.get("institutional_flow", "neutral")
    lines.append(f"Institutional flow: {inst_flow}")
    lines.append(f"Max positions: {MAX_INTRADAY_POSITIONS}")

    # Market macro context from news
    if news_data and news_data.get("_market"):
        lines.append(f"\nMarket context: {news_data['_market']}")
    lines.append("")

    for c in candidates:
        if c.get("signal") == "AVOID":
            continue
        sym = c["symbol"].replace(".NS", "")
        regime = c.get("symbol_regime", {})
        lines.append(f"--- {sym} ({c.get('name', '')}) [{c['strategy'].upper()}] "
                     f"{_action_label(c['direction'])} | Signal: {c['signal']} ---")
        lines.append(f"  LTP: {fmt(c['ltp'])} | Change: {fmt(c.get('change_pct'))}%")
        lines.append(f"  Entry: {fmt(c['entry_price'])} | Target: {fmt(c['target_price'])} "
                     f"(+{c['target_pct']}%) | Stop: {fmt(c['stop_price'])} (-{c['stop_pct']}%)")
        lines.append(f"  RR: {c['rr_ratio']} | Score: {c['score']:.0%} | Conf: {c['confidence']:.0%}")
        lines.append(f"  Regime: trend={regime.get('trend','N/A')}, "
                     f"weekly={regime.get('weekly_trend','N/A')}, "
                     f"momentum={regime.get('momentum','N/A')}, "
                     f"RS={regime.get('relative_strength','N/A')}")
        lines.append(f"  DOW WR: {c.get('dow_wr', 'N/A')}% | Month WR: {c.get('month_period_wr', 'N/A')}%")
        # Convergence
        conv_detail = c.get("convergence_detail", "N/A")
        conv_score = c.get("convergence_score", 0)
        lines.append(f"  Convergence: {conv_score}% — {conv_detail}")
        # Historical hit rate
        hist_ctx = c.get("historical_context", "")
        if hist_ctx:
            lines.append(f"  History: {hist_ctx}")
        # News
        news_summary = c.get("news_summary", "")
        news_sent = c.get("news_sentiment", 0)
        if news_summary:
            lines.append(f"  News: {news_summary} (sentiment: {news_sent:+.1f})")
        lines.append(f"  Reason: {c.get('reason', '')}")
        lines.append(f"  Signal: {c.get('signal_reason', '')}")
        lines.append("")

    return "\n".join(lines)


def get_intraday_advisory(context, config=None):
    """Call LLM for intraday advisory via common.llm (env-driven provider)."""
    from common.llm import call_llm

    messages = [
        {"role": "system", "content": INTRADAY_AI_SYSTEM_PROMPT},
        {"role": "user", "content": context},
    ]

    return call_llm(messages, max_tokens=1000)


# ── Dashboard Rendering ─────────────────────────────────────────────────

def render_intraday_dashboard(candidates, nifty_state, vix_info, day_type_info,
                               dow_name, month_period, ai_text, metrics):
    """Box-drawing dashboard for terminal output."""
    vix_val, vix_regime = vix_info
    now = datetime.now(IST)
    lines = []

    lines.append(box_top())
    regime = nifty_state.get("regime", "unknown").upper()
    vix_str = f"VIX: {vix_val} ({vix_regime.upper()})" if vix_val else "VIX: N/A"
    dt = day_type_info.get("type", "unknown").upper().replace("_", " ")
    lines.append(box_line(f"INTRADAY SCANNER - {now.strftime('%Y-%m-%d %H:%M')} IST"))
    lines.append(box_line(f"Nifty: {regime} | {vix_str} | Day: {dt}"))
    lines.append(box_line(f"DOW: {dow_name} | Period: {month_period} | Max: {MAX_INTRADAY_POSITIONS}"))
    lines.append(box_mid())

    # Group by signal tier
    strong = [c for c in candidates if c.get("signal") == "STRONG"]
    active = [c for c in candidates if c.get("signal") == "ACTIVE"]
    watch = [c for c in candidates if c.get("signal") == "WATCH"]
    avoid = [c for c in candidates if c.get("signal") == "AVOID"]

    # Strong signals
    if strong:
        lines.append(box_line("STRONG SIGNALS"))
        lines.append(box_line())
        for c in strong:
            _render_candidate(lines, c)
    else:
        lines.append(box_line("STRONG SIGNALS: None"))
        lines.append(box_line())

    # Active signals
    if active:
        lines.append(box_line("ACTIVE SIGNALS"))
        lines.append(box_line())
        for c in active:
            _render_candidate(lines, c)

    # Watch
    if watch:
        lines.append(box_line("WATCH LIST"))
        for c in watch:
            sym = c["symbol"].replace(".NS", "")
            strat = c["strategy"].upper()
            lines.append(box_line(
                f"  {sym} [{strat}] {_action_label(c['direction'])} "
                f"Score:{c['score']:.0%} — {c.get('signal_reason', '')}"
            ))
        lines.append(box_line())

    # Avoid (count only)
    if avoid:
        lines.append(box_line(f"AVOIDED: {len(avoid)} candidates"))
        lines.append(box_line())

    # Portfolio metrics
    if metrics and metrics.get("n_trades", 0) > 0:
        lines.append(box_mid())
        pm = metrics
        lines.append(box_line("METRICS (30d)"))
        lines.append(box_line(
            f"  Trades: {pm['n_trades']}  |  WR: {pm['win_rate']}%  |  "
            f"P&L: {pm['gross_pnl']:+,.0f}"
        ))
        lines.append(box_line())

    lines.append(box_bot())
    return "\n".join(lines)


def _render_candidate(lines, c):
    """Render a single candidate in the dashboard."""
    sym = c["symbol"].replace(".NS", "")
    strat = c["strategy"].upper()
    direction = _action_label(c["direction"])
    chg = f"{c.get('change_pct', 0):+.2f}%"
    regime = c.get("symbol_regime", {})

    lines.append(box_line(
        f"  {sym} [{strat}] {direction}  {fmt(c['ltp'])} ({chg})  "
        f"Score: {c['score']:.0%}"
    ))
    lines.append(box_line(
        f"  Entry {fmt(c['entry_price'])} -> Tgt {fmt(c['target_price'])} "
        f"(+{c['target_pct']}%) / SL {fmt(c['stop_price'])} (-{c['stop_pct']}%)"
    ))
    lines.append(box_line(
        f"  RR: {c['rr_ratio']}  DOW-WR: {c.get('dow_wr', 'N/A')}%  "
        f"Month: {c.get('month_period', '')} ({c.get('month_period_wr', 'N/A')}%)"
    ))
    # Momentum + relative strength
    lines.append(box_line(
        f"  Momentum: {regime.get('momentum', 'N/A')}  "
        f"RS: {regime.get('relative_strength', 'N/A')}"
    ))
    # Convergence + historical
    conv_score = c.get("convergence_score", 0)
    conv_detail = c.get("convergence_detail", "")
    hist_ctx = c.get("historical_context", "")
    if conv_detail:
        lines.append(box_line(f"  Convergence: {conv_score}% — {conv_detail}"))
    if hist_ctx:
        lines.append(box_line(f"  History: {hist_ctx}"))
    # Time window status
    time_status = c.get("time_status", "")
    if time_status:
        lines.append(box_line(f"  {time_status}"))
    # News
    news_sum = c.get("news_summary", "")
    if news_sum:
        lines.append(box_line(f"  News: {news_sum}"))

    qty = c.get("recommended_qty", 0)
    risk = c.get("capital_at_risk", 0)
    if qty > 0:
        lines.append(box_line(f"  Qty: {qty}  |  Risk: {risk:,.0f}"))

    lines.append(box_line(f"  {c.get('reason', '')}"))
    lines.append(box_line())


# ── Markdown Report ─────────────────────────────────────────────────────

def write_intraday_report(candidates, nifty_state, vix_info, day_type_info,
                           dow_name, month_period, ai_text):
    """Write markdown report to intraday_reports/."""
    INTRADAY_REPORT_DIR.mkdir(exist_ok=True)
    now = datetime.now(IST)
    report_path = INTRADAY_REPORT_DIR / f"intraday_{now.strftime('%Y-%m-%d_%H%M')}.md"

    vix_val, vix_regime = vix_info
    dt = day_type_info.get("type", "unknown")
    lines = []
    lines.append(f"# Intraday Scanner — {now.strftime('%Y-%m-%d %H:%M')} IST")
    lines.append(f"\n**Nifty**: {nifty_state.get('regime', 'unknown').upper()} | "
                 f"**VIX**: {vix_val} ({vix_regime}) | "
                 f"**Day Type**: {dt}")
    lines.append(f"**DOW**: {dow_name} | **Period**: {month_period}\n")

    # How to Read This Report
    lines.append("## How to Read This Report\n")
    lines.append("- **BUY** = Buy shares first, sell later for profit (price expected to go UP)")
    lines.append("- **SELL** = Sell shares first, buy back later for profit (price expected to go DOWN)")
    lines.append("- **RR** = Risk-Reward ratio (e.g., 3.0 means you gain ₹3 for every ₹1 risked)")
    lines.append("- **Score** = Overall setup quality (higher is better)")
    lines.append("- **Convergence** = How many indicators agree on the direction")
    lines.append("- **STRONG** = High conviction, full position size | **ACTIVE** = Good setup, normal size\n")

    # Group by signal tier
    strong = [c for c in candidates if c.get("signal") == "STRONG"]
    active = [c for c in candidates if c.get("signal") == "ACTIVE"]
    watch = [c for c in candidates if c.get("signal") == "WATCH"]

    # Recommended Trades summary
    if strong or active:
        lines.append("## Recommended Trades\n")
        lines.append("Ranked by conviction. Execute STRONG first, then ACTIVE if capital allows.\n")

        lines.append("| # | Symbol | Strategy | Action | Entry | Target | Stop | RR | Score | Risk/₹1L | Signal |")
        lines.append("|---|--------|----------|--------|-------|--------|------|-----|-------|----------|--------|")

        rank = 0
        for c in strong + active:
            rank += 1
            sym = c["symbol"].replace(".NS", "")
            direction_label = _action_label(c["direction"])
            entry = c.get("entry_price", 0)
            stop = c.get("stop_price", 0)
            target = c.get("target_price", 0)
            risk_per_lakh = ""
            if entry > 0:
                shares = int(100_000 / entry)
                risk_per_lakh = f"₹{abs(entry - stop) * shares:,.0f}"
            lines.append(
                f"| {rank} | **{sym}** | {c['strategy'].upper()} | {direction_label} | "
                f"{fmt(entry)} | {fmt(target)} | {fmt(stop)} | "
                f"{c['rr_ratio']} | {c['score']:.0%} | {risk_per_lakh} | "
                f"{c.get('signal', '')} |"
            )
        lines.append("")

        top3 = (strong + active)[:3]
        if top3:
            lines.append("### Quick Action Plan\n")
            for i, c in enumerate(top3, 1):
                sym = c["symbol"].replace(".NS", "")
                direction_label = _action_label(c["direction"])
                lines.append(f"{i}. **{sym}** — {c['strategy'].upper()} {direction_label} "
                             f"@ {fmt(c['entry_price'])} | Stop {fmt(c['stop_price'])} | "
                             f"Target {fmt(c['target_price'])}")
            lines.append("")
            lines.append(f"> **Max positions**: {MAX_INTRADAY_POSITIONS}. "
                         f"Today is {dow_name}, {month_period}.\n")

        lines.append("---\n")

    # Strategy breakdown
    strat_counts = {}
    for c in candidates:
        s = c["strategy"]
        strat_counts[s] = strat_counts.get(s, 0) + 1
    lines.append("## Strategy Breakdown\n")
    lines.append("| Strategy | Candidates |")
    lines.append("|----------|-----------|")
    for s, n in sorted(strat_counts.items()):
        lines.append(f"| {s} | {n} |")
    lines.append("")

    if strong:
        lines.append("## Strong Signals — Detailed\n")
        for c in strong:
            _write_candidate_md(lines, c)

    if active:
        lines.append("## Active Signals — Detailed\n")
        for c in active:
            _write_candidate_md(lines, c)

    if watch:
        lines.append("## Watch List — Detailed\n")
        lines.append("> These setups have potential but one or more gates failed. "
                     "Monitor and enter only if conditions improve.\n")
        for c in watch:
            _write_candidate_md(lines, c)
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


def _write_candidate_md(lines, c):
    """Write a single candidate as markdown with educational content."""
    sym = c["symbol"].replace(".NS", "")
    chg = f"{c.get('change_pct', 0):+.2f}%"
    regime = c.get("symbol_regime", {})
    strategy = c.get("strategy", "unknown")
    direction = c.get("direction", "long")

    lines.append(f"### {sym} — {c.get('name', '')} [{strategy.upper()}]")
    lines.append(f"\n**Signal**: {c.get('signal', 'WATCH')} | "
                 f"**Score**: {c['score']:.0%} | "
                 f"**Confidence**: {c['confidence']:.0%}\n")

    # Strategy explanation
    strat_desc = STRATEGY_DESCRIPTIONS.get(strategy, "")
    if strat_desc:
        lines.append(f"**Strategy**: {strategy.upper()} — {strat_desc}\n")

    # Action + levels
    lines.append(f"**Action**: {_action_label(direction, explain=True)}")
    lines.append(f"- LTP: {fmt(c['ltp'])} ({chg})")
    lines.append(f"- Entry: {fmt(c['entry_price'])}")
    lines.append(f"- Target: {fmt(c['target_price'])} (+{c['target_pct']}%)")
    lines.append(f"- Stop-loss: {fmt(c['stop_price'])} (-{c['stop_pct']}%)")
    lines.append(f"- RR: {c['rr_ratio']}:1\n")

    # Stock context
    trend = regime.get("trend", "N/A")
    vol = regime.get("volatility", "N/A")
    momentum = regime.get("momentum", "N/A")
    weekly = regime.get("weekly_trend", "N/A")
    rs = regime.get("relative_strength", "N/A")
    lines.append(f"**Context**: {trend} trend, {vol} volatility, {momentum} momentum")
    lines.append(f"- Weekly trend: {weekly} | Relative strength: {rs}")
    lines.append(f"- DOW WR: {c.get('dow_wr', 'N/A')}% | "
                 f"Month period: {c.get('month_period', '')} ({c.get('month_period_wr', 'N/A')}%)\n")

    # Risk per ₹1L capital
    entry = c.get("entry_price", 0)
    stop = c.get("stop_price", 0)
    target = c.get("target_price", 0)
    if entry > 0:
        shares = int(100_000 / entry)
        risk_amt = abs(entry - stop) * shares
        reward_amt = abs(target - entry) * shares
        lines.append(f"**Per ₹1L capital**: ~{shares} shares | "
                     f"Risk: ₹{risk_amt:,.0f} | Reward: ₹{reward_amt:,.0f}")
    qty = c.get("recommended_qty", 0)
    risk_cap = c.get("capital_at_risk", 0)
    if qty > 0:
        lines.append(f"- Recommended qty: {qty} | Capital at risk: ₹{risk_cap:,.0f}")
    lines.append("")

    # Convergence
    conv_score = c.get("convergence_score", 0)
    conv_detail = c.get("convergence_detail", "")
    if conv_detail:
        lines.append(f"**Convergence**: {conv_score}% — {conv_detail}")
        if conv_score >= 70:
            lines.append("Strong alignment — multiple indicators confirm the trade direction.")
        elif conv_score < 40:
            lines.append("Weak alignment — indicators are mixed. Reduce size or skip.")
        lines.append("")

    # Historical context
    hist_ctx = c.get("historical_context", "")
    if hist_ctx:
        lines.append(f"**History**: {hist_ctx}\n")

    # News
    news_sum = c.get("news_summary", "")
    news_sent = c.get("news_sentiment", 0)
    if news_sum:
        lines.append(f"**News**: {news_sum} (sentiment: {news_sent:+.1f})\n")

    # Risks
    risks = []
    if direction == "long" and weekly in ("mild_down", "strong_down"):
        risks.append("Weekly trend is down — fighting the bigger picture")
    elif direction == "short" and weekly in ("mild_up", "strong_up"):
        risks.append("Weekly trend is up — shorting into strength")
    if vol == "expanded":
        risks.append("Expanded volatility — wider stops needed, smaller size")
    if news_sent < -0.3 and direction == "long":
        risks.append("Negative news sentiment opposes buy direction")
    elif news_sent > 0.3 and direction == "short":
        risks.append("Positive news sentiment opposes sell direction")
    if risks:
        lines.append("**Risks**:")
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")

    # Conditions
    conds = c.get("conditions", {})
    if conds:
        lines.append("**Conditions**:\n")
        lines.append("| Condition | Met | Detail |")
        lines.append("|-----------|-----|--------|")
        for k, v in conds.items():
            if isinstance(v, dict):
                met = "Yes" if v.get("met") else "**No**"
                detail = v.get("detail", "")
            else:
                met = "Yes" if v else "**No**"
                detail = ""
            lines.append(f"| {k} | {met} | {detail} |")
        lines.append("")

    lines.append(f"**Reason**: {c.get('reason', '')}\n")

    # Verdict
    signal = c.get("signal", "WATCH")
    if signal == "STRONG":
        lines.append("**Verdict**: HIGH CONVICTION — multiple factors align. Full position size.\n")
    elif signal == "ACTIVE":
        lines.append("**Verdict**: GOOD SETUP — edge is present but not overwhelming. Normal position size.\n")
    elif signal == "WATCH":
        lines.append("**Verdict**: WATCHLIST ONLY — monitor but don't enter until conditions improve.\n")

    lines.append("---\n")


# ── Main ────────────────────────────────────────────────────────────────

def _run_live_scan(config, symbols, now_ist=None, data_override=None,
                   skip_llm=False):
    """Live scan (9:15-15:15): full scanner with time-relevance per strategy.

    This is the original main() logic, extracted into its own function.

    Args:
        data_override: dict {symbol: {"daily": df, "intra": df},
                             "_nifty": {"daily": df, "intra": df}}
        skip_llm: if True, skip LLM, rendering, persistence, portfolio filters
    """
    now_ist = now_ist or datetime.now(IST)

    # Load config for capital
    g = config.get("global", {})
    capital = g.get("capital", 1000000)
    intraday_capital = capital * MAX_INTRADAY_CAPITAL_PCT / 100

    if not data_override:
        print(f"  [LIVE] Full intraday scanner")
        print(f"  Capital: {capital:,.0f} | Intraday allocation: {intraday_capital:,.0f} "
              f"({MAX_INTRADAY_CAPITAL_PCT}%)")

    # Fetch VIX
    if data_override:
        vix_val, vix_regime = data_override.get("_vix", (None, "normal"))
        vix_info = (vix_val, vix_regime)
        vix_scale = vix_position_scale(vix_val) if vix_val else 0.7
        nifty_intra = data_override.get("_nifty", {}).get("intra", pd.DataFrame())
        nifty_daily = data_override.get("_nifty", {}).get("daily", pd.DataFrame())
    else:
        print("  Fetching India VIX...")
        vix_val, vix_regime = fetch_india_vix()
        vix_info = (vix_val, vix_regime)

        # ── Bug Fix #3: VIX fetch failure → conservative 0.7x (not 1.0) ──
        if vix_val:
            vix_scale = vix_position_scale(vix_val)
            print(f"  VIX: {vix_val} ({vix_regime}) | Scale: {vix_scale}x")
        else:
            vix_scale = 0.7  # conservative default on failure
            print(f"  VIX: unavailable — using conservative scale {vix_scale}x")

        # Fetch benchmark
        print("  Fetching benchmark data...")
        nifty_intra = fetch_yf(BENCHMARK, period="5d", interval="5m")
        nifty_daily = fetch_yf(BENCHMARK, period="6mo", interval="1d")

    nifty_ist = compute_vwap(_to_ist(nifty_intra)) if not nifty_intra.empty else pd.DataFrame()
    nifty_new_lows = nifty_making_new_lows(nifty_ist) if not nifty_ist.empty else True
    nifty_regime, beta_scale = detect_nifty_regime(nifty_daily)

    nifty_state = {
        "regime": nifty_regime,
        "new_lows": nifty_new_lows,
        "beta_scale": beta_scale,
        "nifty_ist": nifty_ist,
        "nifty_daily": nifty_daily,
    }
    if not data_override:
        print(f"  Nifty: {nifty_regime.upper()} | Making new lows: {nifty_new_lows}")

    # Classify day type
    day_type_info = classify_day_type(nifty_ist, nifty_daily)

    # Mid-session re-classification if after 11:00
    if now_ist.hour >= 11:
        reclassified = reclassify_day_type(nifty_ist, nifty_daily)
        if reclassified["confidence"] > day_type_info["confidence"]:
            if not data_override:
                print(f"  Day type reclassified: {day_type_info['type']} → {reclassified['type']} "
                      f"(conf: {reclassified['confidence']:.0%})")
            day_type_info = reclassified

    if not data_override:
        print(f"  Day type: {day_type_info['type']} (conf: {day_type_info['confidence']:.0%})")

    # DOW and month period
    dow = now_ist.weekday()
    dow_name = DOW_NAMES.get(dow, "Unknown")
    month_period = classify_month_period(now_ist)
    if not data_override:
        print(f"  DOW: {dow_name} | Month period: {month_period}")

    # ── Bug Fix #2: Daily drawdown enforcement (skip in backtest) ──
    drawdown_breached = False
    loss_velocity_pause = False
    strategy_budget_exceeded = set()
    stopped_today = set()

    if not data_override:
        try:
            from common.db import get_today_realized_pnl
            today_pnl = get_today_realized_pnl(scanner_type="intraday")
            if today_pnl is not None:
                today_pnl_pct = today_pnl / capital * 100
                if today_pnl_pct <= -MAX_DAILY_DRAWDOWN_PCT:
                    drawdown_breached = True
                    print(f"  *** DAILY DRAWDOWN BREACHED: {today_pnl_pct:+.2f}% "
                          f"(limit: -{MAX_DAILY_DRAWDOWN_PCT}%) — skipping new signals ***")
        except Exception:
            pass

    # Institutional flow estimate
    if data_override:
        inst_flow = data_override.get("_inst_flow", "neutral")
        news_data = data_override.get("_news", {})
    else:
        print("  Estimating institutional flow...")
        inst_flow = estimate_institutional_flow()
        print(f"  Institutional flow: {inst_flow}")

        # Fetch news & sentiment
        print("  Fetching news & sentiment...")
        try:
            news_data = get_news_and_sentiment(symbols)
            news_count = sum(1 for s in symbols if news_data.get(s, {}).get("summary"))
            print(f"  News: {news_count} stocks with headlines | Market context: {'Yes' if news_data.get('_market') else 'No'}")
        except Exception as e:
            print(f"  [WARN] News fetch failed: {e}")
            news_data = {}

    nifty_state["institutional_flow"] = inst_flow

    # Adjust VIX scale if net_selling
    if inst_flow == "net_selling":
        vix_scale = max(0, vix_scale - 0.15)
        if not data_override:
            print(f"  VIX scale reduced to {vix_scale:.2f}x (institutional net selling)")

    if not data_override:
        # P&L velocity circuit breaker
        try:
            from common.db import get_today_trades
            recent_trades = get_today_trades(scanner_type="intraday")
            if recent_trades and len(recent_trades) >= 3:
                losses = [t for t in recent_trades if t.get("pnl", 0) < 0]
                if len(losses) >= 3:
                    last_3_losses = sorted(losses, key=lambda t: t.get("closed_at", ""))[-3:]
                    if len(last_3_losses) == 3:
                        try:
                            first_ts = pd.Timestamp(last_3_losses[0].get("closed_at", ""))
                            last_ts = pd.Timestamp(last_3_losses[-1].get("closed_at", ""))
                            if (last_ts - first_ts).total_seconds() <= 1800:
                                loss_velocity_pause = True
                                print("  *** P&L VELOCITY BREAKER: 3 losses in 30 min — pausing 30 min ***")
                        except Exception:
                            pass
        except Exception:
            pass

        # Per-strategy daily loss budget
        try:
            from common.db import get_today_trades
            today_trades = get_today_trades(scanner_type="intraday")
            if today_trades:
                strat_pnl = {}
                for t in today_trades:
                    s = t.get("strategy", "")
                    strat_pnl[s] = strat_pnl.get(s, 0) + t.get("pnl", 0)
                for strat, pnl in strat_pnl.items():
                    budget = STRATEGY_DAILY_LOSS_BUDGET.get(strat, 0.5)
                    if capital > 0 and pnl / capital * 100 <= -budget:
                        strategy_budget_exceeded.add(strat)
                        print(f"  Strategy budget exceeded: {strat} ({pnl/capital*100:+.2f}% vs -{budget}%)")
        except Exception:
            pass

        # Repeat-entry guard
        try:
            from common.db import get_today_trades
            today_trades = get_today_trades(scanner_type="intraday")
            if today_trades:
                for t in today_trades:
                    if t.get("exit_reason") == "stop_hit":
                        stopped_today.add((t.get("symbol", ""), t.get("strategy", "")))
        except Exception:
            pass

    # Fetch sector indices
    sectors = set(cfg["sector"] for cfg in TICKERS.values() if cfg.get("sector"))
    sector_data = {}
    if data_override:
        for sec in sectors:
            sector_data[sec] = data_override.get(sec, {}).get("daily", pd.DataFrame())
    else:
        print("  Fetching sector indices...")
        for sec in sectors:
            sector_data[sec] = fetch_yf(sec, period="5d", interval="1d")

    # Fetch all ticker data
    all_data = {}
    if data_override:
        for sym in symbols:
            all_data[sym] = {
                "intra": data_override.get(sym, {}).get("intra", pd.DataFrame()),
                "daily": data_override.get(sym, {}).get("daily", pd.DataFrame()),
            }
    else:
        for sym in symbols:
            print(f"  Fetching {sym}...")
            all_data[sym] = {
                "intra": fetch_yf(sym, period="5d", interval="5m"),
                "daily": fetch_yf(sym, period="6mo", interval="1d"),
            }

    # Evaluate signals (skip if drawdown or velocity breached)
    print("  Evaluating intraday signals...")
    all_candidates = []

    if not drawdown_breached and not loss_velocity_pause:
        for sym in symbols:
            d = all_data.get(sym, {"intra": pd.DataFrame(), "daily": pd.DataFrame()})
            dow_month_stats = compute_dow_month_stats(d["daily"]) if not d["daily"].empty else {}

            candidates = evaluate_symbol(
                sym, d["intra"], d["daily"], nifty_state, vix_info,
                day_type_info, dow_month_stats, sector_data,
                news_data=news_data, now_ist=now_ist,
                skip_earnings_check=bool(data_override),
            )

            # Apply per-strategy loss budget and repeat-entry guard
            filtered = []
            for c in candidates:
                strat = c.get("strategy", "")
                if strat in strategy_budget_exceeded:
                    c["signal"] = "AVOID"
                    c["signal_reason"] = f"Strategy {strat} daily loss budget exceeded"
                if (sym, strat) in stopped_today:
                    c["signal"] = "AVOID"
                    c["signal_reason"] = f"Already stopped out on {strat} today"
                filtered.append(c)
            all_candidates.extend(filtered)

    if not data_override:
        print(f"  Total candidates: {len(all_candidates)}")

    # Rank signals
    all_candidates = rank_signals(all_candidates)

    # In backtest mode, return candidates early (skip portfolio filters, persistence, LLM)
    if skip_llm:
        return all_candidates

    # ── Portfolio risk filters ──

    # Correlation clusters
    print("  Computing correlation clusters...")
    daily_data_dict = {sym: all_data[sym]["daily"] for sym in symbols
                       if not all_data[sym]["daily"].empty}
    corr_clusters = compute_correlation_clusters(daily_data_dict)

    sym_to_cluster = {}
    for cid, syms in corr_clusters.items():
        for sym in syms:
            sym_to_cluster[sym] = cid

    # Apply cluster limit (max 2 from same cluster among STRONG/ACTIVE)
    cluster_counts = {}
    for c in all_candidates:
        if c.get("signal") in ("STRONG", "ACTIVE"):
            cid = sym_to_cluster.get(c["symbol"])
            if cid is not None:
                cluster_counts[cid] = cluster_counts.get(cid, 0) + 1
                if cluster_counts[cid] > 2:
                    c["signal"] = "WATCH"
                    c["signal_reason"] = "Correlation cluster limit"

    # Sector concentration limit
    sector_counts = {}
    for c in all_candidates:
        if c.get("signal") in ("STRONG", "ACTIVE"):
            sec = c.get("sector", "")
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
            if sector_counts[sec] > MAX_SECTOR_EXPOSURE:
                c["signal"] = "WATCH"
                c["signal_reason"] = f"Sector limit ({sec})"

    # ── Net direction cap ──
    direction_counts = {"long": 0, "short": 0}
    for c in all_candidates:
        if c.get("signal") in ("STRONG", "ACTIVE"):
            d = c.get("direction", "long")
            direction_counts[d] = direction_counts.get(d, 0) + 1
            if direction_counts[d] > MAX_SAME_DIRECTION:
                c["signal"] = "WATCH"
                c["signal_reason"] = f"Direction cap (max {MAX_SAME_DIRECTION} {d})"

    # Position limit
    active_count = 0
    for c in all_candidates:
        if c.get("signal") in ("STRONG", "ACTIVE"):
            active_count += 1
            if active_count > MAX_INTRADAY_POSITIONS:
                c["signal"] = "WATCH"
                c["signal_reason"] = f"Position limit (max {MAX_INTRADAY_POSITIONS})"

    # Position sizing for active signals — with per-stock beta
    bench_daily = nifty_daily
    for c in all_candidates:
        if c.get("signal") in ("STRONG", "ACTIVE"):
            wr = c["score"]
            rr = c["rr_ratio"] if c["rr_ratio"] > 0 else 1.5
            kelly = max(0, (wr * rr - (1 - wr)) / rr) * 0.5  # half-Kelly
            size_mult = c.get("size_multiplier", 1.0)

            sym = c["symbol"]
            sym_daily = all_data.get(sym, {}).get("daily", pd.DataFrame())
            stock_beta = 1.0
            if not sym_daily.empty and not bench_daily.empty:
                try:
                    stock_beta = compute_beta(sym_daily, bench_daily)
                    if np.isnan(stock_beta):
                        stock_beta = 1.0
                except Exception:
                    stock_beta = 1.0
            individual_beta_scale = compute_individual_beta_scale(stock_beta)

            pos_size = compute_position_size(
                capital=intraday_capital * size_mult,
                kelly_fraction=max(kelly, 0.05),
                entry_price=c["entry_price"],
                stop_pct=c["stop_pct"],
                vix_scale=vix_scale,
                beta_scale=individual_beta_scale,
            )
            c["recommended_qty"] = pos_size["quantity"]
            c["capital_allocated"] = pos_size["capital_allocated"]
            c["capital_at_risk"] = pos_size["capital_at_risk"]
            c["risk_pct"] = pos_size["risk_pct"]
            c["stock_beta"] = round(stock_beta, 2)

    # ── Supabase persistence ──
    strong_signals = [c for c in all_candidates if c.get("signal") in ("STRONG", "ACTIVE")]

    supa_logged = 0
    try:
        from common.db import log_signal_supa, log_scan_run
        for c in strong_signals:
            log_signal_supa(
                candidate=c,
                vix_val=vix_val,
                nifty_regime=nifty_regime,
                scanner_type="intraday",
            )
            supa_logged += 1
        if supa_logged:
            print(f"  Logged {supa_logged} signal(s) to Supabase")
    except Exception as e:
        print(f"  [WARN] Supabase signal logging failed: {e}")

    # Portfolio metrics
    portfolio_metrics = None
    try:
        from common.db import get_portfolio_metrics_supa
        portfolio_metrics = get_portfolio_metrics_supa(days=30, scanner_type="intraday")
    except Exception:
        pass

    # AI advisory (with time-relevance context)
    print("  Generating AI advisory...")
    ai_context = build_intraday_context(
        all_candidates, nifty_state, vix_info, day_type_info, dow_name, month_period,
        news_data=news_data,
    )
    ai_text = get_intraday_advisory(ai_context, config)

    # Also generate educational LLM explanation for top setups
    top_candidates = [c for c in all_candidates if c.get("signal") in ("STRONG", "ACTIVE")][:3]
    if top_candidates:
        market_context = {
            "nifty_regime": nifty_regime,
            "vix_val": vix_val,
            "vix_regime": vix_regime,
            "inst_flow": inst_flow,
            "day_type": day_type_info.get("type"),
            "market_news": (news_data or {}).get("_market", ""),
        }
        edu_text = generate_llm_explanation(top_candidates, "live", market_context)
        if edu_text:
            ai_text = (ai_text or "") + "\n\n--- EDUCATIONAL ---\n" + edu_text

    # Write report
    report_path, report_content = write_intraday_report(
        all_candidates, nifty_state, vix_info, day_type_info,
        dow_name, month_period, ai_text,
    )
    print(f"  Report saved: {report_path}")

    # Log scan run to Supabase
    try:
        from common.db import log_scan_run
        strong_n = sum(1 for c in all_candidates if c.get("signal") == "STRONG")
        active_n = sum(1 for c in all_candidates if c.get("signal") == "ACTIVE")
        log_scan_run(
            scanner_type="intraday",
            vix_val=vix_val,
            vix_regime=vix_regime,
            nifty_regime=nifty_regime,
            day_type=day_type_info.get("type"),
            dow=dow_name,
            month_period=month_period,
            total_candidates=len(all_candidates),
            strong_count=strong_n,
            active_count=active_n,
            report_markdown=report_content,
            ai_advisory=ai_text,
        )
    except Exception:
        pass

    # Render dashboard (console = summary only, details are in the report)
    dashboard = render_intraday_dashboard(
        all_candidates, nifty_state, vix_info, day_type_info,
        dow_name, month_period, None, portfolio_metrics,
    )
    print()
    print(dashboard)

    # Summary
    strong_n = sum(1 for c in all_candidates if c.get("signal") == "STRONG")
    active_n = sum(1 for c in all_candidates if c.get("signal") == "ACTIVE")
    watch_n = sum(1 for c in all_candidates if c.get("signal") == "WATCH")
    print(f"\n  Summary: {strong_n} STRONG | {active_n} ACTIVE | {watch_n} WATCH | "
          f"{len(all_candidates)} total candidates")


def main():
    parser = argparse.ArgumentParser(description="Intraday Scanner (Time-Aware)")
    parser.add_argument("--force", action="store_true",
                        help="Force LIVE mode anytime (for testing)")
    parser.add_argument("--manage", action="store_true",
                        help="Position management mode only")
    args = parser.parse_args()

    now_ist = datetime.now(IST)
    phase = "live" if args.force else detect_market_phase(now_ist)
    symbols = list(TICKERS.keys())

    print(f"\n  Intraday Scanner - {now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"  Phase: {phase.upper()} | Tickers: {len(TICKERS)} | Strategies: {len(STRATEGY_FN_MAP)}")

    # Weekend check (applies to all phases)
    if now_ist.weekday() >= 5 and not args.force:
        print("  Market closed (weekend). Use --force to override.")
        return

    # Load config
    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}

    # Dispatch based on phase
    if phase == "pre_market":
        run_pre_market_scan(config, symbols)
    elif phase == "pre_live":
        run_pre_live_scan(config, symbols)
    elif phase == "live":
        _run_live_scan(config, symbols)
    elif phase == "post_market":
        run_post_market_scan(config, symbols)


if __name__ == "__main__":
    main()
