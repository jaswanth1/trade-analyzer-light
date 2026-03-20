"""Phase scan functions for the intraday scanner.

Extracted from scanner.py — contains pre-market, pre-live, post-market,
and live scan functions plus their rendering helpers.
"""

import warnings
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
import yfinance as yf
from zoneinfo import ZoneInfo

from common.data import fetch_yf, fetch_bulk, fetch_bulk_single, BENCHMARK, PROJECT_ROOT, CONFIG_PATH, load_universe_for_tier

TICKERS = load_universe_for_tier("intraday")
from common.indicators import compute_atr, compute_beta, compute_vwap, _to_ist, classify_gaps
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
from common.display import fmt, box_top, box_mid, box_bot, box_line, W
from intraday.features import compute_ema, compute_rsi, compute_macd
from intraday.regime import (
    classify_day_type, reclassify_day_type, classify_symbol_regime,
    classify_month_period, compute_dow_month_stats,
    get_eligible_strategies, DOW_NAMES,
)
from intraday.explanations import (
    generate_setup_explanation, generate_scenario_explanation,
    generate_llm_explanation, _compute_stock_profile, _format_rupee,
    _action_label, STRATEGY_DESCRIPTIONS,
)
from intraday.scoring import evaluate_symbol, rank_signals, compute_time_relevance
from intraday.output import (
    build_intraday_context, get_intraday_advisory,
    render_intraday_dashboard, write_intraday_report,
    INTRADAY_AI_SYSTEM_PROMPT,
)

# ── Constants ────────────────────────────────────────────────────────────

IST = ZoneInfo("Asia/Kolkata")
MAX_INTRADAY_POSITIONS = 5
MAX_INTRADAY_CAPITAL_PCT = 50.0
MAX_SECTOR_EXPOSURE = 2
MAX_DAILY_DRAWDOWN_PCT = 2.0
LONG_ONLY = True
INTRADAY_REPORT_DIR = PROJECT_ROOT / "intraday" / "reports"

STRATEGY_DAILY_LOSS_BUDGET = {
    "orb": 0.5,
    "pullback": 0.5,
    "compression": 0.3,
    "mean_revert": 0.3,
    "swing": 0.5,
    "mlr": 0.5,
}


# ── Gap Scenarios ────────────────────────────────────────────────────────

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


# ── Pre-Market Scan (before 9:00) ────────────────────────────────────────

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

    nifty_regime, beta_scale, _regime_strength = detect_nifty_regime(nifty_daily)
    if not data_override:
        print(f"  Nifty regime: {nifty_regime.upper()}")

    # DOW / month period
    dow = now_ist.weekday()
    dow_name = DOW_NAMES.get(dow, "Unknown")
    month_period = classify_month_period(now_ist)
    if not data_override:
        print(f"  DOW: {dow_name} | Period: {month_period}")

    # Fetch daily data for all tickers (parallel)
    if not data_override:
        print(f"  Fetching {len(symbols)} tickers in parallel...")
        _daily_bulk = fetch_bulk_single(symbols, "6mo", "1d", max_workers=10, label="PreMkt")
    else:
        _daily_bulk = {sym: data_override.get(sym, {}).get("daily", pd.DataFrame()) for sym in symbols}

    all_setups = []
    for sym in symbols:
        daily_df = _daily_bulk.get(sym, pd.DataFrame())
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
    nifty_regime, _, _regime_strength = detect_nifty_regime(nifty_daily)
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

    nifty_regime, _, _regime_strength = detect_nifty_regime(nifty_daily)
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

    # ── Bulk fetch for session review + tomorrow watchlist ──
    if not data_override:
        print(f"  Fetching {len(symbols)} tickers in parallel...")
        _post_bulk = fetch_bulk(symbols, {
            "intra": ("5d", "5m"),
            "daily": ("6mo", "1d"),
        }, max_workers=10, label="PostMkt")
    else:
        _post_bulk = {
            sym: {
                "intra": data_override.get(sym, {}).get("intra", pd.DataFrame()),
                "daily": data_override.get(sym, {}).get("daily", pd.DataFrame()),
            }
            for sym in symbols
        }

    # ── Section 1: Session Review ──
    session_summaries = []
    for sym in symbols:
        intra_df = _post_bulk.get(sym, {}).get("intra", pd.DataFrame())
        daily_df = _post_bulk.get(sym, {}).get("daily", pd.DataFrame())
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
        daily_df = _post_bulk.get(sym, {}).get("daily", pd.DataFrame())
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


# ── Live Scan (9:15-15:15) ──────────────────────────────────────────────

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
    nifty_regime, beta_scale, regime_strength = detect_nifty_regime(nifty_daily)

    nifty_state = {
        "regime": nifty_regime,
        "regime_strength": regime_strength,
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

    # Compute continuous market context scores for multi-factor scoring
    from common.market import compute_market_context_scores
    market_ctx = compute_market_context_scores(
        nifty_daily, vix_val, inst_flow, regime_strength
    )

    # Detect regime transition (bear→range, range→bull, etc.)
    from intraday.regime import detect_regime_transition
    transition = detect_regime_transition(nifty_ist, nifty_daily)
    if transition["transition"]:
        # Apply transition adjustment to regime score
        market_ctx["regime_score"] = round(
            max(0.0, min(1.0,
                market_ctx["regime_score"] + transition["regime_score_adjustment"])),
            3,
        )
        market_ctx["regime_transition"] = transition["transition"]
        market_ctx["transition_strength"] = transition["transition_strength"]
        if not data_override:
            print(f"  Regime transition: {transition['transition']} "
                  f"(strength: {transition['transition_strength']:.2f}, "
                  f"regime_score adjusted to {market_ctx['regime_score']:.2f})")

    nifty_state["market_ctx"] = market_ctx

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

    # Fetch sector indices (parallel)
    sectors = set(cfg["sector"] for cfg in TICKERS.values() if cfg.get("sector"))
    if data_override:
        sector_data = {sec: data_override.get(sec, {}).get("daily", pd.DataFrame()) for sec in sectors}
    else:
        print(f"  Fetching {len(sectors)} sector indices in parallel...")
        sector_data = fetch_bulk_single(list(sectors), "5d", "1d", max_workers=8, label="Sectors")

    # Fetch all ticker data (parallel)
    if data_override:
        all_data = {
            sym: {
                "intra": data_override.get(sym, {}).get("intra", pd.DataFrame()),
                "daily": data_override.get(sym, {}).get("daily", pd.DataFrame()),
            }
            for sym in symbols
        }
    else:
        print(f"  Fetching {len(symbols)} tickers in parallel...")
        all_data = fetch_bulk(symbols, {
            "intra": ("5d", "5m"),
            "daily": ("6mo", "1d"),
        }, max_workers=10, label="Live")

    # Evaluate signals (skip if drawdown or velocity breached)
    print("  Evaluating intraday signals...")
    print(f"  [DEBUG] Circuit breakers: drawdown_breached={drawdown_breached}, "
          f"loss_velocity_pause={loss_velocity_pause}")
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
    else:
        print(f"  [DEBUG] SKIPPING all symbol evaluation — circuit breaker active")

    if not data_override:
        print(f"  Total candidates: {len(all_candidates)}")

    # Rank signals
    all_candidates = rank_signals(all_candidates, nifty_regime=nifty_regime)

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

    # Position sizing for ALL non-AVOID signals — with per-stock beta.
    # Institutional approach: sizing IS the risk control, not signal filtering.
    # STRONG/ACTIVE get full sizing; WATCH gets reduced sizing proportional
    # to composite score, so traders see opportunity with appropriate size.
    bench_daily = nifty_daily
    for c in all_candidates:
        if c.get("signal") == "AVOID":
            c["recommended_qty"] = 0
            c["capital_allocated"] = 0
            c["capital_at_risk"] = 0
            c["risk_pct"] = 0
            c["stock_beta"] = 1.0
            continue

        wr = c["score"]
        rr = c["rr_ratio"] if c["rr_ratio"] > 0 else 1.5
        kelly = max(0, (wr * rr - (1 - wr)) / rr) * 0.5  # half-Kelly
        size_mult = c.get("size_multiplier", 1.0)

        # WATCH tier: scale down sizing proportional to composite score
        # Score 0.52+ (ACTIVE) = full size, score 0.38 (WATCH floor) = ~25% size
        if c.get("signal") == "WATCH":
            score_scale = max(0.15, (wr - 0.30) / 0.30)  # 0.15 at 0.34, 0.73 at 0.52
            size_mult *= score_scale

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
