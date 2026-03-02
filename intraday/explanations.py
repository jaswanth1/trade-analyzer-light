"""
Educational explanations for intraday setups.

Two modes:
1. Template-based — deterministic, fast, covers all setups
2. LLM-powered — richer narrative for top 3 setups, with cricket analogies and rupee terms

Adapts based on market phase (pre_market, pre_live, live, post_market).
"""

import numpy as np

from common.indicators import compute_atr, compute_beta


# ── Strategy descriptions ────────────────────────────────────────────────

STRATEGY_DESCRIPTIONS = {
    "orb": (
        "Opening Range Breakout (ORB) captures the directional move that follows "
        "the first 30 minutes of trading. The opening range absorbs overnight news, "
        "gap reactions, and institutional pre-market orders — when price breaks out "
        "of this range with volume, it signals a directional commitment. "
        "Edge decays after ~10:30; by noon it's gone."
    ),
    "pullback": (
        "Pullback entry waits for a trending stock to temporarily retrace toward "
        "dynamic support (VWAP or 20-EMA) before continuing. This gives a better "
        "risk/reward than chasing — you enter where institutional buyers are likely "
        "to step in, with a tight stop below the support zone."
    ),
    "compression": (
        "Compression Squeeze detects when Bollinger Bands contract inside Keltner "
        "Channels — volatility is coiling. When the squeeze releases and price breaks "
        "out with volume, the stored energy produces a sharp directional move. "
        "Best in the mid-session when morning volatility settles into a range."
    ),
    "mean_revert": (
        "Mean Reversion fades extreme moves back toward VWAP. When price reaches "
        "VWAP ± 2 standard deviations in a range-bound or volatile day, the "
        "statistical tendency is to revert toward the mean. Works best when the "
        "overall market isn't trending strongly — you don't want to fade a real trend."
    ),
    "swing": (
        "Swing Continuation identifies stocks closing strongly with RSI momentum "
        "for a 1-5 day hold. Unlike intraday strategies, this captures the overnight "
        "continuation when a stock ends near the high of a trend day with institutional "
        "volume. Wider stops but also wider targets (3× risk)."
    ),
    "mlr": (
        "Morning Low Recovery (MLR) buys stocks that form their session low in the "
        "first 90 minutes (9:15\u201311:00 AM) and show confirmed reversal. Data shows "
        "57% of daily lows form in this window, with avg +2.2% recovery to close. "
        "Entry triggers on volume-confirmed bounce off the morning low, targeting the "
        "previous close or pivot level. Works in all market regimes including bearish days."
    ),
}

# ── Template-based explanations ──────────────────────────────────────────

def _action_label(direction, explain=False):
    """Convert long/short to BUY/SELL with optional explanation."""
    if direction == "long":
        return "BUY (buy now, sell later)" if explain else "BUY"
    return "SELL (sell now, buy back later)" if explain else "SELL"


def _format_rupee(value):
    """Format value in ₹ with Indian comma notation."""
    if value >= 10_000_000:
        return f"₹{value / 10_000_000:.1f}Cr"
    if value >= 100_000:
        return f"₹{value / 100_000:.1f}L"
    if value >= 1000:
        return f"₹{value:,.0f}"
    return f"₹{value:.2f}"


def _compute_stock_profile(candidate, daily_df, bench_daily=None):
    """Build stock profile metrics for explanation."""
    profile = {}
    entry = candidate.get("entry_price", 0)

    # ATR in ₹ terms
    atr_raw = compute_atr(daily_df) if not daily_df.empty else None
    if atr_raw is not None and not np.isnan(atr_raw):
        atr_val = float(atr_raw)
        profile["atr"] = atr_val
        profile["atr_pct"] = round(atr_val / entry * 100, 2) if entry > 0 else 0
    else:
        profile["atr"] = 0
        profile["atr_pct"] = 0

    # Beta
    if bench_daily is not None and not bench_daily.empty and not daily_df.empty:
        try:
            beta = compute_beta(daily_df, bench_daily)
            profile["beta"] = round(beta, 2) if beta == beta else 1.0
        except Exception:
            profile["beta"] = 1.0
    else:
        profile["beta"] = 1.0

    # Per-₹1L capital risk
    stop_pct = candidate.get("stop_pct", 1.0)
    target_pct = candidate.get("target_pct", 1.0)
    if entry > 0:
        shares_per_lakh = int(100_000 / entry)
        profile["shares_per_lakh"] = shares_per_lakh
        profile["risk_per_lakh"] = round(shares_per_lakh * entry * stop_pct / 100, 0)
        profile["reward_per_lakh"] = round(shares_per_lakh * entry * target_pct / 100, 0)
    else:
        profile["shares_per_lakh"] = 0
        profile["risk_per_lakh"] = 0
        profile["reward_per_lakh"] = 0

    return profile


def generate_setup_explanation(candidate, mode, daily_df=None, bench_daily=None):
    """Generate template-based educational explanation for one setup.

    Args:
        candidate: setup dict from evaluate_symbol or pre/post market scan
        mode: "pre_market" | "pre_live" | "live" | "post_market"
        daily_df: daily data for the symbol (for ATR/beta)
        bench_daily: Nifty daily data (for beta)

    Returns:
        Multi-line explanation string.
    """
    sym = candidate.get("symbol", "").replace(".NS", "")
    name = candidate.get("name", sym)
    strategy = candidate.get("strategy", "unknown")
    direction = candidate.get("direction", "long")
    entry = candidate.get("entry_price", 0)
    target = candidate.get("target_price", 0)
    stop = candidate.get("stop_price", 0)
    rr = candidate.get("rr_ratio", 0)
    score = candidate.get("score", 0)
    regime = candidate.get("symbol_regime", {})

    # Stock profile
    if daily_df is not None and not daily_df.empty:
        profile = _compute_stock_profile(candidate, daily_df, bench_daily)
    else:
        profile = {"atr": 0, "atr_pct": 0, "beta": 1.0,
                   "shares_per_lakh": 0, "risk_per_lakh": 0, "reward_per_lakh": 0}

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
    lines.append(f"Regime: {trend} trend, {vol} volatility, {momentum} momentum.")

    # 2. Strategy logic
    strat_desc = STRATEGY_DESCRIPTIONS.get(strategy, "")
    if strat_desc:
        lines.append(f"\nWhy {strategy.upper()}? {strat_desc}")

    # 3. Entry conditions
    lines.append(f"\nSetup: {_action_label(direction, explain=True)} @ {_format_rupee(entry)}")
    lines.append(f"Target: {_format_rupee(target)} (+{candidate.get('target_pct', 0):.1f}%) "
                 f"| Stop: {_format_rupee(stop)} (-{candidate.get('stop_pct', 0):.1f}%) "
                 f"| RR: {rr:.1f}:1")

    # Conditions to verify
    conds = candidate.get("conditions", {})
    if conds:
        verify = []
        for k, v in conds.items():
            if isinstance(v, dict):
                if not v.get("met"):
                    verify.append(f"  - {k}: {v.get('detail', 'not met')} (WATCH)")
                else:
                    verify.append(f"  - {k}: {v.get('detail', 'met')}")
            elif not v:
                verify.append(f"  - {k}: not met (WATCH)")
        if verify:
            lines.append("Conditions to verify:")
            lines.extend(verify)

    # 4. ₹ terms per ₹1L capital
    if profile["shares_per_lakh"] > 0:
        lines.append(
            f"\nPer ₹1L capital: ~{profile['shares_per_lakh']} shares "
            f"| Risk: {_format_rupee(profile['risk_per_lakh'])} "
            f"| Reward: {_format_rupee(profile['reward_per_lakh'])}"
        )

    # 5. Convergence breakdown
    conv_score = candidate.get("convergence_score", 0)
    conv_detail = candidate.get("convergence_detail", "")
    if conv_detail:
        lines.append(f"\nConvergence: {conv_score}% — {conv_detail}")
        if conv_score >= 70:
            lines.append("Strong alignment — multiple indicators confirm the trade direction.")
        elif conv_score < 40:
            lines.append("Weak alignment — indicators are mixed. Reduce size or skip.")

    # 6. Historical context
    hist_ctx = candidate.get("historical_context", "")
    hist_rate = candidate.get("historical_hit_rate", 0)
    hist_n = candidate.get("historical_sample_size", 0)
    if hist_ctx and hist_n > 0:
        lines.append(f"\nHistory: {hist_ctx}")
        if hist_rate >= 60 and hist_n >= 10:
            lines.append("Historical edge is solid — this pattern has paid off consistently.")
        elif hist_rate < 40 and hist_n >= 10:
            lines.append("Historical edge is WEAK — similar setups have underperformed.")

    # 7. Timing context (mode-aware)
    time_status = candidate.get("time_status", "")
    time_note = candidate.get("time_note", "")
    if mode == "pre_market":
        lines.append("\nTiming: This is a CONDITIONAL setup — wait for the market to open "
                     "and confirm the gap scenario before entering.")
    elif mode == "pre_live":
        lines.append("\nTiming: Pre-market session is active (9:00-9:15). "
                     "Watch the indicated open price to confirm which gap scenario plays out.")
    elif mode == "live":
        if time_status:
            lines.append(f"\nTiming: {time_status}")
        if time_note:
            lines.append(time_note)
    elif mode == "post_market":
        lines.append("\nTiming: This is a TOMORROW setup — conditional on tomorrow's open. "
                     "Check pre-market data at 9:00 for confirmation.")

    # 8. What could go wrong + verdict
    risks = []
    news_sent = candidate.get("news_sentiment", 0)
    if news_sent < -0.3 and direction == "long":
        risks.append("Negative news sentiment opposes long direction")
    elif news_sent > 0.3 and direction == "short":
        risks.append("Positive news sentiment opposes short direction")
    weekly_trend = regime.get("weekly_trend", "sideways")
    if direction == "long" and weekly_trend in ("mild_down", "strong_down"):
        risks.append("Weekly trend is down — fighting the bigger picture")
    elif direction == "short" and weekly_trend in ("mild_up", "strong_up"):
        risks.append("Weekly trend is up — shorting into strength")
    if profile.get("beta", 1) > 1.5:
        risks.append(f"High-beta stock ({beta:.1f}) — expect sharp swings both ways")
    if regime.get("volatility") == "expanded":
        risks.append("Expanded volatility — wider stops needed, smaller size")

    if risks:
        lines.append("\nRisks:")
        for r in risks:
            lines.append(f"  - {r}")

    # Verdict
    signal = candidate.get("signal", "WATCH")
    if signal == "STRONG":
        lines.append("\nVerdict: HIGH CONVICTION — multiple factors align. Full size.")
    elif signal == "ACTIVE":
        lines.append("\nVerdict: GOOD SETUP — edge is there but not overwhelming. Normal size.")
    elif signal == "WATCH":
        lines.append("\nVerdict: WATCHLIST ONLY — monitor but don't enter until conditions improve.")
    else:
        lines.append("\nVerdict: AVOID — conditions are unfavorable.")

    return "\n".join(lines)


# ── Gap scenario explanation for pre-market ──────────────────────────────

def generate_scenario_explanation(symbol_name, scenario, profile):
    """Generate IF-THEN explanation for a pre-market gap scenario.

    Args:
        symbol_name: e.g. "RELIANCE"
        scenario: dict with keys: type, gap_threshold, strategy, direction,
                  entry, target, stop, probability, rr, historical_context,
                  convergence_detail, conditions_to_watch
        profile: stock profile dict from _compute_stock_profile

    Returns:
        Multi-line IF-THEN explanation string.
    """
    stype = scenario["type"]  # "gap_up", "gap_down", "flat"
    gap_thresh = scenario.get("gap_threshold", 0.5)
    strategy = scenario.get("strategy", "orb")
    direction = scenario.get("direction", "long")
    entry = scenario.get("entry", 0)
    target = scenario.get("target", 0)
    stop = scenario.get("stop", 0)
    prob = scenario.get("probability", 0)
    rr = scenario.get("rr", 0)
    hist = scenario.get("historical_context", "")

    lines = []

    if stype == "gap_up":
        lines.append(f"IF {symbol_name} opens gap-up (> {gap_thresh:.1f}% above prev close):")
    elif stype == "gap_down":
        lines.append(f"IF {symbol_name} opens gap-down (> {gap_thresh:.1f}% below prev close):")
    else:
        lines.append(f"IF {symbol_name} opens flat (within ±0.3% of prev close):")

    lines.append(f"  → {strategy.upper()} {_action_label(direction)} | "
                 f"Entry: ~{_format_rupee(entry)} | "
                 f"Target: {_format_rupee(target)} | "
                 f"Stop: {_format_rupee(stop)}")
    lines.append(f"  → Probability: {prob:.0f}% | RR: {rr:.1f}:1")

    if hist:
        lines.append(f"  → History: {hist}")

    # Conditions to watch at open
    watch_items = scenario.get("conditions_to_watch", [])
    if watch_items:
        lines.append(f"  → Watch for: {', '.join(watch_items)}")

    # Strategy description
    strat_desc = STRATEGY_DESCRIPTIONS.get(strategy, "")
    if strat_desc:
        lines.append(f"  → Why: {strat_desc[:120]}...")

    return "\n".join(lines)


# ── LLM-powered explanations ────────────────────────────────────────────

_LLM_SYSTEM_PROMPTS = {
    "pre_market": """You are an expert Indian equity market educator explaining pre-market analysis.
The trader has conditional IF-THEN setups based on gap scenarios. Your job:
1. Explain WHY each scenario matters — what the gap tells us about overnight sentiment
2. Which scenario is MOST likely given the regime and news
3. What to watch at 9:00 (pre-market session) and 9:15 (market open) to confirm
4. Use cricket analogies where they genuinely help (e.g., "like reading the pitch before the first ball")
5. Express risk in rupee terms per ₹1 lakh capital
6. Keep it educational — explain the reasoning, not just the levels
Be concise (200-300 words). No disclaimers.""",

    "pre_live": """You are an expert Indian equity market educator analyzing the 9:00-9:15 pre-market session.
Pre-market auction data is now available — indicated opening prices and volume. Your job:
1. Explain what the pre-market data REVEALS about institutional positioning
2. Which gap scenario is now confirmed and what it means
3. Flag stocks with unusually high pre-market volume (institutional interest)
4. Give specific things to watch at 9:15 open: first 5 minutes, VWAP development, volume
5. Use rupee terms per ₹1 lakh capital
6. Cricket analogies where helpful (e.g., "the toss has happened — we know the conditions now")
Be concise (200-300 words). No disclaimers.""",

    "live": """You are an expert Indian equity market advisor for live intraday trading.
Active setups with real-time data. Your job:
1. RANK by conviction — what to enter NOW vs wait vs skip
2. Time pressure context — which strategy windows are closing
3. Specific entry confirmation: what the trader should see on their screen before clicking buy/sell
4. Risk in rupee terms: "if this goes wrong, you lose ₹X on ₹1L capital"
5. Quick — trader needs decisions, not essays
Be concise (200-300 words). No disclaimers. Assume experienced trader.""",

    "post_market": """You are an expert Indian equity market educator reviewing today's session and preparing for tomorrow.
Your job:
1. What kind of day was it? Summarize the character (trend/range/volatile)
2. For tomorrow's watchlist: explain each IF-THEN setup and its edge
3. What overnight risks exist (global cues, events, FII/DII patterns)
4. Use cricket analogies (e.g., "like preparing for tomorrow's match by studying the opponent's recent form")
5. Express risk in rupee terms per ₹1 lakh capital
6. Educational focus — help the trader learn from today's patterns
Be concise (200-300 words). No disclaimers.""",
}


def generate_llm_explanation(candidates, mode, market_context=None):
    """Generate LLM-powered educational explanation for top setups.

    Args:
        candidates: list of top candidates (max 3)
        mode: "pre_market" | "pre_live" | "live" | "post_market"
        market_context: dict with nifty_state, vix_info, day_type_info, news, etc.

    Returns:
        LLM explanation string, or None if LLM unavailable.
    """
    from common.llm import call_llm

    system_prompt = _LLM_SYSTEM_PROMPTS.get(mode, _LLM_SYSTEM_PROMPTS["live"])

    # Build user content with setup details
    lines = []
    ctx = market_context or {}
    if ctx.get("nifty_regime"):
        lines.append(f"Nifty: {ctx['nifty_regime']} | VIX: {ctx.get('vix_val', 'N/A')} ({ctx.get('vix_regime', '')})")
    if ctx.get("day_type"):
        lines.append(f"Day type: {ctx['day_type']}")
    if ctx.get("inst_flow"):
        lines.append(f"Institutional flow: {ctx['inst_flow']}")
    if ctx.get("market_news"):
        lines.append(f"Market context: {ctx['market_news'][:200]}")
    lines.append("")

    for i, c in enumerate(candidates[:3], 1):
        sym = c.get("symbol", "").replace(".NS", "")
        lines.append(f"Setup {i}: {sym} [{c.get('strategy', '').upper()}] {c.get('direction', '').upper()}")
        lines.append(f"  Entry: {c.get('entry_price', 0):.2f} | Target: {c.get('target_price', 0):.2f} | "
                     f"Stop: {c.get('stop_price', 0):.2f}")
        lines.append(f"  Score: {c.get('score', 0):.0%} | RR: {c.get('rr_ratio', 0):.1f} | "
                     f"Convergence: {c.get('convergence_score', 0)}%")
        regime = c.get("symbol_regime", {})
        lines.append(f"  Regime: {regime.get('trend', 'N/A')}, {regime.get('momentum', 'N/A')}, "
                     f"weekly={regime.get('weekly_trend', 'N/A')}")
        hist = c.get("historical_context", "")
        if hist:
            lines.append(f"  History: {hist}")
        news_sum = c.get("news_summary", "")
        if news_sum:
            lines.append(f"  News: {news_sum}")

        # Mode-specific additions
        if mode == "pre_market":
            scenarios = c.get("gap_scenarios", [])
            for s in scenarios:
                lines.append(f"  Scenario: {s.get('type', '')} → {s.get('strategy', '')} "
                             f"{s.get('direction', '')} (prob: {s.get('probability', 0):.0f}%)")
        elif mode == "live":
            ts = c.get("time_status", "")
            if ts:
                lines.append(f"  Timing: {ts}")
        lines.append("")

    user_content = "\n".join(lines)

    response = call_llm(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_tokens=800,
        temperature=0.4,
    )

    if response and not response.startswith("[AI Error"):
        return response
    return None
