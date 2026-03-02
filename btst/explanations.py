"""
Educational explanations for BTST setups.

Template-based explanations for all setups + LLM-powered narrative for top picks.
Reuses stock profile utilities from intraday.explanations to avoid duplication.
"""

from intraday.explanations import _compute_stock_profile, _format_rupee, _action_label


# ── Strategy descriptions ────────────────────────────────────────────────

BTST_STRATEGY_DESCRIPTIONS = {
    "closing_strength": (
        "Closing Strength BTST captures stocks that finish the day at or near their "
        "high with above-average volume. When institutional buyers accumulate throughout "
        "the day and push price to close near the high, the momentum often carries "
        "into the next session. The key is the close position (close relative to "
        "day's range) — a value ≥ 0.80 means the stock closed in the top 20% of "
        "its range, signaling strong demand into the close."
    ),
    "volume_breakout": (
        "Volume Breakout BTST identifies stocks where the last hour's volume surges "
        "significantly above the historical average. This late-session volume spike "
        "often indicates institutional block buying that couldn't be completed intraday "
        "and will continue at tomorrow's open. Combined with closing near the high, "
        "it creates a strong overnight edge."
    ),
    "trend_continuation": (
        "Trend Continuation BTST buys stocks in a confirmed daily uptrend (EMA 9 > 20 > 50) "
        "that also show bullish closing action. When a stock is trending up on the daily "
        "chart AND closes near its high with relative strength vs Nifty, the overnight "
        "continuation probability increases significantly. The multi-timeframe alignment "
        "(daily trend + closing strength) provides the edge."
    ),
}


# ── LLM system prompt ───────────────────────────────────────────────────

BTST_LLM_SYSTEM_PROMPT = """You are a professional overnight/BTST trading advisor for Indian equity markets (NSE).
You receive structured data about stocks being evaluated for Buy Today Sell Tomorrow trades.

Your job:
1. RANK the top setups by conviction — which ones have the strongest overnight edge
2. For each, explain WHY the overnight edge exists:
   - Closing strength pattern (close near high = unfilled demand)
   - Volume signature (late-session surge = institutional accumulation)
   - Daily trend alignment (multi-timeframe confluence)
   - Historical overnight win rate for this pattern
3. Flag specific risks:
   - Correlated positions (if multiple stocks from same sector)
   - Gap-down risk factors (global events, sector headwinds)
   - Earnings proximity
   - VIX level and what it means for overnight holds
4. Give specific exit plan: "Exit by 10:30 AM tomorrow or at target. Trail stop after +1.5%."
5. Recommend how many of the max 3 BTST slots to use tonight
6. Express risk in rupee terms per ₹1 lakh capital

Be concise. Bullet points. No disclaimers. Assume experienced trader.
Respond in 250-350 words max."""


# ── Template-based explanation ───────────────────────────────────────────

def generate_btst_explanation(signal, daily_df, bench_daily=None):
    """Generate educational explanation for one BTST setup.

    Reuses _compute_stock_profile, _format_rupee, _action_label from
    intraday.explanations (no duplication).

    Returns multi-line explanation string.
    """
    sym = signal.get("symbol", "").replace(".NS", "")
    name = signal.get("name", sym)
    entry = signal.get("entry_price", 0)
    target = signal.get("target_price", 0)
    stop = signal.get("stop_price", 0)
    regime = signal.get("symbol_regime", {})

    # Stock profile (ATR, beta, per-₹1L)
    profile = _compute_stock_profile(signal, daily_df, bench_daily)

    lines = []

    # 1. Stock profile
    beta = profile["beta"]
    atr = profile["atr"]
    lines.append(f"--- {sym} ({name}) ---")
    if atr > 0:
        lines.append(
            f"Daily range: ~{_format_rupee(atr)} ({profile['atr_pct']}% of price). "
            f"Beta: {beta:.1f} — for every ₹100 Nifty moves, {sym} moves ~{_format_rupee(beta * 100)}."
        )
    trend = regime.get("trend", "N/A")
    vol = regime.get("volatility", "N/A")
    momentum = regime.get("momentum", "N/A")
    weekly = regime.get("weekly_trend", "N/A")
    rs = regime.get("relative_strength", "N/A")
    lines.append(f"Regime: {trend} trend, {vol} volatility, {momentum} momentum, "
                 f"weekly {weekly}, RS {rs}.")

    # 2. Strategy description
    # Determine which BTST strategy applies
    cs = signal.get("closing_strength", {})
    vol_surge = cs.get("volume_surge_ratio", 1.0)
    if vol_surge >= 1.5:
        strat_key = "volume_breakout"
    elif trend in ("strong_up", "mild_up") and weekly == "up":
        strat_key = "trend_continuation"
    else:
        strat_key = "closing_strength"

    strat_desc = BTST_STRATEGY_DESCRIPTIONS.get(strat_key, "")
    if strat_desc:
        lines.append(f"\nWhy this setup? {strat_desc}")

    # 3. Entry/target/stop
    rr = signal.get("target_pct", 0) / signal.get("stop_pct", 1) if signal.get("stop_pct", 0) > 0 else 0
    lines.append(f"\nSetup: BUY (buy today, sell tomorrow) @ {_format_rupee(entry)}")
    lines.append(f"Target: {_format_rupee(target)} (+{signal.get('target_pct', 0):.1f}%) "
                 f"| Stop: {_format_rupee(stop)} (-{signal.get('stop_pct', 0):.1f}%) "
                 f"| RR: {rr:.1f}:1")

    # 4. Per-₹1L capital
    if profile["shares_per_lakh"] > 0:
        lines.append(
            f"\nPer ₹1L capital: ~{profile['shares_per_lakh']} shares "
            f"| Risk: {_format_rupee(profile['risk_per_lakh'])} "
            f"| Reward: {_format_rupee(profile['reward_per_lakh'])}"
        )

    # 5. Convergence breakdown
    conv_score = signal.get("convergence_score", 0)
    conv_aligned = signal.get("convergence_aligned", [])
    conv_conflicting = signal.get("convergence_conflicting", [])
    if conv_aligned or conv_conflicting:
        lines.append(f"\nConvergence: {conv_score}%")
        if conv_aligned:
            lines.append(f"  Aligned: {', '.join(conv_aligned)}")
        if conv_conflicting:
            lines.append(f"  Conflicting: {', '.join(conv_conflicting)}")
        if conv_score >= 70:
            lines.append("  Strong alignment — multiple daily indicators confirm the overnight hold.")
        elif conv_score < 40:
            lines.append("  Weak alignment — indicators are mixed. Reduce size or skip.")

    # 6. Overnight stats
    overnight_wr = signal.get("overnight_wr", 0)
    overnight_n = signal.get("overnight_stats", {}).get("all", {}).get("n_samples", 0)
    if overnight_wr > 0:
        lines.append(f"\nOvernight win rate: {overnight_wr:.0f}% (N={overnight_n})")

    # 7. DOW + month-period stats
    dow_wr = signal.get("dow_wr", 0)
    month_period = signal.get("month_period", "")
    month_period_wr = signal.get("month_period_wr", 0)
    if dow_wr > 0 or month_period_wr > 0:
        lines.append(f"DOW overnight WR: {dow_wr:.0f}% | {month_period} period WR: {month_period_wr:.0f}%")

    # 8. Historical hit rate
    hist_ctx = signal.get("historical_context", "")
    hist_rate = signal.get("historical_hit_rate", 0)
    hist_n = signal.get("historical_sample_size", 0)
    if hist_ctx and hist_n > 0:
        lines.append(f"\nHistory: {hist_ctx}")
        if hist_rate >= 60 and hist_n >= 10:
            lines.append("Historical edge is solid — this pattern has paid off consistently.")
        elif hist_rate < 40 and hist_n >= 10:
            lines.append("Historical edge is WEAK — similar setups have underperformed.")

    # 9. News
    news_sum = signal.get("news_summary", "")
    if news_sum:
        lines.append(f"\nNews: {news_sum}")

    # 10. Risks
    risks = []
    news_sent = signal.get("news_sentiment", 0)
    if news_sent < -0.3:
        risks.append("Negative news sentiment opposes overnight long direction")
    if signal.get("has_material_event", False):
        risks.append("Material event detected — increased overnight gap risk")
    if weekly in ("down",):
        risks.append("Weekly trend is down — fighting the bigger picture")
    if profile.get("beta", 1) > 1.5:
        risks.append(f"High-beta stock ({beta:.1f}) — expect sharp overnight moves both ways")
    if regime.get("volatility") == "expanded":
        risks.append("Expanded volatility — wider stops needed, smaller size")
    if conv_score < 50 and conv_conflicting:
        risks.append(f"Convergence weak ({conv_score}%) — {', '.join(conv_conflicting[:2])} conflicting")

    if risks:
        lines.append("\nRisks:")
        for r in risks:
            lines.append(f"  - {r}")

    # 11. Verdict
    sig = signal.get("signal", "WATCH")
    composite = signal.get("composite_score", 0)
    if sig == "STRONG_BUY":
        lines.append(f"\nVerdict: HIGH CONVICTION (composite {composite:.0%}) — multiple factors align. Full size.")
    elif sig == "BUY":
        lines.append(f"\nVerdict: GOOD SETUP (composite {composite:.0%}) — edge is there but not overwhelming. Normal size.")
    elif sig == "WATCH":
        lines.append("\nVerdict: WATCHLIST ONLY — monitor but don't enter until conditions improve.")
    else:
        lines.append("\nVerdict: AVOID — conditions are unfavorable for overnight hold.")

    return "\n".join(lines)


# ── LLM-powered explanation ─────────────────────────────────────────────

def generate_btst_llm_explanation(signals, market_context):
    """Generate LLM-powered narrative for top BTST setups.

    Args:
        signals: list of top signals (max 5)
        market_context: dict with nifty_regime, vix_val, vix_regime, inst_flow,
                        dow_name, month_period, market_news

    Returns:
        LLM explanation string, or None if LLM unavailable.
    """
    from common.llm import call_llm

    lines = []
    ctx = market_context or {}
    lines.append(f"Nifty: {ctx.get('nifty_regime', 'unknown')} | "
                 f"VIX: {ctx.get('vix_val', 'N/A')} ({ctx.get('vix_regime', '')})")
    if ctx.get("inst_flow"):
        lines.append(f"Institutional flow: {ctx['inst_flow']}")
    if ctx.get("dow_name"):
        lines.append(f"DOW: {ctx['dow_name']} | Month period: {ctx.get('month_period', 'N/A')}")
    if ctx.get("market_news"):
        lines.append(f"Market context: {ctx['market_news'][:300]}")
    lines.append("")

    for i, s in enumerate(signals[:5], 1):
        sym = s.get("symbol", "").replace(".NS", "")
        lines.append(f"Setup {i}: {sym} ({s.get('name', '')}) | Signal: {s.get('signal', '')}")
        lines.append(f"  Composite: {s.get('composite_score', 0):.0%} | "
                     f"Overnight WR: {s.get('overnight_wr', 0):.0f}%")
        lines.append(f"  Entry: {s.get('entry_price', 0):.2f} | "
                     f"Target: {s.get('target_price', 0):.2f} (+{s.get('target_pct', 0):.1f}%) | "
                     f"Stop: {s.get('stop_price', 0):.2f} (-{s.get('stop_pct', 0):.1f}%)")
        cs = s.get("closing_strength", {})
        lines.append(f"  Close position: {cs.get('close_position', 'N/A')} | "
                     f"Volume surge: {cs.get('volume_surge_ratio', 'N/A')}x")
        lines.append(f"  Convergence: {s.get('convergence_score', 0)}% | "
                     f"Regime: {s.get('symbol_regime', {}).get('trend', 'N/A')}, "
                     f"weekly={s.get('symbol_regime', {}).get('weekly_trend', 'N/A')}")
        hist = s.get("historical_context", "")
        if hist:
            lines.append(f"  History: {hist}")
        news = s.get("news_summary", "")
        if news:
            lines.append(f"  News: {news}")
        lines.append("")

    user_content = "\n".join(lines)

    response = call_llm(
        [
            {"role": "system", "content": BTST_LLM_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_tokens=800,
        temperature=0.4,
    )

    if response and not response.startswith("[AI Error"):
        return response
    return None
