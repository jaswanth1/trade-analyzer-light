#!/usr/bin/env python3
"""
Morning Low Recovery (MLR) Config Generator

Precomputes per-ticker MLR statistics from 60 days of 5-min OHLCV data
(yfinance/Upstox intraday limit) plus 1 year of daily data for broader stats:
recovery probabilities by time bucket, optimal entry/target/stop from
historical MAE/MFE, DOW/month seasonality, Monte Carlo CIs.

Output: mlr_config.yaml consumed by the live scanner for ticker-specific
calibration.

Architecture follows scalp/config.py pattern:
  fetch → compute → validate → YAML + documentation

Usage: python -m intraday.mlr_config
"""

import argparse
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

log = logging.getLogger(__name__)

from common.data import (
    PROJECT_ROOT, BENCHMARK, fetch_yf, load_universe_for_tier,
)

TICKERS = load_universe_for_tier("intraday")
from common.indicators import compute_atr, _to_ist

from intraday.mlr_stats import (
    # Constants re-exported for use in this module
    MONTE_CARLO_ITERS,
    OOS_TRAIN_RATIO,
    ROUND_TRIP_COST_PCT,
    MORNING_CUTOFF_HOUR,
    MORNING_CUTOFF_MIN,
    SETTLE_TIME,
    PHASE_WINDOWS,
    GAP_UP_LARGE,
    GAP_UP_SMALL,
    GAP_DOWN_SMALL,
    GAP_DOWN_LARGE,
    MIN_PROFILE_PREDICTABILITY,
    MIN_PROFILE_SAMPLE,
    DOW_NAMES,
    # Compute functions
    compute_morning_low_stats,
    compute_ev_combos,
    validate_oos,
    compute_mae_analysis,
    monte_carlo_ci,
    compute_dow_month_stats,
    compute_open_type_profiles,
    compute_phase_heatmap,
    _sanitize,
)

# ── Tunable Constants ────────────────────────────────────────────────

MIN_SAMPLE = 15              # minimum trading days with morning low for enable
MIN_WIN_RATE = 50.0          # minimum recovery win rate to enable
EV_THRESHOLD = 0.0           # minimum EV to enable

INTRADAY_DIR = PROJECT_ROOT / "intraday"
CONFIG_PATH = INTRADAY_DIR / "mlr_config.yaml"
DOC_PATH = INTRADAY_DIR / "mlr_config_guide.md"


# ── Step 8: Should-enable decision ───────────────────────────────────

def should_enable(best_combo, oos_result, mc_result, sample_size):
    """Enable if: EV > 0, WR >= 50%, sample >= 15, MC lower CI > -0.5%, OOS not fragile."""
    if best_combo is None:
        return False
    if sample_size < MIN_SAMPLE:
        return False
    if best_combo["ev"] <= EV_THRESHOLD:
        return False
    if best_combo["win_rate"] < MIN_WIN_RATE:
        return False
    if oos_result.get("degraded", True):
        return False
    if mc_result.get("ev_ci_lower", -999) < -0.5:
        return False
    return True


# ── Step 9: Edge strength score ──────────────────────────────────────

def compute_edge_strength(best_combo, oos_result, mc_result, sample_size):
    """Composite edge strength 1-5."""
    if best_combo is None:
        return 0

    score = 0.0

    # EV contribution (0-1.5)
    ev = best_combo["ev"]
    if ev > 0.5:
        score += 1.5
    elif ev > 0.2:
        score += 1.0
    elif ev > 0:
        score += 0.5

    # Win rate contribution (0-1.0)
    wr = best_combo["win_rate"]
    if wr >= 65:
        score += 1.0
    elif wr >= 55:
        score += 0.5

    # Sample size contribution (0-1.0)
    if sample_size >= 50:
        score += 1.0
    elif sample_size >= 30:
        score += 0.5

    # OOS validation (0-1.0)
    if oos_result.get("oos_valid") and not oos_result.get("degraded"):
        score += 0.75
        if oos_result.get("oos_ev", 0) > 0.1:
            score += 0.25

    # Monte Carlo stability (0-0.5)
    if mc_result.get("ev_ci_lower", -1) > 0:
        score += 0.5

    return min(5, max(1, round(score)))


# ── Main pipeline per ticker ─────────────────────────────────────────

def process_ticker(symbol, cfg):
    """Full MLR pipeline for one ticker. Returns config dict or None."""
    log.info("Processing %s...", symbol)

    # Fetch data: 60d of 5-min (yfinance/Upstox limit) + 1y daily for stats
    # fetch_yf handles cache -> yfinance -> Upstox fallback transparently
    try:
        intra_raw = fetch_yf(symbol, period="60d", interval="5m")
        log.debug(
            "%s: intra_raw — %d rows, index_type=%s, tz=%s",
            symbol, len(intra_raw) if not intra_raw.empty else 0,
            type(intra_raw.index).__name__,
            getattr(intra_raw.index, 'tz', None),
        )
        intra_df = _to_ist(intra_raw) if not intra_raw.empty else intra_raw
        log.debug(
            "%s: after _to_ist — %d rows, index_type=%s, tz=%s",
            symbol, len(intra_df) if not intra_df.empty else 0,
            type(intra_df.index).__name__,
            getattr(intra_df.index, 'tz', None),
        )
        daily_df = fetch_yf(symbol, period="1y", interval="1d")
        log.debug(
            "%s: daily_df — %d rows, index_type=%s",
            symbol, len(daily_df) if not daily_df.empty else 0,
            type(daily_df.index).__name__,
        )
    except Exception as e:
        log.warning("%s: SKIP (fetch error: %s)", symbol, e)
        return None

    if intra_df.empty or daily_df.empty or len(daily_df) < 60:
        log.info(
            "%s: SKIP (insufficient data — intra=%d, daily=%d)",
            symbol, len(intra_df) if not intra_df.empty else 0,
            len(daily_df) if not daily_df.empty else 0,
        )
        return None

    # Step 1a: Morning low stats (default cutoff for strategy use)
    # Finds the low within 10:00-11:30 morning window on each day
    stats_df = compute_morning_low_stats(intra_df, daily_df)
    if stats_df.empty or len(stats_df) < MIN_SAMPLE:
        log.info(
            "%s: SKIP (%d morning low days < %d min)",
            symbol, len(stats_df) if not stats_df.empty else 0, MIN_SAMPLE,
        )
        return None

    # Step 1b: Full-session low analysis (wide cutoff) — for phase window discovery
    # Discovers when THIS stock's morning window low forms across all phases
    full_session_df = compute_morning_low_stats(
        intra_df, daily_df, low_cutoff_hour=15, low_cutoff_min=15,
    )

    sample_size = len(stats_df)

    # Step 2: EV combos
    ev_result = compute_ev_combos(stats_df)
    best = ev_result["best"]

    # Step 3: OOS validation
    oos = validate_oos(stats_df, best)

    # Step 4: MAE
    mae = compute_mae_analysis(stats_df)

    # Step 5: Monte Carlo
    mc = monte_carlo_ci(stats_df, best)

    # Step 6: DOW/month
    seasonality = compute_dow_month_stats(stats_df)

    # Step 7: Predictability-scored open-type profiles
    phase_stats = compute_open_type_profiles(stats_df, full_session_df)

    # Step 8: Enable decision
    enabled = should_enable(best, oos, mc, sample_size)

    # Step 9: Edge strength
    edge = compute_edge_strength(best, oos, mc, sample_size)

    # Summary stats
    avg_recovery_close = round(float(stats_df["recovery_to_close_pct"].mean()), 2)
    avg_recovery_post_low_high = round(float(stats_df["recovery_to_post_low_high_pct"].mean()), 2)
    pct_above_1 = round(float((stats_df["recovery_to_close_pct"] > 1.0).mean() * 100), 1)
    pct_above_3 = round(float((stats_df["recovery_to_close_pct"] > 3.0).mean() * 100), 1)

    # ATR-normalized summary stats
    has_norm = "drop_norm" in stats_df.columns and stats_df["drop_norm"].sum() > 0
    avg_drop_norm = round(float(stats_df["drop_norm"].mean()), 3) if has_norm else 0.0
    avg_high_norm = round(float(stats_df["high_norm"].mean()), 3) if has_norm else 0.0
    avg_recovery_norm = round(float(stats_df["recovery_norm"].mean()), 3) if has_norm else 0.0

    # DOW favorability for today
    today_dow = datetime.now().weekday()
    dow_name = DOW_NAMES.get(today_dow, "")
    dow_data = seasonality.get("dow", {}).get(dow_name, {})
    dow_favorable = dow_data.get("win_rate", 50) > 55

    result = {
        "enabled": enabled,
        "name": cfg.get("name", symbol),
        "edge_strength": edge,
        "sample_size": sample_size,
        "avg_recovery_to_close_pct": avg_recovery_close,
        "avg_recovery_to_post_low_high_pct": avg_recovery_post_low_high,
        "pct_recovery_above_1": pct_above_1,
        "pct_recovery_above_3": pct_above_3,
        "mae_p90": mae.get("mae_p90", 0),
        "mae_median": mae.get("mae_median", 0),
        "dow_favorable": dow_favorable,
        # ATR-normalized summary
        "avg_drop_norm": avg_drop_norm,
        "avg_high_norm": avg_high_norm,
        "avg_recovery_norm": avg_recovery_norm,
    }

    if best:
        result.update({
            "optimal_entry_delay": best["entry_delay"],
            "optimal_stop_pct": best["stop_pct"],
            "optimal_target_pct": best["target_pct"],
            "ev": best["ev"],
            "win_rate": best["win_rate"],
        })

    if oos.get("oos_valid"):
        result["oos_win_rate"] = oos["oos_win_rate"]
        result["oos_ev"] = oos["oos_ev"]

    if mc:
        result["monte_carlo"] = mc

    # Predictability-scored profiles per opening type + heatmap
    if phase_stats:
        result["profiles"] = phase_stats.get("profiles", {})
        result["low_cutoff_recommendation"] = phase_stats.get("low_cutoff_recommendation", "11:30")
        # Overall heatmap summary
        heatmap = phase_stats.get("heatmap", {})
        if heatmap:
            result["best_low_phase"] = heatmap.get("best_low_phase")
            result["best_post_low_high_phase"] = heatmap.get("best_post_low_high_phase")
            result["avg_post_low_trade_window_mins"] = heatmap.get("avg_post_low_trade_window_mins", 0)

    result["dow_stats"] = seasonality.get("dow", {})
    result["month_period_stats"] = seasonality.get("month_period", {})

    status = "ENABLED" if enabled else "disabled"
    cutoff = result.get("low_cutoff_recommendation", "11:30")
    profiles_data = result.get("profiles", {})
    best_lp = result.get("best_low_phase", "?")
    best_plhp = result.get("best_post_low_high_phase", "?")
    tw = result.get("avg_post_low_trade_window_mins", 0)
    if best:
        log.info(
            "%s: %s | edge=%s | EV=%.3f | WR=%.0f%% | n=%d | "
            "drop=%.2fx ATR | high=%.2fx ATR | low@%s -> high@%s (%.0fmin)",
            symbol, status, edge, best['ev'], best['win_rate'], sample_size,
            avg_drop_norm, avg_high_norm, best_lp, best_plhp, tw,
        )
    else:
        log.info("%s: %s | no valid combos", symbol, status)
    for ot_name in ("gap_down_large", "gap_down_small", "flat", "gap_up_small", "gap_up_large"):
        prof = profiles_data.get(ot_name)
        if not prof:
            continue
        low1 = prof.get("low_1", {})
        high1 = prof.get("high_1", {})
        l_win = low1.get("window", "?")
        h_win = high1.get("window", "?")
        drop = prof.get("avg_drop_from_open_pct", 0)
        drop_n = prof.get("avg_drop_norm", 0)
        recov = low1.get("avg_recovery_pct", 0)
        recov_by = low1.get("recovery_by", "?")
        rec_open = prof.get("recovered_past_open_pct", 0)
        pred = prof.get("predictability", 0)
        tw_ot = prof.get("avg_post_low_trade_window_mins", 0)
        log.debug(
            "%s:   %s (n=%d, pred=%.2f): low@%s(%.2fx ATR) -> high@%s | "
            "-%.1f%% drop -> +%.1f%% recov by %s | %.0f%% past open | window %.0fmin",
            symbol, ot_name, prof['n'], pred, l_win, drop_n, h_win,
            drop, recov, recov_by, rec_open, tw_ot,
        )

    return result


# ── Build YAML ───────────────────────────────────────────────────────

def build_yaml(ticker_results, output_path=CONFIG_PATH):
    """Write mlr_config.yaml with per-ticker configs."""
    config = {
        "generated": datetime.now().isoformat(),
        "description": "MLR (Morning Low Recovery) per-ticker config — auto-generated",
        "methodology": (
            "60 days of 5-min data + 1 year daily data analyzed per ticker. Morning lows "
            "identified, full-session phase analysis discovers per-stock low/high formation "
            "windows, EV-optimal entry/stop/target grid-searched, validated with 70/30 "
            "walk-forward OOS, Monte Carlo 95% CIs for robustness."
        ),
        "tickers": {},
    }

    enabled_count = 0
    for symbol, result in sorted(ticker_results.items()):
        if result is not None:
            config["tickers"][symbol] = result
            if result.get("enabled"):
                enabled_count += 1

    config["summary"] = {
        "total_tickers": len(ticker_results),
        "enabled": enabled_count,
        "disabled": len(ticker_results) - enabled_count,
    }

    config = _sanitize(config)

    with open(output_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return output_path


# ── Documentation ────────────────────────────────────────────────────

def generate_documentation(ticker_results, output_path=DOC_PATH):
    """Generate mlr_config_guide.md explaining the config."""
    lines = [
        "# MLR Config Guide",
        "",
        "Auto-generated documentation for Morning Low Recovery configuration.",
        "",
        "## What is MLR?",
        "",
        "Morning Low Recovery buys stocks that form their post-settle daily low",
        "(after 10:00 AM, once opening noise clears) and show confirmed reversal.",
        "The first 45 minutes are ignored — every stock shows extreme moves then.",
        "The real edge is in lows that form after the dust settles.",
        "",
        "## How the Config Works",
        "",
        "For each ticker, the generator:",
        "1. Fetches 60 days of 5-minute OHLCV data (+ 1 year daily)",
        "2. Identifies days where the session low formed in the morning window",
        "3. Runs full-session phase analysis — discovers when each stock forms lows/highs",
        "4. Computes recovery statistics (to close, to high)",
        "5. Grid-searches optimal entry delay, stop, and target combinations",
        "6. Validates with 70/30 walk-forward out-of-sample test",
        "7. Runs Monte Carlo bootstrap for 95% confidence intervals",
        "8. Computes DOW/month seasonality and per-phase window probabilities",
        "9. Recommends per-stock low cutoff based on where 80%+ of lows form",
        "",
        "## Enabled Tickers",
        "",
    ]

    enabled = {s: r for s, r in ticker_results.items() if r and r.get("enabled")}
    disabled = {s: r for s, r in ticker_results.items() if r and not r.get("enabled")}

    if enabled:
        lines.append("| Ticker | Edge | EV | WR% | n | Avg Rec | Drop(xATR) | High(xATR) | Best Low | Best Post-Low High | Window | Cutoff |")
        lines.append("|--------|------|----|-----|---|---------|------------|------------|----------|---------------------|--------|--------|")
        for sym, r in sorted(enabled.items(), key=lambda x: x[1].get("edge_strength", 0), reverse=True):
            cutoff = r.get("low_cutoff_recommendation", "11:30")
            plhp = r.get("best_post_low_high_phase", "—")
            tw = r.get("avg_post_low_trade_window_mins", 0)
            lines.append(
                f"| {sym.replace('.NS', '')} | {r.get('edge_strength', 0)} | "
                f"{r.get('ev', 0):.3f} | {r.get('win_rate', 0):.0f}% | "
                f"{r.get('sample_size', 0)} | {r.get('avg_recovery_to_close_pct', 0):.1f}% | "
                f"{r.get('avg_drop_norm', 0):.2f}x | {r.get('avg_high_norm', 0):.2f}x | "
                f"{r.get('best_low_phase', '—')} | {plhp} | "
                f"{tw:.0f}min | {cutoff} |"
            )

        # Per-stock open-type profiles
        lines.extend(["", "### Open-Type Profiles", ""])
        lines.append("Predictability-scored profiles per opening type (drop -> recovery -> timing):")
        lines.append("")
        ot_order = ("gap_down_large", "gap_down_small", "flat", "gap_up_small", "gap_up_large")
        for sym, r in sorted(enabled.items(), key=lambda x: x[1].get("edge_strength", 0), reverse=True):
            profiles = r.get("profiles", {})
            if not profiles:
                continue
            sym_short = sym.replace('.NS', '')
            lines.append(f"**{sym_short}**:")
            lines.append("")
            lines.append("| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |")
            lines.append("|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|")
            for otype in ot_order:
                prof = profiles.get(otype)
                if not prof:
                    continue
                low1 = prof.get("low_1", {})
                # Prefer post-low high window (tradeable) over absolute high window
                plh1 = prof.get("post_low_high_1", {})
                high1 = prof.get("high_1", {})
                l_window = low1.get("window", "—")
                h_window = plh1.get("window") or (high1.get("window", "—") if high1 else "—")
                drop_n = prof.get("avg_drop_norm", 0)
                high_n = prof.get("avg_high_norm", 0)
                tw = prof.get("avg_post_low_trade_window_mins", 0)
                rec_open = prof.get("recovered_past_open_pct", 0)
                pred = prof.get("predictability", 0)
                label = otype.replace("_", " ").title()
                lines.append(
                    f"| {label} | {prof['n']} | {pred:.2f} | {l_window} | "
                    f"{drop_n:.2f}x | {h_window} | {high_n:.2f}x | "
                    f"{tw:.0f} | {rec_open:.0f}% |"
                )
            lines.append("")
    else:
        lines.append("No tickers enabled.")

    lines.extend([
        "## Disabled Tickers",
        "",
    ])

    if disabled:
        for sym, r in sorted(disabled.items()):
            reason = "insufficient data" if r.get("sample_size", 0) < MIN_SAMPLE else "low EV/WR or OOS degraded"
            lines.append(f"- **{sym.replace('.NS', '')}**: {reason}")
    else:
        lines.append("None disabled.")

    lines.extend([
        "",
        "## Key Parameters",
        "",
        f"- Minimum sample: {MIN_SAMPLE} trading days with morning low",
        f"- Minimum win rate: {MIN_WIN_RATE}%",
        f"- Round-trip cost: {ROUND_TRIP_COST_PCT}%",
        f"- OOS train/test split: {OOS_TRAIN_RATIO*100:.0f}/{(1-OOS_TRAIN_RATIO)*100:.0f}",
        f"- Monte Carlo iterations: {MONTE_CARLO_ITERS:,}",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    return output_path


# ── CLI entry point ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate MLR config (mlr_config.yaml)")
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG-level logging per ticker")
    parser.add_argument("--ticker", "-t", type=str, help="Process single ticker (e.g. RELIANCE.NS)")
    args = parser.parse_args()

    # Configure logging — verbose gets DEBUG, default gets INFO
    level = logging.DEBUG if (args.verbose or args.ticker) else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("=" * 60)
    log.info("MLR Config Generator — Morning Low Recovery")
    log.info("=" * 60)

    if args.ticker:
        tickers = {args.ticker: TICKERS.get(args.ticker, {"name": args.ticker, "sector": ""})}
    else:
        tickers = TICKERS

    log.info("Processing %d tickers in parallel...", len(tickers))
    results = {}

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _process(sym_cfg):
        sym, cfg = sym_cfg
        return sym, process_ticker(sym, cfg)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_process, (sym, cfg)): sym
                   for sym, cfg in tickers.items()}
        done = 0
        for future in as_completed(futures):
            done += 1
            sym = futures[future]
            try:
                _, result = future.result()
                results[sym] = result
            except Exception as e:
                log.warning("%s: %s", sym, e)
                results[sym] = None
            if done % 10 == 0 or done == len(tickers):
                log.info("[%d/%d] processed", done, len(tickers))

    # Build outputs
    config_path = build_yaml(results)
    doc_path = generate_documentation(results)

    # Summary
    enabled = sum(1 for r in results.values() if r and r.get("enabled"))
    total = len(results)
    processed = sum(1 for r in results.values() if r is not None)

    log.info("=" * 60)
    log.info("Results: %d/%d tickers processed", processed, total)
    log.info("  Enabled:  %d", enabled)
    log.info("  Disabled: %d", processed - enabled)
    log.info("  Skipped:  %d", total - processed)
    log.info("Config: %s", config_path)
    log.info("Guide:  %s", doc_path)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
