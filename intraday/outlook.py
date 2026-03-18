"""
Market Outlook Predictor — session forecast for Indian equities.

Time-aware modes:
  PRE_MARKET  (before 9:15)  — forecast for TODAY's upcoming session
  LIVE        (9:15-15:30)   — forecast for the REST of today's session
  POST_MARKET (after 15:30)  — forecast for the NEXT trading session

Synthesizes regime, breadth, sector rotation, historical patterns, and
seasonality into a single forecast with LLM-generated plain-English outlook.

Usage:
    python -m intraday.outlook
"""

import argparse
import textwrap
import warnings
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

from common.data import fetch_yf, BENCHMARK, INTRADAY_REPORT_DIR, load_universe_for_tier

TICKERS = load_universe_for_tier("intraday")
from common.display import box_top, box_mid, box_bot, box_line, W, fmt
from common.indicators import compute_atr, classify_gaps
from common.llm import call_llm
from common.market import fetch_india_vix, detect_nifty_regime, estimate_institutional_flow
from common.news import get_news_and_sentiment
from intraday.features import compute_ema, compute_rsi, compute_macd, compute_intraday_levels
from intraday.regime import (
    classify_day_type, reclassify_day_type, classify_symbol_regime,
    compute_dow_month_stats, classify_month_period, DOW_NAMES,
)

warnings.filterwarnings("ignore")

IST = ZoneInfo("Asia/Kolkata")

# Sector index tickers used in TICKERS config
SECTOR_INDICES = sorted({meta["sector"] for meta in TICKERS.values()})

# Friendly names for sector indices
SECTOR_NAMES = {
    "^CNXFIN": "FIN",
    "^CNXENERGY": "ENERGY",
    "^CNXMETAL": "METAL",
    "^CNXPSE": "PSE",
    "^CNXINFRA": "INFRA",
    "^CNXIT": "IT",
    "^CNXAUTO": "AUTO",
    "^CNXFMCG": "FMCG",
    "^CNXREALTY": "REALTY",
    "^CNXPHARMA": "PHARMA",
}


# ── Mode Detection ───────────────────────────────────────────────────────

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)


def _detect_mode():
    """Detect outlook mode based on current IST time.

    Returns: "pre_market" | "live" | "post_market"
    """
    now = datetime.now(IST)
    t = now.time()
    if t < MARKET_OPEN:
        return "pre_market"
    elif t <= MARKET_CLOSE:
        return "live"
    else:
        return "post_market"


def _next_trading_day(from_date):
    """Return the next weekday after from_date."""
    d = from_date + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _get_target_session(mode):
    """Return (target_date, session_label) for the forecast.

    pre_market  → today, "Today's Session: Tuesday 2026-03-17"
    live        → today, "Rest of Session: Tuesday 2026-03-17"
    post_market → next trading day, "Next Session: Wednesday 2026-03-18"
    """
    now = datetime.now(IST)
    today = now.date()

    if mode == "pre_market":
        return today, f"Today's Session: {today.strftime('%A %Y-%m-%d')}"
    elif mode == "live":
        return today, f"Rest of Session: {today.strftime('%A %Y-%m-%d')}"
    else:  # post_market
        nxt = _next_trading_day(today)
        return nxt, f"Next Session: {nxt.strftime('%A %Y-%m-%d')}"


# ── Data Fetching ────────────────────────────────────────────────────────

def _fetch_all_data():
    """Fetch Nifty + all 34 stocks + sector indices (daily + intraday).

    Returns dict with keys: nifty_daily, nifty_intra, stocks, sectors.
    """
    print("  Fetching Nifty data...")
    nifty_daily = fetch_yf(BENCHMARK, period="1y", interval="1d")
    nifty_intra = fetch_yf(BENCHMARK, period="5d", interval="5m")

    print(f"  Fetching {len(TICKERS)} stocks (daily)...")
    stocks = {}
    for sym in TICKERS:
        daily = fetch_yf(sym, period="6mo", interval="1d")
        if not daily.empty:
            stocks[sym] = {"daily": daily}

    print(f"  Fetching {len(SECTOR_INDICES)} sector indices...")
    sectors = {}
    for idx in SECTOR_INDICES:
        df = fetch_yf(idx, period="1mo", interval="1d")
        if not df.empty:
            sectors[idx] = df

    return {
        "nifty_daily": nifty_daily,
        "nifty_intra": nifty_intra,
        "stocks": stocks,
        "sectors": sectors,
    }


# ── Market Structure ─────────────────────────────────────────────────────

def _analyze_market_structure(data, mode="post_market"):
    """VIX, regime, day type, flow, Nifty technicals."""
    nifty_daily = data["nifty_daily"]
    nifty_intra = data["nifty_intra"]

    # Convert intraday to IST for day-type classification
    nifty_ist = nifty_intra.copy()
    if not nifty_ist.empty:
        if nifty_ist.index.tz is None:
            nifty_ist.index = nifty_ist.index.tz_localize("UTC")
        nifty_ist.index = nifty_ist.index.tz_convert("Asia/Kolkata")

    # VIX
    vix_val, vix_regime = fetch_india_vix()

    # Regime
    regime, beta_scale, strength = detect_nifty_regime(nifty_daily)

    # Day type (uses intraday bars)
    if not nifty_ist.empty and len(nifty_daily) >= 14:
        today = nifty_ist.index[-1].date()
        today_bars = nifty_ist[nifty_ist.index.date == today]
        if len(today_bars) >= 12:
            day_type = reclassify_day_type(nifty_ist, nifty_daily)
        elif len(today_bars) >= 6:
            day_type = classify_day_type(nifty_ist, nifty_daily)
        else:
            day_type = {"type": "range_bound", "confidence": 0.3, "detail": "Pre-market"}
    else:
        day_type = {"type": "range_bound", "confidence": 0.3, "detail": "No intraday data"}

    # Institutional flow
    flow = estimate_institutional_flow()

    # Nifty technicals
    close = nifty_daily["Close"]
    price = float(close.iloc[-1]) if not nifty_daily.empty else 0

    ema20 = float(compute_ema(close, 20).iloc[-1]) if len(close) >= 20 else None
    ema50 = float(compute_ema(close, 50).iloc[-1]) if len(close) >= 50 else None
    rsi = float(compute_rsi(close).iloc[-1]) if len(close) >= 14 else None
    macd_data = compute_macd(close)
    macd_hist = float(macd_data["histogram"].iloc[-1]) if len(close) >= 26 else None
    macd_signal = "bullish" if macd_hist and macd_hist > 0 else "bearish"
    atr = compute_atr(nifty_daily) if len(nifty_daily) >= 14 else None

    # Pivot levels from daily
    levels = compute_intraday_levels(nifty_daily)

    result = {
        "price": price,
        "vix_val": vix_val,
        "vix_regime": vix_regime,
        "regime": regime,
        "regime_strength": strength,
        "day_type": day_type["type"],
        "day_type_confidence": day_type["confidence"],
        "flow": flow,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "atr": atr,
        "levels": levels,
    }

    # Live mode: add intraday session progress
    if mode == "live" and not nifty_ist.empty:
        today = nifty_ist.index[-1].date()
        today_bars = nifty_ist[nifty_ist.index.date == today]
        if len(today_bars) >= 2:
            session_open = float(today_bars["Open"].iloc[0])
            session_high = float(today_bars["High"].max())
            session_low = float(today_bars["Low"].min())
            session_last = float(today_bars["Close"].iloc[-1])
            result["session"] = {
                "open": session_open,
                "high": session_high,
                "low": session_low,
                "last": session_last,
                "change_pct": round((session_last / session_open - 1) * 100, 2),
                "range_pct": round((session_high - session_low) / session_open * 100, 2),
                "bars_elapsed": len(today_bars),
                "last_bar_time": today_bars.index[-1].strftime("%H:%M"),
            }

    return result


# ── Breadth Analysis ─────────────────────────────────────────────────────

def _analyze_breadth(data, nifty_daily):
    """% above EMA20, A/D ratio, trend distribution across 34 stocks."""
    stocks = data["stocks"]
    if not stocks:
        return {"above_ema20_pct": 0, "ad_ratio": 0, "trend_dist": {}, "n": 0}

    above_ema20 = 0
    advances = 0
    declines = 0
    trend_counts = {"strong_up": 0, "mild_up": 0, "sideways": 0, "mild_down": 0, "strong_down": 0}
    n = 0

    for sym, sdata in stocks.items():
        daily = sdata["daily"]
        if daily.empty or len(daily) < 20:
            continue
        n += 1
        close = daily["Close"]
        price = float(close.iloc[-1])
        ema20 = float(compute_ema(close, 20).iloc[-1])

        if price > ema20:
            above_ema20 += 1

        # 1-day return for A/D
        if len(close) >= 2:
            ret = float(close.iloc[-1]) / float(close.iloc[-2]) - 1
            if ret > 0:
                advances += 1
            elif ret < 0:
                declines += 1

        # Trend classification (lightweight — daily only, no intraday)
        regime = classify_symbol_regime(daily, pd.DataFrame(), nifty_daily)
        trend = regime.get("trend", "sideways")
        if trend in trend_counts:
            trend_counts[trend] += 1

    ad_ratio = round(advances / max(declines, 1), 2)
    above_pct = round(above_ema20 / max(n, 1) * 100, 1)

    # Summarize trend distribution as percentages
    trend_dist = {}
    for k, v in trend_counts.items():
        trend_dist[k] = round(v / max(n, 1) * 100, 1)

    return {
        "above_ema20_pct": above_pct,
        "ad_ratio": ad_ratio,
        "advances": advances,
        "declines": declines,
        "trend_dist": trend_dist,
        "n": n,
    }


# ── Sector Rotation ──────────────────────────────────────────────────────

def _analyze_sector_rotation(data):
    """Per-sector 1d/5d returns, leader stock, relative performance."""
    sectors = data["sectors"]
    stocks = data["stocks"]
    results = []

    for idx_sym, df in sectors.items():
        if df.empty or len(df) < 5:
            continue
        close = df["Close"]
        ret_1d = (float(close.iloc[-1]) / float(close.iloc[-2]) - 1) * 100 if len(close) >= 2 else 0
        ret_5d = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1) * 100 if len(close) >= 6 else 0

        # Find best-performing stock in this sector (1d return)
        leader = None
        leader_ret = -999
        for sym, meta in TICKERS.items():
            if meta["sector"] != idx_sym:
                continue
            if sym not in stocks or stocks[sym]["daily"].empty or len(stocks[sym]["daily"]) < 2:
                continue
            sc = stocks[sym]["daily"]["Close"]
            sr = (float(sc.iloc[-1]) / float(sc.iloc[-2]) - 1) * 100
            if sr > leader_ret:
                leader_ret = sr
                leader = meta["name"]

        name = SECTOR_NAMES.get(idx_sym, idx_sym)
        results.append({
            "sector": name,
            "index": idx_sym,
            "ret_1d": round(ret_1d, 2),
            "ret_5d": round(ret_5d, 2),
            "leader": leader,
            "leader_ret": round(leader_ret, 2) if leader else None,
        })

    # Sort by 1d return
    results.sort(key=lambda x: x["ret_1d"], reverse=True)
    return results


# ── Historical Pattern Matching ──────────────────────────────────────────

def _match_historical_patterns(data, mkt, target_date=None):
    """Filter Nifty daily for similar regime+VIX days, compute next-day stats.

    Also computes DOW/month seasonality for the target_date.
    """
    nifty_daily = data["nifty_daily"]
    if nifty_daily.empty or len(nifty_daily) < 60:
        return {"regime_match": {}, "seasonality": {}}

    if target_date is None:
        target_date = _next_trading_day(datetime.now(IST).date())

    target_dow = DOW_NAMES.get(target_date.weekday(), "Unknown")
    target_month_period = classify_month_period(
        datetime(target_date.year, target_date.month, target_date.day)
    )

    # DOW + month seasonality from Nifty daily
    dow_stats = compute_dow_month_stats(nifty_daily)
    dow_entry = dow_stats.get(target_dow, {})
    period_entry = dow_entry.get(target_month_period, dow_entry.get("all", {}))

    seasonality = {
        "dow": target_dow,
        "month_period": target_month_period,
        "win_rate": period_entry.get("win_rate"),
        "avg_return": period_entry.get("avg_return"),
        "n": period_entry.get("n"),
    }

    # Regime-based pattern matching
    # Find historical days with similar regime + VIX level, compute next-day return
    close = nifty_daily["Close"]
    current_regime = mkt["regime"]
    current_vix = mkt["vix_val"]

    df = nifty_daily.copy()
    df["ret_1d"] = close.pct_change() * 100
    df["next_ret"] = df["ret_1d"].shift(-1)
    df = df.dropna(subset=["next_ret"])

    if len(df) < 30:
        return {"regime_match": {}, "seasonality": seasonality}

    # Classify each historical day's regime (simplified: above/below SMA20)
    sma20 = close.rolling(20).mean()
    df["above_sma20"] = close > sma20

    # Current state
    current_above = float(close.iloc[-1]) > float(sma20.iloc[-1]) if len(sma20.dropna()) > 0 else True

    # Filter similar days: same side of SMA20
    similar = df[df["above_sma20"] == current_above]

    # Further filter by VIX proximity if available
    # (We don't have historical VIX per day in this data, so just use regime match)

    regime_match = {}
    if len(similar) >= 5:
        next_rets = similar["next_ret"]
        up_pct = round((next_rets > 0).sum() / len(next_rets) * 100, 1)
        regime_match = {
            "up_pct": up_pct,
            "avg_return": round(float(next_rets.mean()), 3),
            "median_return": round(float(next_rets.median()), 3),
            "n": len(similar),
        }

    return {
        "regime_match": regime_match,
        "seasonality": seasonality,
    }


# ── Forecast Builder ─────────────────────────────────────────────────────

def _build_forecast(mkt, breadth, sectors, patterns, mode="post_market"):
    """Heuristic synthesis: gap/direction, day type, strategies, risk level.

    In live mode: predicts rest-of-day direction instead of gap direction.
    In pre_market/post_market: predicts gap direction for the target session.
    """
    # Directional signals (shared across modes)
    dir_signals = []

    # Regime signal
    if mkt["regime"] == "bullish":
        dir_signals.append(0.3)
    elif mkt["regime"] == "bearish":
        dir_signals.append(-0.3)
    else:
        dir_signals.append(0.0)

    # Breadth signal
    if breadth["above_ema20_pct"] > 60:
        dir_signals.append(0.2)
    elif breadth["above_ema20_pct"] < 40:
        dir_signals.append(-0.2)
    else:
        dir_signals.append(0.0)

    # Flow signal
    if mkt["flow"] == "net_buying":
        dir_signals.append(0.2)
    elif mkt["flow"] == "net_selling":
        dir_signals.append(-0.2)
    else:
        dir_signals.append(0.0)

    # Historical pattern signal
    rm = patterns.get("regime_match", {})
    if rm.get("avg_return") is not None:
        dir_signals.append(np.clip(rm["avg_return"] / 0.5, -0.3, 0.3))

    avg_signal = np.mean(dir_signals)

    # Live mode: use intraday momentum + day-type to predict rest-of-day
    if mode == "live":
        session = mkt.get("session", {})
        change = session.get("change_pct", 0)
        # Intraday momentum adds to directional signal
        if change > 0.3:
            dir_signals.append(0.2)
        elif change < -0.3:
            dir_signals.append(-0.2)
        avg_signal = np.mean(dir_signals)

        if avg_signal > 0.15:
            direction = "continuation higher"
        elif avg_signal < -0.15:
            direction = "drift lower"
        else:
            direction = "sideways / choppy"

        # Use actual day type (already classified from live bars)
        likely_day = mkt["day_type"]
    else:
        # Pre-market / post-market: gap prediction
        if avg_signal > 0.15:
            direction = "small gap up"
        elif avg_signal < -0.15:
            direction = "small gap down"
        else:
            direction = "flat / near-zero gap"

        # Day type prediction (based on VIX + regime)
        vix = mkt["vix_val"] or 16
        if vix > 22:
            likely_day = "volatile_two_sided"
        elif vix < 14 and mkt["regime"] in ("bullish",):
            likely_day = "trend_up"
        elif vix < 14 and mkt["regime"] in ("bearish",):
            likely_day = "trend_down"
        else:
            likely_day = "range_bound"

    # Best strategies for predicted day type
    strategy_map = {
        "trend_up": ["ORB", "Pullback", "MLR"],
        "trend_down": ["ORB (short bias)", "Mean-Revert", "MLR"],
        "range_bound": ["Mean-Revert", "Compression", "MLR"],
        "volatile_two_sided": ["Mean-Revert", "MLR"],
        "gap_and_go": ["ORB", "Pullback"],
        "gap_and_fade": ["Mean-Revert", "MLR"],
    }
    strategies = strategy_map.get(likely_day, ["MLR"])

    # Risk level
    risk_factors = 0
    if mkt["vix_regime"] in ("elevated", "stress"):
        risk_factors += 1
    if mkt["regime"] == "bearish":
        risk_factors += 1
    if breadth["above_ema20_pct"] < 35:
        risk_factors += 1
    if mkt["flow"] == "net_selling":
        risk_factors += 1

    risk_levels = {0: "LOW", 1: "MODERATE", 2: "ELEVATED", 3: "HIGH", 4: "VERY HIGH"}
    risk_level = risk_levels.get(risk_factors, "HIGH")

    # Sectors to watch (top 2 leaders)
    watch_sectors = [s["sector"] for s in sectors[:2]] if sectors else []

    return {
        "direction": direction,
        "likely_day_type": likely_day,
        "strategies": strategies,
        "risk_level": risk_level,
        "risk_factors": risk_factors,
        "watch_sectors": watch_sectors,
        "signal_strength": round(abs(avg_signal), 2),
    }


# ── LLM Outlook ──────────────────────────────────────────────────────────

def _call_llm_outlook(mkt, breadth, sectors, patterns, forecast, news_ctx,
                      mode="post_market", session_label=""):
    """LLM prompt with all analysis → plain-English 200-400 word outlook."""
    # Build sector summary
    sector_lines = []
    for s in sectors[:5]:
        sector_lines.append(f"  {s['sector']}: 1d {s['ret_1d']:+.2f}%, 5d {s['ret_5d']:+.2f}%")

    # Build pattern summary
    rm = patterns.get("regime_match", {})
    seas = patterns.get("seasonality", {})
    pattern_text = ""
    if rm:
        pattern_text += f"Similar regime days (N={rm.get('n', 0)}): {rm.get('up_pct', 0)}% up, avg {rm.get('avg_return', 0):+.3f}%\n"
    if seas.get("win_rate") is not None:
        pattern_text += f"{seas['dow']} + {seas['month_period']}: {seas['win_rate']}% win, avg {seas['avg_return']:+.3f}% (N={seas['n']})\n"

    # Mode-specific framing
    if mode == "live":
        session = mkt.get("session", {})
        session_text = (
            f"\nINTRADAY SESSION (so far):\n"
            f"- Open: {session.get('open', 0):,.0f} | High: {session.get('high', 0):,.0f} | "
            f"Low: {session.get('low', 0):,.0f} | Last: {session.get('last', 0):,.0f}\n"
            f"- Change: {session.get('change_pct', 0):+.2f}% | Range: {session.get('range_pct', 0):.2f}%\n"
            f"- Bars: {session.get('bars_elapsed', 0)} | Last bar: {session.get('last_bar_time', 'N/A')}\n"
        )
        time_frame = "the remaining trading hours today"
        task = (
            "Write a 200-400 word outlook for the REST of today's session.\n"
            "1. Assess whether the current intraday trend is likely to continue or reverse.\n"
            "2. Identify key intraday levels (session high/low, VWAP, pivot) that will decide direction.\n"
            "3. Recommend which strategies to use NOW and which to avoid.\n"
            "4. Flag if it's too late for new entries or if opportunities remain.\n"
            "5. Note the primary risk for the rest of the session."
        )
    elif mode == "pre_market":
        session_text = ""
        time_frame = "today's upcoming session"
        task = (
            "Write a 200-400 word outlook for TODAY's session.\n"
            "1. Open with a clear directional view (bullish/bearish/neutral) and conviction level.\n"
            "2. Predict the likely gap direction and opening behavior.\n"
            "3. Specify key Nifty levels to watch (support/resistance).\n"
            "4. Recommend which strategies to prioritize and which sectors to focus on.\n"
            "5. Note the primary risk to your base case."
        )
    else:  # post_market
        session_text = ""
        time_frame = "the next trading session"
        task = (
            "Write a 200-400 word outlook for the next trading session.\n"
            "1. Open with a clear directional view (bullish/bearish/neutral) and conviction level.\n"
            "2. Highlight the 2-3 most important factors driving your view.\n"
            "3. Specify key Nifty levels to watch (support/resistance).\n"
            "4. Recommend which strategies to prioritize and which sectors to focus on.\n"
            "5. Note the primary risk to your base case."
        )

    prompt = f"""You are a senior Indian equity market analyst. Based on the data below, write a market outlook for {time_frame} ({session_label}).

MARKET STRUCTURE:
- Nifty: {mkt['price']:,.0f} | Regime: {mkt['regime'].upper()} (strength {mkt['regime_strength']:.2f})
- VIX: {fmt(mkt['vix_val'])} ({mkt['vix_regime']}) | Flow: {mkt['flow']}
- Day Type: {mkt['day_type']} ({mkt['day_type_confidence'] * 100:.0f}% conf)
- RSI: {fmt(mkt['rsi'], 0)} | MACD: {mkt['macd_signal']} | ATR: {fmt(mkt['atr'])}
{session_text}
BREADTH ({breadth['n']} stocks):
- Above EMA20: {breadth['above_ema20_pct']}% | A/D ratio: {breadth['ad_ratio']}
- Up-trend: {breadth['trend_dist'].get('strong_up', 0) + breadth['trend_dist'].get('mild_up', 0):.0f}% | Down-trend: {breadth['trend_dist'].get('strong_down', 0) + breadth['trend_dist'].get('mild_down', 0):.0f}%

SECTOR ROTATION:
{chr(10).join(sector_lines) if sector_lines else '  No sector data'}

HISTORICAL PATTERNS:
{pattern_text or '  Insufficient data'}

FORECAST:
- Direction: {forecast['direction']} | Day Type: {forecast['likely_day_type']} | Risk: {forecast['risk_level']}
- Strategies: {', '.join(forecast['strategies'])}
- Watch: {', '.join(forecast['watch_sectors']) if forecast['watch_sectors'] else 'N/A'}

NEWS CONTEXT:
{news_ctx or 'No news data available'}

Instructions:
{task}
Keep it practical — this is for an active intraday trader, not a newsletter.
"""

    messages = [{"role": "user", "content": prompt}]
    return call_llm(messages, temperature=0.4)


# ── Terminal Dashboard ───────────────────────────────────────────────────

def _render_dashboard(mkt, breadth, sectors, patterns, forecast, llm_text,
                      mode="post_market", session_label=""):
    """Terminal box-drawing dashboard."""
    lines = []
    lines.append(box_top())
    lines.append(box_line(f"MARKET OUTLOOK — {session_label}"))

    # ── Market Structure ─────────────────────────────────────────────
    lines.append(box_mid())
    lines.append(box_line("MARKET STRUCTURE"))
    vix_str = f"{mkt['vix_val']}" if mkt["vix_val"] else "N/A"
    lines.append(box_line(
        f"Nifty: {mkt['price']:,.0f} | {mkt['regime'].upper()} | "
        f"VIX: {vix_str} ({mkt['vix_regime']}) | Flow: {mkt['flow']}"
    ))
    dt_conf = f"{mkt['day_type_confidence'] * 100:.0f}%"
    rsi_str = f"{mkt['rsi']:.0f}" if mkt["rsi"] else "N/A"
    lines.append(box_line(
        f"Day Type: {mkt['day_type']} ({dt_conf}) | "
        f"RSI: {rsi_str} | MACD: {mkt['macd_signal']}"
    ))

    # ── Live Session Progress (live mode only) ─────────────────────
    session = mkt.get("session")
    if mode == "live" and session:
        lines.append(box_mid())
        lines.append(box_line("SESSION PROGRESS"))
        lines.append(box_line(
            f"Open: {session['open']:,.0f} | High: {session['high']:,.0f} | "
            f"Low: {session['low']:,.0f} | Last: {session['last']:,.0f}"
        ))
        lines.append(box_line(
            f"Change: {session['change_pct']:+.2f}% | Range: {session['range_pct']:.2f}% | "
            f"Bars: {session['bars_elapsed']} | Time: {session['last_bar_time']}"
        ))

    # ── Nifty Levels ─────────────────────────────────────────────────
    lvl = mkt["levels"]
    if lvl:
        lines.append(box_mid())
        lines.append(box_line("NIFTY LEVELS"))
        lines.append(box_line(
            f"R2: {lvl.get('r2', 'N/A'):,.0f} | R1: {lvl.get('r1', 'N/A'):,.0f} | "
            f"Pivot: {lvl.get('pivot', 'N/A'):,.0f}"
        ))
        ema20_str = f"{mkt['ema20']:,.0f}" if mkt["ema20"] else "N/A"
        ema50_str = f"{mkt['ema50']:,.0f}" if mkt["ema50"] else "N/A"
        lines.append(box_line(
            f"S1: {lvl.get('s1', 'N/A'):,.0f} | S2: {lvl.get('s2', 'N/A'):,.0f} | "
            f"EMA20: {ema20_str} | EMA50: {ema50_str}"
        ))

    # ── Breadth ──────────────────────────────────────────────────────
    lines.append(box_mid())
    lines.append(box_line(f"BREADTH ({breadth['n']} stocks)"))
    up_trend = breadth["trend_dist"].get("strong_up", 0) + breadth["trend_dist"].get("mild_up", 0)
    dn_trend = breadth["trend_dist"].get("strong_down", 0) + breadth["trend_dist"].get("mild_down", 0)
    lines.append(box_line(
        f"Above EMA20: {breadth['above_ema20_pct']}% | A/D: {breadth['ad_ratio']} | "
        f"Up-trend: {up_trend:.0f}% | Down-trend: {dn_trend:.0f}%"
    ))

    # ── Sector Rotation ──────────────────────────────────────────────
    lines.append(box_mid())
    lines.append(box_line("SECTOR ROTATION"))
    if sectors:
        leaders = [f"{s['sector']} {s['ret_1d']:+.1f}%" for s in sectors[:3]]
        laggers = [f"{s['sector']} {s['ret_1d']:+.1f}%" for s in sectors[-2:]]
        lines.append(box_line(f"Leading: {' | '.join(leaders)}"))
        lines.append(box_line(f"Lagging: {' | '.join(laggers)}"))
    else:
        lines.append(box_line("No sector data"))

    # ── Historical Patterns ──────────────────────────────────────────
    lines.append(box_mid())
    lines.append(box_line("HISTORICAL PATTERNS"))
    seas = patterns.get("seasonality", {})
    if seas.get("win_rate") is not None:
        lines.append(box_line(
            f"{seas['dow']} + {seas['month_period']}: "
            f"{seas['win_rate']}% win, avg {seas['avg_return']:+.3f}% (N={seas['n']})"
        ))
    rm = patterns.get("regime_match", {})
    if rm:
        lines.append(box_line(
            f"Similar regime (N={rm['n']}): "
            f"{rm['up_pct']}% up next day, avg {rm['avg_return']:+.3f}%"
        ))
    if not seas.get("win_rate") and not rm:
        lines.append(box_line("Insufficient historical data"))

    # ── Forecast ─────────────────────────────────────────────────────
    lines.append(box_mid())
    lines.append(box_line("FORECAST"))
    dir_label = "Bias" if mode == "live" else "Gap"
    lines.append(box_line(
        f"{dir_label}: {forecast['direction']} | "
        f"Day Type: {forecast['likely_day_type']} | "
        f"Risk: {forecast['risk_level']}"
    ))
    lines.append(box_line(
        f"Strategies: {', '.join(forecast['strategies'])} | "
        f"Watch: {', '.join(forecast['watch_sectors']) if forecast['watch_sectors'] else 'N/A'}"
    ))

    # ── AI Outlook ───────────────────────────────────────────────────
    lines.append(box_mid())
    lines.append(box_line("AI OUTLOOK"))
    if llm_text:
        wrapped = textwrap.wrap(llm_text, width=W - 6)
        for wline in wrapped:
            lines.append(box_line(wline))
    else:
        lines.append(box_line("LLM unavailable — see quantitative forecast above"))

    lines.append(box_bot())

    output = "\n".join(lines)
    print(output)
    return output


# ── Report Writer ────────────────────────────────────────────────────────

def _write_report(mkt, breadth, sectors, patterns, forecast, llm_text,
                   mode="post_market", session_label=""):
    """Save markdown report to intraday/reports/outlook_YYYY-MM-DD_HHMM.md."""
    now = datetime.now(IST)

    INTRADAY_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"outlook_{now.strftime('%Y-%m-%d_%H%M')}.md"
    path = INTRADAY_REPORT_DIR / fname

    mode_tag = {"pre_market": "Pre-Market", "live": "Live", "post_market": "Post-Market"}
    lines = []
    lines.append(f"# Market Outlook — {session_label}")
    lines.append(f"*Generated: {now.strftime('%Y-%m-%d %H:%M IST')} | Mode: {mode_tag.get(mode, mode)}*\n")

    # Market Structure
    lines.append("## Market Structure")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Nifty | {mkt['price']:,.2f} |")
    lines.append(f"| Regime | {mkt['regime'].upper()} (strength {mkt['regime_strength']:.2f}) |")
    lines.append(f"| VIX | {fmt(mkt['vix_val'])} ({mkt['vix_regime']}) |")
    lines.append(f"| Flow | {mkt['flow']} |")
    lines.append(f"| Day Type | {mkt['day_type']} ({mkt['day_type_confidence'] * 100:.0f}% conf) |")
    lines.append(f"| RSI | {fmt(mkt['rsi'], 0)} |")
    lines.append(f"| MACD | {mkt['macd_signal']} |")
    lines.append(f"| ATR | {fmt(mkt['atr'])} |")
    lines.append("")

    # Session Progress (live mode)
    session = mkt.get("session")
    if mode == "live" and session:
        lines.append("## Session Progress")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Open | {session['open']:,.2f} |")
        lines.append(f"| High | {session['high']:,.2f} |")
        lines.append(f"| Low | {session['low']:,.2f} |")
        lines.append(f"| Last | {session['last']:,.2f} |")
        lines.append(f"| Change | {session['change_pct']:+.2f}% |")
        lines.append(f"| Range | {session['range_pct']:.2f}% |")
        lines.append(f"| Bars | {session['bars_elapsed']} |")
        lines.append(f"| Last Bar | {session['last_bar_time']} |")
        lines.append("")

    # Nifty Levels
    lvl = mkt["levels"]
    if lvl:
        lines.append("## Nifty Levels")
        lines.append(f"| Level | Price |")
        lines.append(f"|-------|-------|")
        for key in ("r2", "r1", "pivot", "s1", "s2"):
            lines.append(f"| {key.upper()} | {lvl.get(key, 'N/A'):,.2f} |")
        if mkt["ema20"]:
            lines.append(f"| EMA20 | {mkt['ema20']:,.2f} |")
        if mkt["ema50"]:
            lines.append(f"| EMA50 | {mkt['ema50']:,.2f} |")
        lines.append("")

    # Breadth
    lines.append(f"## Breadth ({breadth['n']} stocks)")
    lines.append(f"- Above EMA20: **{breadth['above_ema20_pct']}%**")
    lines.append(f"- A/D Ratio: **{breadth['ad_ratio']}** ({breadth['advances']}A / {breadth['declines']}D)")
    td = breadth["trend_dist"]
    lines.append(f"- Trend: Up {td.get('strong_up', 0) + td.get('mild_up', 0):.0f}% | "
                 f"Sideways {td.get('sideways', 0):.0f}% | "
                 f"Down {td.get('strong_down', 0) + td.get('mild_down', 0):.0f}%")
    lines.append("")

    # Sector Rotation
    lines.append("## Sector Rotation")
    lines.append("| Sector | 1D | 5D | Leader |")
    lines.append("|--------|----|----|--------|")
    for s in sectors:
        leader_str = f"{s['leader']} ({s['leader_ret']:+.1f}%)" if s["leader"] else "-"
        lines.append(f"| {s['sector']} | {s['ret_1d']:+.2f}% | {s['ret_5d']:+.2f}% | {leader_str} |")
    lines.append("")

    # Historical Patterns
    lines.append("## Historical Patterns")
    seas = patterns.get("seasonality", {})
    if seas.get("win_rate") is not None:
        lines.append(f"- **{seas['dow']} + {seas['month_period']}**: "
                     f"{seas['win_rate']}% win rate, avg {seas['avg_return']:+.3f}% (N={seas['n']})")
    rm = patterns.get("regime_match", {})
    if rm:
        lines.append(f"- **Similar regime** (N={rm['n']}): "
                     f"{rm['up_pct']}% up next day, avg {rm['avg_return']:+.3f}%")
    lines.append("")

    # Forecast
    lines.append("## Forecast")
    dir_label = "Bias" if mode == "live" else "Gap Direction"
    lines.append(f"- **{dir_label}**: {forecast['direction']}")
    lines.append(f"- **Likely Day Type**: {forecast['likely_day_type']}")
    lines.append(f"- **Risk Level**: {forecast['risk_level']}")
    lines.append(f"- **Strategies**: {', '.join(forecast['strategies'])}")
    lines.append(f"- **Watch Sectors**: {', '.join(forecast['watch_sectors']) if forecast['watch_sectors'] else 'N/A'}")
    lines.append("")

    # AI Outlook
    lines.append("## AI Outlook")
    if llm_text:
        lines.append(llm_text)
    else:
        lines.append("*LLM unavailable*")
    lines.append("")

    path.write_text("\n".join(lines))
    print(f"\n  Report saved: {path}")
    return path


# ── Main Orchestrator ────────────────────────────────────────────────────

def main():
    """Fetch → analyze → forecast → render → save."""
    parser = argparse.ArgumentParser(description="Market Outlook Predictor")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM call")
    parser.add_argument("--mode", choices=["pre_market", "live", "post_market"],
                        default=None, help="Force a specific mode (auto-detects by default)")
    args = parser.parse_args()

    # Detect mode
    mode = args.mode or _detect_mode()
    target_date, session_label = _get_target_session(mode)
    mode_display = {"pre_market": "PRE-MARKET", "live": "LIVE", "post_market": "POST-MARKET"}

    print("\n" + "=" * 60)
    print(f"  MARKET OUTLOOK PREDICTOR  [{mode_display[mode]}]")
    print(f"  {session_label}")
    print("=" * 60)

    # 1. Fetch all data
    print("\n[1/6] Fetching data...")
    data = _fetch_all_data()

    # 2. Analyze market structure
    print("[2/6] Analyzing market structure...")
    mkt = _analyze_market_structure(data, mode)

    # 3. Analyze breadth
    print("[3/6] Computing breadth...")
    breadth = _analyze_breadth(data, data["nifty_daily"])

    # 4. Sector rotation
    print("[4/6] Mapping sector rotation...")
    sectors = _analyze_sector_rotation(data)

    # 5. Historical patterns
    print("[5/6] Matching historical patterns...")
    patterns = _match_historical_patterns(data, mkt, target_date)

    # 6. Build forecast
    print("[6/6] Building forecast...")
    forecast = _build_forecast(mkt, breadth, sectors, patterns, mode)

    # LLM outlook
    llm_text = None
    if not args.no_llm:
        print("\n  Calling LLM for outlook...")
        news = get_news_and_sentiment(list(TICKERS.keys())[:5])
        news_ctx = news.get("_market", "")
        llm_text = _call_llm_outlook(mkt, breadth, sectors, patterns, forecast,
                                     news_ctx, mode, session_label)

    # Render terminal dashboard
    print()
    _render_dashboard(mkt, breadth, sectors, patterns, forecast, llm_text,
                      mode, session_label)

    # Save report
    _write_report(mkt, breadth, sectors, patterns, forecast, llm_text,
                  mode, session_label)

    print("\n  Done.\n")


if __name__ == "__main__":
    main()
