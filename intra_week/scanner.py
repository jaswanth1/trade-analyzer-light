"""
IntraWeek Scanner — identifies stocks with high probability of 10-20% upside
within a single trading week.

Runs three sub-strategies:
  1. Oversold Recovery
  2. Volatility Compression Breakout
  3. Weekly Context Recovery

Usage:
    python -m intra_week.scanner           # best on Mon/Tue
    python -m intra_week.scanner --force   # run any day
"""

import argparse
import logging
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from common.data import (
    fetch_yf, fetch_bulk_single, BENCHMARK, PROJECT_ROOT,
    load_universe_for_tier, INTRAWEEK_REPORT_DIR,
)
from common.indicators import compute_atr, compute_atr_percentile, compute_relative_performance
from common.market import (
    fetch_india_vix, vix_position_scale, detect_nifty_regime,
    estimate_institutional_flow, compute_market_context_scores,
)
from common.risk import (
    compute_position_size, compute_correlation_clusters,
    compute_individual_beta_scale,
)
from common.display import fmt, box_top, box_mid, box_bot, box_line, W

from intra_week.strategies import (
    evaluate_oversold_recovery, evaluate_vol_compression, evaluate_weekly_context,
)
from intra_week.convergence import compute_weekly_convergence, compute_weekly_hit_rate
from intra_week.scoring import (
    compute_composite_score, compute_regime_alignment, rank_signals,
)
from intra_week.weekly_context import get_weekly_context
from intra_week.explanations import (
    generate_explanation, generate_risk_notes,
    STRATEGY_SHORT, STRATEGY_DESCRIPTIONS,
)

warnings.filterwarnings("ignore")
log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── Constants ─────────────────────────────────────────────────────────────

MAX_INTRAWEEK_POSITIONS = 5
MAX_INTRAWEEK_CAPITAL_PCT = 40.0
MAX_SECTOR_EXPOSURE = 2
DEFAULT_CAPITAL = 1_000_000
MIN_SCORE = 0.50

# Universe — use intra_week tier if available, fallback to btst
try:
    TICKERS = load_universe_for_tier("intra_week")
except Exception:
    TICKERS = load_universe_for_tier("btst")

STRATEGY_FNS = [
    evaluate_oversold_recovery,
    evaluate_vol_compression,
    evaluate_weekly_context,
]


def _format_rupee(val):
    """Format number as Indian rupee amount."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "N/A"
    if val >= 1_00_000:
        return f"{val:,.0f}"
    return f"{val:,.2f}"


# ── Main Scanner Logic ────────────────────────────────────────────────────

def scan(force=False):
    """Run the IntraWeek scan. Returns (ranked_candidates, market_ctx, weekly_ctx)."""
    now = datetime.now(IST)
    weekly_ctx = get_weekly_context(now.date())

    # Day-of-week check — best on Mon/Tue, ok on Wed, skip Thu/Fri
    if not force and weekly_ctx["day_of_week"] >= 3:
        print(f"[IntraWeek] Today is {weekly_ctx['day_name']} — "
              f"scanner works best Mon-Wed. Use --force to override.")
        return [], {}, weekly_ctx

    symbols = list(TICKERS.keys())
    if not symbols:
        print("[IntraWeek] No tickers in universe.")
        return [], {}, weekly_ctx

    # ── Market context ──
    vix_val, vix_regime = fetch_india_vix()
    nifty_daily = fetch_yf(BENCHMARK, period="3mo", interval="1d")
    nifty_regime, beta_scale, regime_strength = detect_nifty_regime(nifty_daily)
    inst_flow = estimate_institutional_flow()

    market_ctx = {
        "vix_val": vix_val,
        "vix_regime": vix_regime,
        "nifty_regime": nifty_regime,
        "beta_scale": beta_scale,
        "regime_strength": regime_strength,
        "inst_flow": inst_flow,
        "remaining_trading_days": weekly_ctx["remaining_trading_days"],
    }

    # VIX stress override
    if vix_regime == "stress" and not force:
        print(f"[IntraWeek] VIX STRESS ({vix_val}) — scanner suspended. Use --force to override.")
        return [], market_ctx, weekly_ctx

    # ── Fetch daily data for all symbols ──
    print(f"[IntraWeek] Fetching data for {len(symbols)} stocks...")
    all_daily = fetch_bulk_single(symbols, period="3mo", interval="1d",
                                  max_workers=10, label="IntraWeek daily")

    # Fetch sector data
    sectors_needed = set()
    for sym, cfg in TICKERS.items():
        sec = cfg.get("sector", "")
        if sec:
            sectors_needed.add(sec)

    sector_data = {}
    for sec_sym in sectors_needed:
        try:
            sector_data[sec_sym] = fetch_yf(sec_sym, period="3mo", interval="1d")
        except Exception:
            pass

    # ── Evaluate each symbol across all strategies ──
    print(f"[IntraWeek] Evaluating strategies...")
    candidates = []

    for sym in symbols:
        daily_df = all_daily.get(sym, pd.DataFrame())
        if daily_df.empty or len(daily_df) < 50:
            continue

        cfg = TICKERS.get(sym, {})
        sec_sym = cfg.get("sector", "")
        sec_df = sector_data.get(sec_sym, pd.DataFrame())

        for strategy_fn in STRATEGY_FNS:
            try:
                result = strategy_fn(
                    sym, daily_df, nifty_daily, sec_df, weekly_ctx, market_ctx,
                )
            except Exception as e:
                log.debug("Strategy error %s/%s: %s", sym, strategy_fn.__name__, e)
                continue

            if result is None:
                continue

            # Enrich with name/sector
            result["name"] = cfg.get("name", sym)
            result["sector"] = sec_sym

            # Convergence scoring
            try:
                from intraday.regime import classify_symbol_regime
                sym_regime = classify_symbol_regime(daily_df, pd.DataFrame(), nifty_daily)
            except Exception:
                sym_regime = {}

            convergence = compute_weekly_convergence(daily_df, sym_regime, nifty_daily)
            hit_rate = compute_weekly_hit_rate(daily_df)
            regime_score = compute_regime_alignment(sym_regime)

            # Composite score
            scoring = compute_composite_score(
                result, convergence, hit_rate, regime_score, market_ctx,
            )
            result["scoring"] = scoring
            result["score"] = scoring["score"]
            result["tier"] = scoring["tier"]

            # Generate explanation
            result["reasons"] = generate_explanation(result)
            result["risk_notes"] = generate_risk_notes(result)

            if scoring["tier"] != "AVOID":
                candidates.append(result)

    # ── Rank and filter ──
    ranked = rank_signals(candidates)

    # Enforce sector diversification
    ranked = _apply_sector_cap(ranked, MAX_SECTOR_EXPOSURE)

    # Limit to top N
    ranked = ranked[:MAX_INTRAWEEK_POSITIONS * 2]  # keep double for watch list

    return ranked, market_ctx, weekly_ctx


def _apply_sector_cap(candidates, max_per_sector):
    """Limit candidates per sector to prevent concentration."""
    sector_count = {}
    filtered = []
    for c in candidates:
        sec = c.get("sector", "unknown")
        count = sector_count.get(sec, 0)
        if count < max_per_sector or c.get("tier") == "STRONG":
            filtered.append(c)
            sector_count[sec] = count + 1
    return filtered


# ── Dashboard Rendering ───────────────────────────────────────────────────

def render_dashboard(candidates, market_ctx, weekly_ctx):
    """Render terminal dashboard."""
    now = datetime.now(IST)
    lines = []

    lines.append(box_top())
    lines.append(box_line(
        f"INTRA-WEEK SCANNER  |  {now.strftime('%a %d %b %Y')}  |  "
        f"{weekly_ctx['remaining_trading_days']} trading days left"
    ))
    lines.append(box_mid())

    # Market context
    vix_str = f"VIX: {market_ctx.get('vix_val', 'N/A')} ({market_ctx.get('vix_regime', '?').upper()})"
    regime = market_ctx.get('nifty_regime', 'unknown').upper()
    flow = market_ctx.get('inst_flow', 'N/A')
    lines.append(box_line(f"Market: {regime} | {vix_str} | Flow: {flow}"))

    week_flags = []
    if weekly_ctx.get("is_holiday_week"):
        week_flags.append("Holiday")
    if weekly_ctx.get("is_expiry_week"):
        week_flags.append("Expiry")
    week_type = " + ".join(week_flags) if week_flags else "Normal"
    lines.append(box_line(f"Week: {week_type} | Max positions: {MAX_INTRAWEEK_POSITIONS}"))
    lines.append(box_mid())

    # Group by tier
    strong = [c for c in candidates if c.get("tier") == "STRONG"]
    active = [c for c in candidates if c.get("tier") == "ACTIVE"]
    watch = [c for c in candidates if c.get("tier") == "WATCH"]

    if not strong and not active and not watch:
        lines.append(box_line("No IntraWeek candidates found today."))
        lines.append(box_bot())
        return "\n".join(lines)

    idx = 1
    if strong:
        lines.append(box_line())
        lines.append(box_line("* STRONG SIGNALS"))
        lines.append(box_line("-" * (W - 4)))
        for c in strong:
            idx = _render_candidate(lines, c, idx)

    if active:
        lines.append(box_line())
        lines.append(box_line("+ ACTIVE SIGNALS"))
        lines.append(box_line("-" * (W - 4)))
        for c in active:
            idx = _render_candidate(lines, c, idx)

    if watch:
        lines.append(box_line())
        lines.append(box_line("o WATCHLIST"))
        lines.append(box_line("-" * (W - 4)))
        for c in watch:
            idx = _render_candidate(lines, c, idx)

    # Summary
    lines.append(box_mid())
    lines.append(box_line(
        f"STRONG: {len(strong)} | ACTIVE: {len(active)} | WATCH: {len(watch)}"
    ))
    lines.append(box_bot())

    return "\n".join(lines)


def _render_candidate(lines, c, idx):
    """Render a single candidate in the dashboard."""
    sym = c["symbol"].replace(".NS", "")
    score = c.get("score", 0)
    strategy = STRATEGY_SHORT.get(c.get("strategy", ""), c.get("strategy", ""))
    upside = c.get("scoring", {}).get("expected_upside", (0, 0))

    lines.append(box_line(f"{idx}. {sym:<18} Score: {score:.0%}"))
    lines.append(box_line(f"   Strategy: {strategy}"))
    lines.append(box_line(f"   Expected Upside: {upside[0]:.0f}-{upside[1]:.0f}%"))
    lines.append(box_line(
        f"   Entry: {_format_rupee(c.get('entry'))}  |  "
        f"Target: {_format_rupee(c.get('target_price'))}  |  "
        f"Stop: {_format_rupee(c.get('stop_price'))}"
    ))

    reasons = c.get("reasons", [])
    if reasons:
        lines.append(box_line("   Reasons:"))
        for r in reasons[:5]:
            lines.append(box_line(f"     - {r}"))

    risk_notes = c.get("risk_notes", [])
    if risk_notes:
        lines.append(box_line(f"   Risk: {', '.join(risk_notes)}"))

    lines.append(box_line())
    return idx + 1


# ── Report Generation ─────────────────────────────────────────────────────

def write_report(candidates, market_ctx, weekly_ctx):
    """Write detailed markdown report."""
    INTRAWEEK_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(IST)
    report_path = INTRAWEEK_REPORT_DIR / f"intraweek_{now.strftime('%Y-%m-%d')}.md"

    lines = []
    lines.append(f"# IntraWeek Scanner — {now.strftime('%Y-%m-%d %H:%M')} IST\n")

    # Market context
    vix_val = market_ctx.get("vix_val", "N/A")
    vix_regime = market_ctx.get("vix_regime", "?")
    nifty_regime = market_ctx.get("nifty_regime", "unknown")
    inst_flow = market_ctx.get("inst_flow", "N/A")

    lines.append(f"**Nifty**: {nifty_regime.upper()} | "
                 f"**VIX**: {vix_val} ({vix_regime}) | "
                 f"**Institutional Flow**: {inst_flow}")

    week_flags = []
    if weekly_ctx.get("is_holiday_week"):
        week_flags.append("Holiday")
    if weekly_ctx.get("is_expiry_week"):
        week_flags.append(f"Expiry ({weekly_ctx.get('expiry_date', '')})")
    week_type = " + ".join(week_flags) if week_flags else "Normal"

    lines.append(f"**Week**: {week_type} | "
                 f"**Day**: {weekly_ctx.get('day_name', '?')} | "
                 f"**Remaining**: {weekly_ctx.get('remaining_trading_days', '?')} trading days\n")

    # Strategy descriptions
    lines.append("## Strategies\n")
    for key, desc in STRATEGY_DESCRIPTIONS.items():
        lines.append(f"- **{STRATEGY_SHORT[key]}**: {desc}")
    lines.append("")

    # Candidates by tier
    strong = [c for c in candidates if c.get("tier") == "STRONG"]
    active = [c for c in candidates if c.get("tier") == "ACTIVE"]
    watch = [c for c in candidates if c.get("tier") == "WATCH"]

    if strong:
        lines.append("## STRONG Signals\n")
        for c in strong:
            _write_candidate_md(lines, c)

    if active:
        lines.append("## ACTIVE Signals\n")
        for c in active:
            _write_candidate_md(lines, c)

    if watch:
        lines.append("## Watchlist\n")
        for c in watch:
            _write_candidate_md(lines, c)

    if not strong and not active and not watch:
        lines.append("## No Candidates\n")
        lines.append("No stocks met IntraWeek criteria today.\n")

    # Summary table
    lines.append("## Summary\n")
    lines.append("| # | Symbol | Strategy | Score | Upside | Entry | Target | Stop | Tier |")
    lines.append("|---|--------|----------|-------|--------|-------|--------|------|------|")
    for i, c in enumerate(candidates, 1):
        sym = c["symbol"].replace(".NS", "")
        strat = STRATEGY_SHORT.get(c.get("strategy", ""), "?")
        score = c.get("score", 0)
        up = c.get("scoring", {}).get("expected_upside", (0, 0))
        lines.append(
            f"| {i} | {sym} | {strat} | {score:.0%} | "
            f"{up[0]:.0f}-{up[1]:.0f}% | {_format_rupee(c.get('entry'))} | "
            f"{_format_rupee(c.get('target_price'))} | "
            f"{_format_rupee(c.get('stop_price'))} | {c.get('tier', '?')} |"
        )
    lines.append("")

    report_path.write_text("\n".join(lines))
    return report_path


def _write_candidate_md(lines, c):
    """Write a single candidate to the markdown report."""
    sym = c["symbol"].replace(".NS", "")
    name = c.get("name", sym)
    strategy = STRATEGY_SHORT.get(c.get("strategy", ""), c.get("strategy", ""))
    score = c.get("score", 0)
    scoring = c.get("scoring", {})
    upside = scoring.get("expected_upside", (0, 0))

    lines.append(f"### {sym} — {name}\n")
    lines.append(f"- **Strategy**: {strategy}")
    lines.append(f"- **Score**: {score:.0%} "
                 f"(base: {scoring.get('base_score', 0):.0%}, "
                 f"convergence: {scoring.get('convergence_score', 0):.0f}/100, "
                 f"hit rate: {scoring.get('historical_hit_rate', 0):.0f}%, "
                 f"regime: {scoring.get('regime_score', 0):.0%})")
    lines.append(f"- **Expected Upside**: {upside[0]:.0f}–{upside[1]:.0f}%")
    lines.append(f"- **Entry**: {_format_rupee(c.get('entry'))} | "
                 f"**Target**: {_format_rupee(c.get('target_price'))} ({c.get('target_pct', 0):.1f}%) | "
                 f"**Stop**: {_format_rupee(c.get('stop_price'))} ({c.get('stop_pct', 0):.1f}%)")

    reasons = c.get("reasons", [])
    if reasons:
        lines.append("\n**Reasons**:\n")
        for r in reasons:
            lines.append(f"- {r}")

    risk_notes = c.get("risk_notes", [])
    if risk_notes:
        lines.append(f"\n**Risk**: {', '.join(risk_notes)}")

    # Key metrics
    metrics = c.get("metrics", {})
    if metrics:
        lines.append("\n**Metrics**:\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for k, v in metrics.items():
            if isinstance(v, float):
                lines.append(f"| {k} | {v:.2f} |")
            else:
                lines.append(f"| {k} | {v} |")

    lines.append("")


# ── CLI Entry Point ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IntraWeek Scanner")
    parser.add_argument("--force", action="store_true",
                        help="Run regardless of day-of-week or VIX")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    candidates, market_ctx, weekly_ctx = scan(force=args.force)

    # Render dashboard
    dashboard = render_dashboard(candidates, market_ctx, weekly_ctx)
    print(dashboard)

    # Write report
    if candidates:
        report_path = write_report(candidates, market_ctx, weekly_ctx)
        print(f"\nReport saved: {report_path}")


if __name__ == "__main__":
    main()
