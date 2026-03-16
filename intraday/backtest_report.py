"""
Intraday Backtest — Report Generation

Extracted reporting functions from IntradayBacktestEngine.
These are standalone functions that take signal data as parameters.
"""

from datetime import datetime

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

from intraday.explanations import _action_label

IST = ZoneInfo("Asia/Kolkata")


def fmt_time(time_str):
    """Format a raw timestamp string to readable IST time (HH:MM)."""
    if not time_str:
        return "—"
    try:
        dt = pd.Timestamp(time_str)
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC").tz_convert(IST)
        else:
            dt = dt.tz_convert(IST)
        return dt.strftime("%H:%M")
    except Exception:
        # Fallback: extract time-like portion
        for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(time_str[:25].strip(), fmt)
                return dt.strftime("%H:%M")
            except Exception:
                continue
        return time_str[-8:-3] if len(time_str) > 8 else time_str


def phase_label(phase):
    """Human-readable phase label."""
    labels = {
        "post_market_t-1": "Post-Market (T-1)",
        "pre_market": "Pre-Market",
    }
    if phase in labels:
        return labels[phase]
    if phase.startswith("live_"):
        return f"Live {phase.replace('live_', '')} IST"
    return phase


def write_signal_narrative(lines, sig, idx):
    """Write a detailed narrative for a single signal.

    Args:
        lines: list of strings to append to
        sig: SignalResult instance
        idx: signal number (1-based)
    """
    clean_sym = sig.symbol.replace(".NS", "")
    action = _action_label(sig.direction)
    plabel = phase_label(sig.phase)

    # Header with outcome icon
    icon = {"CORRECT": "✅", "WRONG": "❌", "CLOSE_CALL": "⚠️",
            "NO_ENTRY": "⏭️"}.get(sig.outcome, "❓")

    lines.append(f"### Signal {idx}: {clean_sym} — {sig.strategy.upper()} {action} {icon}\n")

    # What the scanner said
    lines.append(f"**Scanner said** ({plabel}, {sig.signal_tier}):")
    target_pct = abs(sig.target_price - sig.entry_price) / sig.entry_price * 100 if sig.entry_price > 0 else 0
    stop_pct = abs(sig.entry_price - sig.stop_price) / sig.entry_price * 100 if sig.entry_price > 0 else 0
    lines.append(f"- {action} {clean_sym} at ₹{sig.entry_price:,.2f}")
    lines.append(f"- Target: ₹{sig.target_price:,.2f} ({target_pct:+.1f}%) | "
                  f"Stop: ₹{sig.stop_price:,.2f} ({stop_pct:-.1f}%) | "
                  f"RR: {sig.rr_ratio:.1f}")
    lines.append(f"- Score: {sig.score:.0%}")
    if sig.reason:
        lines.append(f"- Reason: {sig.reason}")
    if sig.convergence:
        lines.append(f"- Convergence: {sig.convergence}")
    if sig.regime:
        lines.append(f"- Regime: {sig.regime}")
    if sig.predicted_scenario:
        lines.append(f"- Predicted gap: {sig.predicted_scenario}")
    lines.append("")

    # What actually happened
    lines.append("**What happened:**")

    if not sig.entry_hit:
        lines.append(f"- ⏭️ **NO ENTRY** — Price never reached ₹{sig.entry_price:,.2f}. "
                      f"Signal was never triggered.")
        lines.append("")
        return

    # Entry
    entry_time = fmt_time(sig.entry_hit_time)
    lines.append(f"- Entry hit at **{entry_time}** IST")

    # Gap scenario check
    if sig.predicted_scenario:
        sc_icon = "✅" if sig.scenario_correct else "❌"
        lines.append(f"- Gap prediction: {sig.predicted_scenario} → "
                      f"actual {sig.actual_scenario} {sc_icon}")

    # Price journey
    mfe_time = fmt_time(sig.mfe_time)
    mae_time = fmt_time(sig.mae_time)
    if sig.direction == "long":
        lines.append(f"- Best price (MFE): ₹{sig.mfe:,.2f} "
                      f"({sig.mfe_pct:+.1f}%) at {mfe_time} — "
                      f"reached {sig.mfe_of_target:.0f}% of target distance")
        lines.append(f"- Worst drawdown (MAE): ₹{sig.mae:,.2f} "
                      f"({sig.mae_pct:-.1f}%) at {mae_time}")
    else:
        lines.append(f"- Best price (MFE): ₹{sig.mfe:,.2f} "
                      f"({sig.mfe_pct:+.1f}%) at {mfe_time} — "
                      f"reached {sig.mfe_of_target:.0f}% of target distance")
        lines.append(f"- Worst drawdown (MAE): ₹{sig.mae:,.2f} "
                      f"({sig.mae_pct:-.1f}%) at {mae_time}")

    # Exit
    if sig.exit_reason == "target":
        exit_time = fmt_time(sig.target_hit_time)
        lines.append(f"- ✅ **TARGET HIT** at {exit_time} IST "
                      f"(₹{sig.exit_price:,.2f}) in {sig.bars_to_resolution} bars")
    elif sig.exit_reason == "stop":
        exit_time = fmt_time(sig.stop_hit_time)
        if sig.mfe_of_target >= 50:
            lines.append(f"- ⚠️ **STOPPED OUT (close call)** at {exit_time} IST "
                          f"(₹{sig.exit_price:,.2f}) — price reached "
                          f"{sig.mfe_of_target:.0f}% of target before reversing")
        else:
            lines.append(f"- ❌ **STOPPED OUT** at {exit_time} IST "
                          f"(₹{sig.exit_price:,.2f}) in {sig.bars_to_resolution} bars")
    elif sig.exit_reason == "eod":
        pnl = sig.exit_price - sig.entry_price if sig.direction == "long" else sig.entry_price - sig.exit_price
        pnl_pct = pnl / sig.entry_price * 100 if sig.entry_price > 0 else 0
        pnl_icon = "📈" if pnl > 0 else "📉"
        lines.append(f"- {pnl_icon} **EOD EXIT** at ₹{sig.exit_price:,.2f} "
                      f"({pnl_pct:+.1f}%) — neither target nor stop hit by close")

    # Verdict
    lines.append("")
    if sig.outcome == "CORRECT":
        lines.append(f"> **VERDICT: SUCCESS** — {sig.strategy} {action} worked as expected.")
    elif sig.outcome == "CLOSE_CALL":
        lines.append(f"> **VERDICT: CLOSE CALL** — Price moved {sig.mfe_of_target:.0f}% "
                      f"toward target before reversing. The direction was right but "
                      f"target was too ambitious or stop too tight.")
    elif sig.outcome == "WRONG":
        lines.append(f"> **VERDICT: FAILED** — Price only reached {sig.mfe_of_target:.0f}% "
                      f"of target. The setup didn't play out.")

    # Flag absurd targets
    if target_pct > 5.0 and sig.outcome != "CORRECT":
        lines.append(f">\n> ⚠️ **FLAG**: Target was {target_pct:.1f}% from entry — "
                      f"may be too aggressive for intraday.")
    lines.append("")


# ── LLM Reasoning ─────────────────────────────────────────────────────

def _format_conditions(conditions):
    """Format conditions dict into a readable summary."""
    if not conditions:
        return "N/A"
    parts = []
    for key, val in conditions.items():
        if isinstance(val, dict):
            met = val.get("met", val.get("value", "?"))
            parts.append(f"{key}: {'✓' if met else '✗'}")
        elif isinstance(val, bool):
            parts.append(f"{key}: {'✓' if val else '✗'}")
        else:
            parts.append(f"{key}: {val}")
    return " | ".join(parts)


def _format_gates(gates):
    """Format gates dict into a readable summary."""
    if not gates:
        return "all passed"
    parts = []
    for key, val in gates.items():
        parts.append(f"{key}: {'✓' if val else '✗'}")
    return " | ".join(parts)


def _build_signal_prompt(sig, market_context):
    """Build LLM prompt for per-signal reasoning."""
    clean_sym = sig.symbol.replace(".NS", "")
    action = _action_label(sig.direction)
    outcome_word = "succeeded" if sig.outcome == "CORRECT" else "failed"

    # Market context summary
    mkt_lines = []
    nifty = market_context.get("nifty", {})
    if nifty:
        mkt_lines.append(f"Nifty: {nifty.get('direction', '?')} "
                         f"({nifty.get('change_pct', 0):+.1f}%), "
                         f"range {nifty.get('range_vs_atr', 0):.1f}x ATR")
    vix = market_context.get("vix", {})
    if vix.get("value"):
        mkt_lines.append(f"VIX: {vix['value']} ({vix.get('regime', 'normal')})")
    if market_context.get("day_type"):
        mkt_lines.append(f"Day type: {market_context['day_type']}")
    sym_data = market_context.get("symbols", {}).get(sig.symbol, {})
    if sym_data:
        mkt_lines.append(f"{clean_sym} actual: {sym_data.get('direction', '?')} "
                         f"({sym_data.get('change_pct', 0):+.1f}%), "
                         f"range ₹{sym_data.get('low', 0):,.0f}-{sym_data.get('high', 0):,.0f}")
    market_summary = "\n    - ".join(mkt_lines) if mkt_lines else "N/A"

    prompt = f"""Analyze this intraday trading signal prediction vs actual outcome.

PREDICTION:
- Stock: {clean_sym} | Strategy: {sig.strategy} | Direction: {action}
- Entry: ₹{sig.entry_price:,.2f} | Target: ₹{sig.target_price:,.2f} | Stop: ₹{sig.stop_price:,.2f}
- Score: {sig.score:.0%} (raw confidence: {sig.score_raw:.0%}) | RR: {sig.rr_ratio:.1f}
- Conditions: {_format_conditions(sig.conditions)}
- Gates: {_format_gates(sig.gates)}
- Convergence: {sig.convergence or 'N/A'}
- Day type at scan: {sig.day_type or 'N/A'}
- Historical hit rate: {sig.historical_hit_rate:.0f}% on {sig.historical_sample_size} samples
- Signal tier: {sig.signal_tier} | Reason: {sig.reason or 'N/A'}

ACTUAL OUTCOME: {sig.outcome}
- Entry hit: {'Yes at ' + fmt_time(sig.entry_hit_time) if sig.entry_hit else 'No'}
- MFE: {sig.mfe_pct:+.1f}% (reached {sig.mfe_of_target:.0f}% of target)
- MAE: {sig.mae_pct:.1f}%
- Exit: {sig.exit_reason} at ₹{sig.exit_price:,.2f}

MARKET CONTEXT:
    - {market_summary}

Explain in 3-4 concise sentences:
1. Why the scanner generated this signal (what looked promising in the setup)
2. Why it {outcome_word} (what actually happened in the market)
3. One specific, actionable takeaway for improving the scanner or trading this setup"""

    return prompt


def _generate_signal_reasoning(sig, market_context):
    """Generate LLM reasoning for a single signal.

    Returns reasoning string, or None if LLM unavailable.
    """
    try:
        from common.llm import call_llm
    except ImportError:
        return None

    prompt = _build_signal_prompt(sig, market_context)
    response = call_llm(
        [{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return response


def _generate_session_analysis(all_signals, market_context):
    """Generate LLM overall session analysis.

    Returns analysis string, or None if LLM unavailable.
    """
    try:
        from common.llm import call_llm
    except ImportError:
        return None

    # Only count actionable signals (STRONG/ACTIVE) for headline stats
    actionable = [s for s in all_signals if s.signal_tier in ("STRONG", "ACTIVE")]
    watch = [s for s in all_signals if s.signal_tier == "WATCH"]
    entered_a = [s for s in actionable if s.entry_hit]
    correct_a = [s for s in actionable if s.outcome == "CORRECT"]
    wrong_a = [s for s in actionable if s.outcome == "WRONG"]
    close_a = [s for s in actionable if s.outcome == "CLOSE_CALL"]
    wr = len(correct_a) / len(entered_a) * 100 if entered_a else 0

    # Strategy breakdown
    strat_summary = []
    strategies = sorted(set(s.strategy for s in actionable if s.strategy))
    for strat in strategies:
        ss = [s for s in actionable if s.strategy == strat]
        n_e = sum(1 for s in ss if s.entry_hit)
        n_w = sum(1 for s in ss if s.outcome == "CORRECT")
        swr = f"{n_w}/{n_e}" if n_e else "0/0"
        strat_summary.append(f"{strat}: {swr} wins")

    # Nifty context
    nifty = market_context.get("nifty", {})
    nifty_line = (f"Nifty {nifty.get('direction', '?')} {nifty.get('change_pct', 0):+.1f}%, "
                  f"range {nifty.get('range_vs_atr', 0):.1f}x ATR") if nifty else "N/A"

    # Watch signals summary
    watch_entered = [s for s in watch if s.entry_hit]
    watch_correct = [s for s in watch if s.outcome == "CORRECT"]
    watch_line = (f"{len(watch)} WATCH signals ({len(watch_entered)} entered, "
                  f"{len(watch_correct)} would have won)")

    prompt = f"""Analyze this intraday backtest session and provide actionable insights.

SESSION SUMMARY:
- Actionable signals (STRONG/ACTIVE): {len(actionable)} total, {len(entered_a)} entered
- Win rate: {wr:.0f}% ({len(correct_a)} correct, {len(wrong_a)} wrong, {len(close_a)} close calls)
- Per strategy: {' | '.join(strat_summary) if strat_summary else 'N/A'}
- {watch_line}

MARKET CONDITIONS:
- {nifty_line}
- VIX: {market_context.get('vix', {}).get('value', 'N/A')} ({market_context.get('vix', {}).get('regime', 'normal')})
- Day type: {market_context.get('day_type', 'unknown')}

WRONG/CLOSE CALLS:
"""
    for s in all_signals:
        if s.outcome in ("WRONG", "CLOSE_CALL") and s.signal_tier in ("STRONG", "ACTIVE"):
            clean = s.symbol.replace(".NS", "")
            prompt += (f"- {clean} {s.strategy} {s.direction}: "
                       f"MFE {s.mfe_pct:+.1f}%, MAE {s.mae_pct:.1f}%, "
                       f"exit {s.exit_reason}, score {s.score:.0%}\n")

    prompt += """
Provide analysis in these sections (keep each to 2-3 sentences):
1. **What worked**: Which strategies/conditions produced winners
2. **What failed**: Common patterns among losing signals
3. **Market fit**: How well did the scanner adapt to today's market conditions
4. **Key takeaways**: 2-3 specific, actionable improvements"""

    response = call_llm(
        [{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return response


# ── Report Generation ─────────────────────────────────────────────────

def generate_report(target_date, all_signals, use_llm=False, market_context=None):
    """Generate markdown backtest report with signal-by-signal narratives.

    Args:
        target_date: date object for the backtest day
        all_signals: list of SignalResult instances
        use_llm: if True, add LLM-powered reasoning per signal and session analysis
        market_context: dict from _capture_market_context() for LLM prompts

    Returns:
        Markdown report string
    """
    market_context = market_context or {}
    lines = []
    td = target_date.isoformat()

    lines.append(f"# Intraday Backtest — {td}\n")

    # ── Summary ──
    # Headline stats use only actionable signals (STRONG/ACTIVE)
    actionable_signals = [s for s in all_signals if s.signal_tier in ("STRONG", "ACTIVE")]
    watch_signals = [s for s in all_signals if s.signal_tier == "WATCH"]

    total = len(actionable_signals)
    entered = sum(1 for s in actionable_signals if s.entry_hit)
    correct = sum(1 for s in actionable_signals if s.outcome == "CORRECT")
    wrong = sum(1 for s in actionable_signals if s.outcome == "WRONG")
    no_entry = sum(1 for s in actionable_signals if s.outcome == "NO_ENTRY")
    close_calls = sum(1 for s in actionable_signals if s.outcome == "CLOSE_CALL")
    win_rate = correct / entered * 100 if entered > 0 else 0

    # Avg RR achieved (actionable only)
    rr_achieved_list = []
    for s in actionable_signals:
        if s.entry_hit and s.entry_price > 0:
            stop_dist = abs(s.entry_price - s.stop_price)
            if stop_dist > 0 and s.exit_price > 0:
                if s.direction == "long":
                    actual_rr = (s.exit_price - s.entry_price) / stop_dist
                else:
                    actual_rr = (s.entry_price - s.exit_price) / stop_dist
                rr_achieved_list.append(actual_rr)
    avg_rr = np.mean(rr_achieved_list) if rr_achieved_list else 0

    lines.append("## Summary\n")
    lines.append(f"- Phases simulated: post_market(T-1), pre_market(T), live x4")
    lines.append(f"- Actionable signals (STRONG/ACTIVE): {total} | "
                  f"Entered: {entered} | Correct: {correct} | Wrong: {wrong} | "
                  f"No-entry: {no_entry}")
    lines.append(f"- Win rate (entered): {win_rate:.1f}% | "
                  f"Avg RR achieved: {avg_rr:.1f}")
    lines.append(f"- Close calls (wrong but MFE>50% of target): {close_calls}")

    # WATCH stats (separate from headline)
    if watch_signals:
        w_entered = sum(1 for s in watch_signals if s.entry_hit)
        w_correct = sum(1 for s in watch_signals if s.outcome == "CORRECT")
        w_wr = w_correct / w_entered * 100 if w_entered > 0 else 0
        lines.append(f"- WATCH signals: {len(watch_signals)} total | "
                      f"Entered: {w_entered} | Won: {w_correct} ({w_wr:.0f}%) — "
                      f"tracked for learning, not in headline stats")
    lines.append("")

    # Market context summary (if available)
    nifty_ctx = market_context.get("nifty", {})
    if nifty_ctx:
        vix_ctx = market_context.get("vix", {})
        lines.append("**Market context:**")
        lines.append(f"- Nifty: {nifty_ctx.get('direction', '?')} "
                      f"({nifty_ctx.get('change_pct', 0):+.1f}%), "
                      f"range {nifty_ctx.get('range_vs_atr', 0):.1f}x ATR")
        if vix_ctx.get("value"):
            lines.append(f"- VIX: {vix_ctx['value']} ({vix_ctx.get('regime', 'normal')})")
        if market_context.get("day_type"):
            lines.append(f"- Day type: {market_context['day_type']}")
        lines.append("")

    # ── Signal-by-Signal Replay ──
    lines.append("---\n")
    lines.append("## Signal-by-Signal Replay\n")
    lines.append("> For each signal: what the scanner suggested, and what actually "
                  "happened in the market. This is the core of the backtest — "
                  "read each one to build intuition.\n")

    # Group by phase, ordered chronologically (actionable signals only for replay)
    phase_order = ["post_market_t-1", "pre_market"]
    live_phases = sorted(set(s.phase for s in all_signals
                            if s.phase.startswith("live_")))
    phase_order.extend(live_phases)

    signal_num = 0
    for phase in phase_order:
        phase_sigs = [s for s in actionable_signals if s.phase == phase]
        if not phase_sigs:
            continue
        plabel = phase_label(phase)
        phase_entered = sum(1 for s in phase_sigs if s.entry_hit)
        phase_won = sum(1 for s in phase_sigs if s.outcome == "CORRECT")
        lines.append(f"---\n")
        lines.append(f"#### Scan: {plabel} — {len(phase_sigs)} signal(s), "
                      f"{phase_entered} entered, {phase_won} won\n")

        for s in phase_sigs:
            signal_num += 1
            write_signal_narrative(lines, s, signal_num)

            # LLM reasoning for signals that entered
            if use_llm and s.entry_hit and s.outcome in ("CORRECT", "WRONG", "CLOSE_CALL"):
                reasoning = _generate_signal_reasoning(s, market_context)
                if reasoning and not reasoning.startswith("[AI Error"):
                    lines.append(f"**AI Analysis:**\n")
                    lines.append(f"> {reasoning}\n")

    # ── WATCH Signals Section (if any) ──
    if watch_signals:
        lines.append("---\n")
        lines.append("## WATCH Signals (Borderline — Not in Headline Stats)\n")
        lines.append("> These signals were borderline (WATCH tier). Tracked for "
                      "learning but not counted in win-rate stats.\n")
        watch_num = 0
        for s in watch_signals:
            watch_num += 1
            clean = s.symbol.replace(".NS", "")
            icon = {"CORRECT": "✅", "WRONG": "❌", "CLOSE_CALL": "⚠️",
                    "NO_ENTRY": "⏭️"}.get(s.outcome, "❓")
            lines.append(f"- **{clean}** {s.strategy} {_action_label(s.direction)} "
                          f"({phase_label(s.phase)}) — Score: {s.score:.0%}, "
                          f"RR: {s.rr_ratio:.1f} → {s.outcome} {icon}")
            if s.entry_hit:
                lines.append(f"  MFE: {s.mfe_pct:+.1f}% ({s.mfe_of_target:.0f}% of target), "
                              f"MAE: {s.mae_pct:.1f}%, exit: {s.exit_reason}")
            if s.reason:
                lines.append(f"  Reason: {s.reason}")
        lines.append("")

    # ── Per-Strategy Breakdown ──
    strategies = sorted(set(s.strategy for s in actionable_signals if s.strategy))
    if strategies:
        lines.append("---\n")
        lines.append("## Per-Strategy Breakdown\n")
        lines.append("| Strategy | Signals | Entered | Win Rate | "
                      "Avg MFE | Avg MAE | Avg RR Achieved |")
        lines.append("|----------|---------|---------|----------|"
                      "---------|---------|-----------------|")
        for strat in strategies:
            ss = [s for s in actionable_signals if s.strategy == strat]
            n = len(ss)
            n_entered = sum(1 for s in ss if s.entry_hit)
            n_won = sum(1 for s in ss if s.outcome == "CORRECT")
            wr = f"{n_won/n_entered*100:.0f}%" if n_entered > 0 else "N/A"
            avg_mfe = np.mean([s.mfe_pct for s in ss if s.entry_hit]) if n_entered else 0
            avg_mae = np.mean([s.mae_pct for s in ss if s.entry_hit]) if n_entered else 0
            rrs = []
            for s in ss:
                if s.entry_hit and abs(s.entry_price - s.stop_price) > 0:
                    sd = abs(s.entry_price - s.stop_price)
                    if s.direction == "long":
                        rrs.append((s.exit_price - s.entry_price) / sd)
                    else:
                        rrs.append((s.entry_price - s.exit_price) / sd)
            avg_rr_s = np.mean(rrs) if rrs else 0
            lines.append(
                f"| {strat} | {n} | {n_entered} | {wr} | "
                f"{avg_mfe:.1f}% | {avg_mae:.1f}% | {avg_rr_s:.1f} |"
            )
        lines.append("")

    # ── Absurd Target Flags ──
    flagged = []
    for s in actionable_signals:
        if s.entry_price > 0:
            tgt_pct = abs(s.target_price - s.entry_price) / s.entry_price * 100
            if tgt_pct > 5.0:
                flagged.append((s, tgt_pct))
    if flagged:
        lines.append("## ⚠️ Absurd Target Flags\n")
        lines.append("> Signals where target was >5% from entry — likely too "
                      "aggressive for intraday. Review strategy parameters.\n")
        lines.append("| Stock | Strategy | Dir | Entry | Target | Target % | "
                      "Outcome | MFE % of Target |")
        lines.append("|-------|----------|-----|-------|--------|----------|"
                      "---------|-----------------|")
        for s, tgt_pct in flagged:
            clean = s.symbol.replace(".NS", "")
            mfe_t = f"{s.mfe_of_target:.0f}%" if s.entry_hit else "—"
            lines.append(
                f"| {clean} | {s.strategy} | {_action_label(s.direction)} | "
                f"₹{s.entry_price:,.2f} | ₹{s.target_price:,.2f} | "
                f"{tgt_pct:.1f}% | {s.outcome} | {mfe_t} |"
            )
        lines.append("")

    # ── Wrong Calls Analysis ──
    wrong_sigs = [s for s in actionable_signals
                  if s.outcome in ("WRONG", "CLOSE_CALL")]
    if wrong_sigs:
        lines.append("## Wrong Calls Analysis\n")
        lines.append("| Stock | Phase | Strategy | Action | Entry | Target | Stop | "
                      "Exit | MFE% | How Close |")
        lines.append("|-------|-------|----------|--------|-------|--------|------|"
                      "-----|------|-----------|")
        for s in wrong_sigs:
            clean = s.symbol.replace(".NS", "")
            how_close = f"{s.mfe_of_target:.0f}% of target"
            lines.append(
                f"| {clean} | {s.phase} | {s.strategy} | {_action_label(s.direction)} | "
                f"{s.entry_price:.2f} | {s.target_price:.2f} | {s.stop_price:.2f} | "
                f"{s.exit_price:.2f} | {s.mfe_pct:+.1f}% | {how_close} |"
            )
        lines.append("")

    # ── LLM Session Analysis ──
    if use_llm:
        print("  Generating AI session analysis...")
        session_analysis = _generate_session_analysis(all_signals, market_context)
        if session_analysis and not session_analysis.startswith("[AI Error"):
            lines.append("---\n")
            lines.append("## AI Session Analysis\n")
            lines.append(session_analysis)
            lines.append("")

    return "\n".join(lines)
