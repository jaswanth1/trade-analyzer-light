"""
Intraday output — render/write/LLM functions for LIVE mode.

Dashboard rendering, markdown report generation, and LLM advisory.
Phase-specific rendering (pre-market, pre-live, post-market) lives in phases.py.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from common.data import PROJECT_ROOT
from common.display import fmt, box_top, box_mid, box_bot, box_line, W
from intraday.explanations import (
    _action_label, STRATEGY_DESCRIPTIONS,
)

IST = ZoneInfo("Asia/Kolkata")

# ── Constants (defined locally to avoid circular imports from scanner) ──

MAX_INTRADAY_POSITIONS = 5
INTRADAY_REPORT_DIR = PROJECT_ROOT / "intraday" / "reports"

# ── AI System Prompt ───────────────────────────────────────────────────

INTRADAY_AI_SYSTEM_PROMPT = """You are a professional intraday trading advisor for Indian equity markets (NSE).
You receive structured data about stocks being evaluated across multiple strategies:
ORB (opening range breakout), pullback, compression squeeze, mean-reversion, swing, and MLR (morning low recovery).

CRITICAL RULES:
- You MUST ONLY discuss stocks that appear in the candidate data provided below.
- NEVER invent, fabricate, or suggest stocks that are not in the input data.
- If there are ZERO candidates in the data, say "No qualifying setups found" and briefly explain
  the market conditions (regime, VIX, day-type) that are suppressing signals. Do NOT make up trades.
- All entry/target/stop prices MUST come from the candidate data. Never guess prices.

Your job (ONLY when candidates exist):
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


# ── AI Context Builder ─────────────────────────────────────────────────

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

    return call_llm(messages)


# ── Dashboard Rendering ────────────────────────────────────────────────

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


# ── Markdown Report ────────────────────────────────────────────────────

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
