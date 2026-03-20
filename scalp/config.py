#!/usr/bin/env python3
"""
Deterministic Scalp Config Generator

Fetches OHLCV via fetch_yf(), computes indicators, caches results in
analysis_cache, and generates scalp_config.yaml with optimal parameters.

Pipeline: config.py (fetch → compute → cache → YAML) → scanner.py

Usage: python -m scalp.config
"""

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import binomtest
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

from common.data import (
    PROJECT_ROOT, SCALP_CONFIG_PATH, SCALP_DIR,
    BENCHMARK, fetch_yf, fetch_bulk, fetch_bulk_single, fetch_ticker_info,
    load_universe_for_tier,
)

TICKERS = load_universe_for_tier("scalp")
from common.analysis_cache import get_cached, set_cached, TTL_DAILY
from common.indicators import (
    compute_atr, compute_beta, classify_gaps,
    compute_time_window_stats, compute_probability_matrix,
)
from common.risk import NSE_ROUND_TRIP_COST_PCT

# ── Tunable Constants ──────────────────────────────────────────────
MIN_SAMPLE = 5              # minimum N for a combo to be considered
MIN_TRADABILITY = 55        # minimum overall score to enable
MIN_EDGE_FOR_ENABLE = 3     # minimum edge_strength to enable
EV_THRESHOLD = 0.0          # minimum EV to consider a combo
BAYESIAN_PRIOR = 1          # Beta distribution prior (1 = uniform)
PHASE_WIN_RATE_MIN = 55.0   # minimum win rate for phase selection
PHASE_P_VALUE_MAX = 0.10    # significance level for binomial test
BEARISH_OC_THRESHOLD = -0.2 # avg O→C below this = bearish gap type
BEARISH_UPCLOSE_MIN = 40.0  # up-close rate below this = bearish
ATR_CHASE_FRACTION = 0.67   # max_move_from_open = this × ATR%
ROUND_TRIP_COST_PCT = NSE_ROUND_TRIP_COST_PCT  # unified from common.risk
FDR_ALPHA = 0.10            # Benjamini-Hochberg false discovery rate
OOS_TRAIN_RATIO = 0.70      # walk-forward: train on first 70% of days
OOS_DEGRADATION_PENALTY = 0.5  # edge_strength penalty multiplier if OOS degrades
KELLY_FRACTION = 0.5           # half-Kelly to account for estimation error
MONTE_CARLO_ITERS = 10000     # bootstrap iterations for confidence intervals
DOW_WR_AVOID_THRESHOLD = 40.0 # avoid days with win rate below this
PHASE_TRAP_THRESHOLD = 50.0   # trap rate above this removes gap_type from phase

CONFIG_PATH = SCALP_CONFIG_PATH
DOC_PATH = SCALP_DIR / "scalp_config_guide.md"
GLOSSARY = {
    "EV (Expected Value)": "The average profit/loss per trade if you repeated it many times. Positive EV means the strategy makes money over time.",
    "Hit Rate": "How often the trade reaches target before stop — like a batting average for your setup.",
    "ATR (Average True Range)": "How much the stock typically moves in a day. Higher ATR = more volatile = wider stops needed.",
    "Kelly Fraction": "A formula that sizes your bet based on your edge. Half-Kelly (what we use) is more conservative to account for estimation error.",
    "Edge Strength (1-5)": "Composite score combining EV, win rate, sample size, and trap safety. 5 = strongest statistical edge.",
    "FDR (False Discovery Rate)": "Controls for lucky flukes when testing many combos. Benjamini-Hochberg ensures we're not fooled by random chance.",
    "OOS (Out-of-Sample)": "Walk-forward validation: train on first 70% of data, test on last 30%. Checks if the edge holds on unseen data.",
    "Gap Type": "How the stock opens relative to yesterday's close — gap_up, gap_down, or flat. Different gap types have different trading characteristics.",
    "VWAP": "Volume-Weighted Average Price — the 'fair price' for the day. Reclaiming VWAP after a dip is a bullish signal.",
    "Phase": "Time-of-day window (e.g., MORNING_SCALP 9:30-10:30). Different phases have different volatility and win-rate profiles.",
}

# Phase mapping from time windows to config phase names
WINDOW_TO_PHASE = {
    "09:15-10:00": "MORNING_SCALP",
    "10:00-11:30": "LATE_MORNING",
    "11:30-12:30": "LUNCH_HOUR",
    "12:30-13:30": "EARLY_AFTERNOON",
    "13:30-14:30": "PRE_CLOSE_SETUP",
    "14:30-15:15": "AFTERNOON_SCALP",
}


# ── Binomial test (scipy) ─────────────────────────────────────────

def binomial_p_value(successes, n, p=0.5):
    """One-sided binomial test: P(win_rate > p) using scipy exact test."""
    if n == 0:
        return 1.0
    return binomtest(successes, n, p, alternative="greater").pvalue


# ── Step 1: Compute indicators and cache ─────────────────────────

def compute_and_cache_ticker(symbol, cfg, daily_df, intraday_df, bench_daily, sector_daily, info):
    """Compute gap analysis, probability matrix, time window stats, and metadata.

    Cache all results in analysis_cache. Returns metadata dict.
    """
    last_price = daily_df["Close"].iloc[-1]
    atr = compute_atr(daily_df)
    beta = compute_beta(daily_df, bench_daily)

    # Core computations (same as report.py)
    gap_df = classify_gaps(daily_df)
    tw_stats = compute_time_window_stats(intraday_df)
    prob_matrix = compute_probability_matrix(intraday_df, gap_df)

    # Confidence scores (same as report.py lines 263-282)
    avg_vol_20d = daily_df["Volume"].tail(20).mean()
    atr_pct = atr / last_price * 100 if last_price > 0 and not np.isnan(atr) else 0

    scores = {}
    scores["liquidity"] = min(100, avg_vol_20d / 500_000 * 100) if not np.isnan(avg_vol_20d) else 0

    if 1.0 <= atr_pct <= 3.0:
        scores["volatility"] = 80 + (1 - abs(atr_pct - 2) / 1) * 20
    elif atr_pct > 0:
        scores["volatility"] = max(20, 80 - abs(atr_pct - 2) * 20)
    else:
        scores["volatility"] = 0

    if not tw_stats.empty:
        best_wr = tw_stats["win_rate"].max()
        scores["predictability"] = min(100, best_wr * 1.5)
    else:
        scores["predictability"] = 0

    # Trap stats
    gap_traps = gap_df[
        ((gap_df["gap_type"].isin(["small_up", "large_up"])) & (gap_df["open_to_close_dir"] == "down")) |
        ((gap_df["gap_type"].isin(["small_down", "large_down"])) & (gap_df["open_to_close_dir"] == "up"))
    ]
    gap_non_flat = gap_df[~gap_df["gap_type"].isin(["flat"])]
    trap_pct = len(gap_traps) / len(gap_non_flat) * 100 if len(gap_non_flat) > 0 else 0
    scores["trap_safety"] = max(0, 100 - trap_pct * 2)

    overall = np.mean(list(scores.values()))

    company_name = info.get("longName", cfg["name"])
    sector = info.get("sector", "N/A")
    avg_turnover = avg_vol_20d * last_price

    meta = {
        "name": company_name,
        "sector": sector,
        "atr_abs": float(atr) if not np.isnan(atr) else 0.0,
        "atr_pct": round(float(atr_pct), 2),
        "beta": round(float(beta), 2) if not np.isnan(beta) else 1.0,
        "avg_volume": int(avg_vol_20d) if not np.isnan(avg_vol_20d) else 0,
        "avg_turnover_cr": round(float(avg_turnover / 1e7), 2) if not np.isnan(avg_turnover) else 0.0,
        "score_liquidity": int(scores["liquidity"]),
        "score_volatility": int(scores["volatility"]),
        "score_predictability": int(scores["predictability"]),
        "score_trap_safety": int(scores["trap_safety"]),
        "score_overall": int(overall),
        "trap_count": len(gap_traps),
        "trap_total": len(gap_non_flat),
        "trap_pct": round(float(trap_pct), 1),
    }

    # Cache as JSONB via analysis_cache
    set_cached("scalp_gap_analysis", gap_df.reset_index().to_dict("records"), symbol=symbol)
    set_cached("scalp_prob_matrix", prob_matrix.to_dict("records"), symbol=symbol)
    set_cached("scalp_tw_stats", tw_stats.to_dict("records"), symbol=symbol)
    set_cached("scalp_metadata", meta, symbol=symbol)

    return meta


# ── Step 2: Gap-type stats from gap_analysis.csv ──────────────────

def compute_gap_stats(gap_df: pd.DataFrame) -> dict:
    """Compute per-gap-type statistics."""
    total_days = len(gap_df)
    stats = {}
    for gap_type, grp in gap_df.groupby("gap_type"):
        count = len(grp)
        avg_oc = grp["open_to_close_pct"].mean()
        up_close = (grp["open_to_close_dir"] == "up").sum()
        up_close_rate = (up_close / count * 100) if count > 0 else 0
        is_bearish = avg_oc < BEARISH_OC_THRESHOLD or up_close_rate < BEARISH_UPCLOSE_MIN
        stats[gap_type] = {
            "count": count,
            "pct_of_days": count / total_days * 100 if total_days > 0 else 0,
            "avg_oc_pct": avg_oc,
            "up_close_rate": up_close_rate,
            "is_bearish": is_bearish,
        }
    return stats


# ── FDR correction (Benjamini-Hochberg) ──────────────────────────

def benjamini_hochberg(p_values: list[float], alpha: float = FDR_ALPHA) -> list[bool]:
    """Benjamini-Hochberg FDR correction. Returns list of booleans (significant or not)."""
    n = len(p_values)
    if n == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    significant = [False] * n
    # Find largest k where p_(k) <= k/n * alpha
    max_k = -1
    for rank, (orig_idx, p) in enumerate(indexed, 1):
        threshold = rank / n * alpha
        if p <= threshold:
            max_k = rank
    # All with rank <= max_k are significant
    if max_k > 0:
        for rank, (orig_idx, p) in enumerate(indexed, 1):
            if rank <= max_k:
                significant[orig_idx] = True
    return significant


# ── Step 3: EV-optimized target/stop selection ────────────────────

def compute_ev_combos(prob_df: pd.DataFrame) -> dict:
    """For each gap_type, find the best (target, stop) combo by Bayesian-adjusted EV.

    Includes:
    - Transaction cost modeling (ROUND_TRIP_COST_PCT deducted from EV)
    - FDR correction across all tested combos to control false discoveries
    """
    # First pass: compute all candidate combos across all gap types
    all_candidates = []
    for gap_type, gap_grp in prob_df.groupby("gap_type"):
        for (target, stop), combo_grp in gap_grp.groupby(["target_pct", "stop_pct"]):
            decided = combo_grp[combo_grp["result"].isin(["target", "stop"])]
            n = len(decided)
            if n < MIN_SAMPLE:
                continue

            hits = (decided["result"] == "target").sum()
            misses = n - hits

            # Bayesian adjustment (Beta posterior mean)
            alpha = hits + BAYESIAN_PRIOR
            beta_ = misses + BAYESIAN_PRIOR
            p_adj = alpha / (alpha + beta_)

            # EV with transaction costs: win pays (target - cost), loss costs (stop + cost)
            cost = ROUND_TRIP_COST_PCT
            ev_adj = p_adj * (target - cost) - (1 - p_adj) * (stop + cost)
            raw_win_rate = hits / n * 100

            # Binomial p-value for this combo
            p_val = binomtest(hits, n, 0.5, alternative="greater").pvalue

            all_candidates.append({
                "gap_type": gap_type,
                "target_pct": target,
                "stop_pct": stop,
                "n": n,
                "hits": hits,
                "win_rate": raw_win_rate,
                "p_adjusted": p_adj,
                "ev_adjusted": ev_adj,
                "p_value": p_val,
            })

    # FDR correction across ALL combos (controls false discovery rate)
    if all_candidates:
        p_values = [c["p_value"] for c in all_candidates]
        fdr_significant = benjamini_hochberg(p_values, FDR_ALPHA)
        for i, sig in enumerate(fdr_significant):
            all_candidates[i]["fdr_significant"] = sig
    else:
        fdr_significant = []

    # Second pass: for each gap_type, pick best combo (prefer FDR-significant ones)
    results = {}
    for gap_type in prob_df["gap_type"].unique():
        gap_combos = [c for c in all_candidates if c["gap_type"] == gap_type]
        if not gap_combos:
            results[gap_type] = None
            continue

        # Prefer FDR-significant combos; fall back to non-significant if none pass
        significant_combos = [c for c in gap_combos if c.get("fdr_significant", False) and c["ev_adjusted"] > EV_THRESHOLD]
        pool = significant_combos if significant_combos else [c for c in gap_combos if c["ev_adjusted"] > EV_THRESHOLD]

        if pool:
            # Rank by EV / SE(p) — penalizes small N naturally (t-stat-like)
            for c in pool:
                n = max(c["n"], 1)
                p_adj = c["p_adjusted"]
                se = math.sqrt(p_adj * (1 - p_adj) / n)
                c["selection_score"] = c["ev_adjusted"] / max(se, 0.01)
            best = max(pool, key=lambda c: c["selection_score"])
            results[gap_type] = best
        else:
            results[gap_type] = None

    return results


def validate_oos(prob_df: pd.DataFrame, ev_combos: dict) -> dict:
    """Walk-forward validation: check if in-sample combos hold out-of-sample.

    Splits data by date into train (first 70%) and test (last 30%).
    Returns per-gap OOS stats and overall degradation flag.
    """
    if prob_df.empty or "date" not in prob_df.columns:
        return {"degraded": False, "oos_results": {}}

    dates = sorted(prob_df["date"].unique())
    split_idx = int(len(dates) * OOS_TRAIN_RATIO)
    if split_idx < 5 or len(dates) - split_idx < 5:
        return {"degraded": False, "oos_results": {}}

    test_dates = set(dates[split_idx:])
    test_df = prob_df[prob_df["date"].isin(test_dates)]

    oos_results = {}
    degradation_count = 0
    total_tested = 0

    for gap_type, combo in ev_combos.items():
        if combo is None:
            continue

        target = combo["target_pct"]
        stop = combo["stop_pct"]
        oos_sub = test_df[
            (test_df["gap_type"] == gap_type)
            & (test_df["target_pct"] == target)
            & (test_df["stop_pct"] == stop)
            & (test_df["result"].isin(["target", "stop"]))
        ]

        n_oos = len(oos_sub)
        if n_oos < MIN_SAMPLE:
            oos_results[gap_type] = {"n": n_oos, "win_rate": None, "ev": None}
            continue

        hits = (oos_sub["result"] == "target").sum()
        wr_oos = hits / n_oos * 100
        cost = ROUND_TRIP_COST_PCT
        p_oos = hits / n_oos
        ev_oos = p_oos * (target - cost) - (1 - p_oos) * (stop + cost)

        oos_results[gap_type] = {"n": n_oos, "win_rate": wr_oos, "ev": ev_oos}

        total_tested += 1
        # Significant degradation: OOS win rate drops >15pp or EV goes negative
        if wr_oos < combo["win_rate"] - 15 or ev_oos < 0:
            degradation_count += 1

    degraded = total_tested > 0 and degradation_count / total_tested > 0.5
    return {"degraded": degraded, "oos_results": oos_results}


# ── Feature 6: MAE Analysis ──────────────────────────────────────

def compute_mae_analysis(prob_df: pd.DataFrame) -> dict:
    """Analyze Maximum Adverse Excursion for winning trades per gap_type.

    For winners: median MAE, p90 MAE, % winners that dipped beyond current stop,
    optimal stop suggestion (p90 MAE + buffer).
    """
    decided = prob_df[prob_df["result"].isin(["target", "stop"])]
    results = {}

    for gap_type in decided["gap_type"].unique():
        gap_sub = decided[decided["gap_type"] == gap_type]

        for (target, stop), combo_grp in gap_sub.groupby(["target_pct", "stop_pct"]):
            winners = combo_grp[combo_grp["result"] == "target"]
            if len(winners) < 3:
                continue

            mae_values = winners["mae_pct"].dropna().abs()
            if mae_values.empty:
                continue

            median_mae = float(mae_values.median())
            p90_mae = float(mae_values.quantile(0.90))
            # % of winners that dipped beyond current stop level
            pct_beyond_stop = float((mae_values > stop).mean() * 100)
            # Optimal stop: p90 MAE + 10% buffer
            optimal_stop = round(p90_mae * 1.1, 2)

            key = f"{gap_type}_{target}_{stop}"
            results[key] = {
                "gap_type": gap_type,
                "target_pct": float(target),
                "stop_pct": float(stop),
                "n_winners": len(winners),
                "median_mae_pct": round(median_mae, 3),
                "p90_mae_pct": round(p90_mae, 3),
                "pct_winners_beyond_stop": round(pct_beyond_stop, 1),
                "optimal_stop_pct": optimal_stop,
            }

    return results


# ── Feature 4: Monte Carlo CI ─────────────────────────────────────

def monte_carlo_ci(prob_df: pd.DataFrame, ev_combos: dict,
                   n_iter: int = MONTE_CARLO_ITERS) -> dict:
    """Bootstrap resample to compute 95% CI on EV, WR, and max DD p95.

    Flags fragile=True if EV lower bound < 0.
    """
    results = {}

    for gap_type, combo in ev_combos.items():
        if combo is None:
            continue

        target = combo["target_pct"]
        stop = combo["stop_pct"]
        sub = prob_df[
            (prob_df["gap_type"] == gap_type)
            & (prob_df["target_pct"] == target)
            & (prob_df["stop_pct"] == stop)
            & (prob_df["result"].isin(["target", "stop"]))
        ]

        if len(sub) < MIN_SAMPLE:
            continue

        outcomes = (sub["result"] == "target").astype(int).values
        n = len(outcomes)

        # Bootstrap
        rng = np.random.default_rng(42)
        boot_evs = []
        boot_wrs = []
        boot_dds = []

        cost = ROUND_TRIP_COST_PCT
        for _ in range(n_iter):
            sample = rng.choice(outcomes, size=n, replace=True)
            wr = sample.mean()
            ev = wr * (target - cost) - (1 - wr) * (stop + cost)
            boot_evs.append(ev)
            boot_wrs.append(wr * 100)

            # Simulate cumulative P&L for max DD
            pnls = np.where(sample == 1, target - cost, -(stop + cost))
            cum = np.cumsum(pnls)
            peak = np.maximum.accumulate(cum)
            dd = (peak - cum).max()
            boot_dds.append(dd)

        ev_lo, ev_hi = float(np.percentile(boot_evs, 2.5)), float(np.percentile(boot_evs, 97.5))
        wr_lo, wr_hi = float(np.percentile(boot_wrs, 2.5)), float(np.percentile(boot_wrs, 97.5))
        dd_p95 = float(np.percentile(boot_dds, 95))
        fragile = ev_lo < 0

        results[gap_type] = {
            "ev_ci_lower": round(ev_lo, 4),
            "ev_ci_upper": round(ev_hi, 4),
            "wr_ci_lower": round(wr_lo, 1),
            "wr_ci_upper": round(wr_hi, 1),
            "max_dd_p95": round(dd_p95, 3),
            "fragile": fragile,
            "n": n,
        }

    return results


# ── Feature 7: Day-of-Week Seasonality ────────────────────────────

def compute_dow_stats(gap_df: pd.DataFrame) -> dict:
    """Compute per-day-of-week win rate, avg O→C, and trap rate.

    Returns dict with per-DOW stats and list of avoid_days.
    """
    if "day_of_week" not in gap_df.columns:
        return {"stats": {}, "avoid_days": []}

    dow_names = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"}
    stats = {}
    avoid_days = []

    for dow in range(5):
        subset = gap_df[gap_df["day_of_week"] == dow]
        if subset.empty:
            continue

        n = len(subset)
        up_close = (subset["open_to_close_dir"] == "up").sum()
        wr = up_close / n * 100 if n > 0 else 0
        avg_oc = subset["open_to_close_pct"].mean()

        # Trap rate: gaps that reversed
        non_flat = subset[~subset["gap_type"].isin(["flat"])]
        if len(non_flat) > 0:
            traps = non_flat[
                ((non_flat["gap_type"].isin(["small_up", "large_up"])) & (non_flat["open_to_close_dir"] == "down")) |
                ((non_flat["gap_type"].isin(["small_down", "large_down"])) & (non_flat["open_to_close_dir"] == "up"))
            ]
            trap_rate = len(traps) / len(non_flat) * 100
        else:
            trap_rate = 0

        stats[dow_names[dow]] = {
            "day_of_week": dow,
            "n": n,
            "win_rate": round(wr, 1),
            "avg_oc_pct": round(avg_oc, 3),
            "trap_rate": round(trap_rate, 1),
        }

        if wr < DOW_WR_AVOID_THRESHOLD:
            avoid_days.append(dow)

    return {"stats": stats, "avoid_days": avoid_days}


# ── Feature 8: Gap-Trap by Phase ──────────────────────────────────

def compute_phase_trap_rates(gap_df: pd.DataFrame, prob_df: pd.DataFrame) -> dict:
    """Cross-tab trap rates by (gap_type x time_window).

    Returns dict of {phase: [gap_types_to_remove]} where trap_rate > threshold.
    """
    if prob_df.empty or "date" not in prob_df.columns:
        return {}

    # Build per-date gap type lookup
    gap_lookup = {}
    for idx, row in gap_df.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        gap_lookup[d] = row["gap_type"]

    # We use the probability matrix outcomes as proxy for trap detection
    # A "stop" hit = trade went against us (trap-like behavior)
    decided = prob_df[prob_df["result"].isin(["target", "stop"])]

    phase_removals = {}

    for gap_type in decided["gap_type"].unique():
        gap_sub = decided[decided["gap_type"] == gap_type]
        if len(gap_sub) < MIN_SAMPLE:
            continue

        # Group by best combo for this gap type
        for (target, stop), combo_grp in gap_sub.groupby(["target_pct", "stop_pct"]):
            if len(combo_grp) < MIN_SAMPLE:
                continue

            stops = (combo_grp["result"] == "stop").sum()
            trap_rate = stops / len(combo_grp) * 100

            if trap_rate > PHASE_TRAP_THRESHOLD:
                # Map to phases — since we don't have per-bar phase info in prob_df,
                # we flag this gap_type as risky. The build_gap_rules function
                # will check this list.
                for phase in WINDOW_TO_PHASE.values():
                    if phase not in phase_removals:
                        phase_removals[phase] = set()
                    phase_removals[phase].add(gap_type)

    # Convert sets to lists
    return {phase: list(gaps) for phase, gaps in phase_removals.items()}


# ── Step 4: Phase selection from time_window_stats.csv ────────────

def select_phases(tw_df: pd.DataFrame) -> list[dict]:
    """Select active phases based on win rate and binomial significance."""
    active = []
    for _, row in tw_df.iterrows():
        window = row["window"]
        phase = WINDOW_TO_PHASE.get(window)
        if not phase:
            continue

        win_rate = row["win_rate"]
        n = int(row["day_count"])
        wins = round(win_rate / 100 * n)
        p_val = binomial_p_value(wins, n, 0.5)

        is_significant = p_val < PHASE_P_VALUE_MAX
        is_practical = win_rate > PHASE_WIN_RATE_MIN

        if is_significant or is_practical:
            active.append({
                "phase": phase,
                "window": window,
                "win_rate": win_rate,
                "p_value": p_val,
                "n": n,
            })

    # Deduplicate phases (keep best per phase name)
    best_by_phase = {}
    for entry in active:
        phase = entry["phase"]
        if phase not in best_by_phase or entry["win_rate"] > best_by_phase[phase]["win_rate"]:
            best_by_phase[phase] = entry

    return list(best_by_phase.values())


# ── Step 5: Gap rules per phase ───────────────────────────────────

def build_gap_rules(active_phases: list[dict], ev_combos: dict, gap_stats: dict,
                    phase_trap_removals: dict = None) -> dict:
    """For each active phase, select allowed gap types.

    phase_trap_removals: dict of {phase: [gap_types]} to exclude due to high trap rates.
    """
    if phase_trap_removals is None:
        phase_trap_removals = {}
    rules = {}
    for phase_info in active_phases:
        phase = phase_info["phase"]
        removals = set(phase_trap_removals.get(phase, []))
        preferred = []
        for gap_type, combo in ev_combos.items():
            if combo is None:
                continue
            if combo["ev_adjusted"] <= 0:
                continue
            gs = gap_stats.get(gap_type, {})
            if gs.get("is_bearish", True):
                continue
            if gs.get("up_close_rate", 0) < 35:
                continue
            if gap_type in removals:
                continue
            preferred.append(gap_type)

        # Sort by EV descending
        preferred.sort(key=lambda g: ev_combos[g]["ev_adjusted"] if ev_combos.get(g) else 0, reverse=True)

        if preferred:
            rules[phase] = {"preferred_gaps": preferred}

    return rules


# ── Step 6: Edge strength scoring ─────────────────────────────────

def normalize(x, lo, hi):
    """Normalize x to [0, 1] range, clipped."""
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def extract_feature_vector(ev_combos: dict, meta: dict, active_phases: list[dict],
                           gap_stats: dict) -> dict:
    """Extract raw feature vector for a ticker (used by both fixed and PCA scoring)."""
    best_ev = max((c["ev_adjusted"] for c in ev_combos.values() if c), default=0)
    best_phase_wr = max((p["win_rate"] for p in active_phases), default=50)
    best_n = max((c["n"] for c in ev_combos.values() if c), default=0)
    trap_safety = 100 - meta.get("trap_pct", 50)
    tradable_pct = sum(
        gap_stats[g]["pct_of_days"]
        for g in ev_combos
        if ev_combos[g] is not None
        and ev_combos[g]["ev_adjusted"] > 0
        and not gap_stats.get(g, {}).get("is_bearish", True)
    )
    return {
        "best_ev": best_ev,
        "tradability": meta.get("score_overall", 50),
        "best_phase_wr": best_phase_wr,
        "best_n": best_n,
        "trap_safety": trap_safety,
        "tradable_pct": tradable_pct,
    }


def compute_edge_strength(ev_combos: dict, meta: dict, active_phases: list[dict],
                          gap_stats: dict) -> int:
    """Compute composite edge strength score (1-5) using normalized features.

    Uses fixed weights as fallback. PCA-derived weights are applied in
    compute_pca_edge_strengths() when multiple tickers are available.
    """
    feats = extract_feature_vector(ev_combos, meta, active_phases, gap_stats)

    score = (
        0.30 * normalize(feats["best_ev"], 0, 0.8)
        + 0.20 * normalize(feats["tradability"], 40, 80)
        + 0.15 * normalize(feats["best_phase_wr"], 50, 75)
        + 0.15 * normalize(feats["best_n"], 5, 30)
        + 0.10 * normalize(feats["trap_safety"], 30, 70)
        + 0.10 * normalize(feats["tradable_pct"], 10, 60)
    )

    return max(1, min(5, round(score * 4) + 1))


def compute_pca_edge_strengths(configs: list[dict]) -> list[dict]:
    """Re-compute edge strengths using PCA-derived weights across all tickers.

    PCA on the feature matrix reveals which dimensions explain the most variance.
    PC1 loadings become the weights — data-driven rather than arbitrary.
    Falls back to original scores if PCA fails (too few tickers).
    """
    if len(configs) < 5:
        return configs

    # Build feature matrix
    feature_names = ["best_ev", "tradability", "best_phase_wr", "best_n", "trap_safety", "tradable_pct"]
    rows = []
    for cfg in configs:
        feats = extract_feature_vector(
            cfg["_ev_combos"], cfg["_meta"], cfg["_active_phases"], cfg["_gap_stats"]
        )
        rows.append([feats[f] for f in feature_names])

    X = np.array(rows)
    if np.any(np.isnan(X)):
        return configs

    # Standardize and run PCA
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    pca = PCA(n_components=1)
    scores_1d = pca.fit_transform(X_scaled).flatten()

    # Orient PC1 so positive = better edge (best_ev has positive correlation)
    # If PC1 loading for best_ev is negative, flip all scores
    ev_idx = feature_names.index("best_ev")
    if pca.components_[0][ev_idx] < 0:
        scores_1d = -scores_1d

    # Use PC1 scores directly — they capture signed, variance-weighted combinations
    # Rank-normalize to [0, 1] across tickers for stable 1-5 mapping
    score_min, score_max = scores_1d.min(), scores_1d.max()
    if score_max > score_min:
        normed_scores = (scores_1d - score_min) / (score_max - score_min)
    else:
        normed_scores = np.full_like(scores_1d, 0.5)

    for i, cfg in enumerate(configs):
        pca_score = float(normed_scores[i])
        new_edge = max(1, min(5, round(pca_score * 4) + 1))

        # Apply OOS penalty
        if cfg.get("_oos", {}).get("degraded", False):
            new_edge = max(1, new_edge - 1)

        # Apply Monte Carlo fragility penalty — CI crossing zero = unreliable edge
        mc = cfg.get("_mc_results", {})
        if mc and any(v.get("fragile", False) for v in mc.values()):
            new_edge = max(1, new_edge - 1)

        cfg["edge_strength"] = new_edge
        # Re-evaluate enabled
        cfg["enabled"] = should_enable(
            new_edge, cfg["_meta"], cfg["_active_phases"],
            cfg["_ev_combos"], cfg["_gap_stats"]
        )

    return configs


# ── Step 7: Enabled decision ─────────────────────────────────────

def should_enable(edge_strength: int, meta: dict, active_phases: list[dict],
                  ev_combos: dict, gap_stats: dict) -> bool:
    """Determine if ticker should be enabled."""
    if edge_strength < MIN_EDGE_FOR_ENABLE:
        return False
    if meta.get("score_overall", 0) < MIN_TRADABILITY:
        return False
    # At least one active phase with win_rate > 50
    if not any(p["win_rate"] > 50 for p in active_phases):
        return False
    # At least one gap type with positive EV and sufficient N
    has_edge = any(
        c is not None and c["ev_adjusted"] > 0 and c["n"] >= MIN_SAMPLE
        and not gap_stats.get(g, {}).get("is_bearish", True)
        for g, c in ev_combos.items()
    )
    return has_edge


# ── Step 8: Derive remaining config fields ────────────────────────

def derive_config(symbol: str, meta: dict, ev_combos: dict, gap_stats: dict,
                  active_phases: list[dict], gap_rules: dict,
                  edge_strength: int, enabled: bool,
                  mae_analysis: dict = None, mc_results: dict = None,
                  dow_stats: dict = None) -> dict:
    """Build the full ticker config dict."""
    atr_pct = meta.get("atr_pct", 2.0)
    beta = meta.get("beta", 1.0)

    # Best combo overall (for target/stop defaults)
    best_combo = None
    best_ev = -999
    best_gap = None
    for g, c in ev_combos.items():
        if c is not None and c["ev_adjusted"] > best_ev:
            if not gap_stats.get(g, {}).get("is_bearish", True):
                best_ev = c["ev_adjusted"]
                best_combo = c
                best_gap = g

    # Fallback: pick any combo with highest EV even if bearish
    if best_combo is None:
        for g, c in ev_combos.items():
            if c is not None and c["ev_adjusted"] > best_ev:
                best_ev = c["ev_adjusted"]
                best_combo = c
                best_gap = g

    # Defaults if no combo found (convert numpy to native Python float)
    target = float(best_combo["target_pct"]) if best_combo else 0.5
    stop = float(best_combo["stop_pct"]) if best_combo else 1.5

    # Aggressiveness via half-Kelly fraction (risk-bounded)
    kelly_raw = 0.0
    if best_combo and best_combo["ev_adjusted"] > 0:
        p = best_combo["p_adjusted"]
        b = target / stop if stop > 0 else 1
        kelly_raw = (p * b - (1 - p)) / b if b > 0 else 0
    kelly = kelly_raw * KELLY_FRACTION  # half-Kelly for estimation error safety
    if kelly <= 0:
        aggressiveness = "none"
    elif kelly < 0.08:
        aggressiveness = "low"
    elif kelly < 0.15:
        aggressiveness = "medium"
    else:
        aggressiveness = "high"

    # max_move_from_open_pct
    max_move = round(ATR_CHASE_FRACTION * atr_pct, 1)

    # min_range_multiple_of_atr
    if atr_pct < 2.0:
        min_range_mult = 0.3
    elif atr_pct < 3.0:
        min_range_mult = 0.4
    else:
        min_range_mult = 0.5

    # min_volume_ratio
    liq_score = meta.get("score_liquidity", 100)
    min_vol_ratio = 0.6 if liq_score < 80 else 0.5

    # Regime tags
    regime_tags = []
    if meta.get("sector"):
        sector_lower = meta["sector"].lower()
        if "industrial" in sector_lower or "defence" in sector_lower:
            regime_tags.append("industrials")
        if "financ" in sector_lower:
            regime_tags.append("financials")
        if "energy" in sector_lower or "power" in sector_lower:
            regime_tags.append("energy")
    if beta > 1.5:
        regime_tags.append("high_beta")
    elif beta < 0.8:
        regime_tags.append("low_beta")
    if atr_pct < 2.0:
        regime_tags.append("low_vol")
    elif atr_pct > 3.5:
        regime_tags.append("high_vol")

    # Active/avoid phases
    phase_names = sorted(set(p["phase"] for p in active_phases))
    if not phase_names:
        phase_names = ["LATE_MORNING"]

    # Notes
    notes_parts = [f"Auto-generated from 126d backtest."]
    if best_combo and best_gap:
        notes_parts.append(
            f"Best combo: {best_gap} +{target}%/-{stop}% "
            f"EV={best_combo['ev_adjusted']:.2f} "
            f"({best_combo['win_rate']:.0f}% hit, N={best_combo['n']})."
        )
    # Trap warnings
    for g, gs in gap_stats.items():
        if gs.get("up_close_rate", 100) < 35:
            notes_parts.append(f"TRAP: {g} ({gs['up_close_rate']:.0f}% up-close).")
    # Best phase
    if active_phases:
        bp = max(active_phases, key=lambda p: p["win_rate"])
        notes_parts.append(f"Best window: {bp['window']} ({bp['win_rate']:.0f}% WR).")

    config = {
        "symbol": symbol,
        "name": meta.get("name", ""),
        "enabled": enabled,
        "direction": "long",
        "edge_strength": edge_strength,
        "regime_tags": regime_tags if regime_tags else ["general"],
        "active_phases": phase_names,
        "avoid_phases": ["AVOID_ZONE"],
        "gap_rules": gap_rules if gap_rules else {"LATE_MORNING": {"preferred_gaps": ["flat"]}},
        "entry_conditions": {
            "require_vwap_reclaim": True,
            "require_higher_low": True,
            "require_nifty_ok": True,
            "min_volume_ratio": min_vol_ratio,
            "min_range_multiple_of_atr": min_range_mult,
            "max_move_from_open_pct": max_move,
        },
        "risk": {
            "base_target_pct": target,
            "base_stop_pct": stop,
            "atr_target_multiple": round(target / atr_pct, 2) if atr_pct > 0 else 0.5,
            "atr_stop_multiple": round(stop / atr_pct, 2) if atr_pct > 0 else 0.7,
            "kelly_fraction": round(kelly, 3),
            "aggressiveness": aggressiveness,
            "max_trades_per_day": 1,
            "max_hold_minutes": 45,
        },
        "notes": " ".join(notes_parts),
    }

    # Per-gap-type optimal combos (used by backtest for gap-specific target/stop)
    gap_combos = {}
    for g, c in ev_combos.items():
        if c is not None and c["ev_adjusted"] > 0:
            gap_combos[g] = {
                "target_pct": float(c["target_pct"]),
                "stop_pct": float(c["stop_pct"]),
                "ev": round(float(c["ev_adjusted"]), 3),
                "win_rate": round(float(c["win_rate"]), 1),
                "n": int(c["n"]),
            }
    if gap_combos:
        config["gap_combos"] = gap_combos

    # Feature 6: MAE analysis
    if mae_analysis:
        # Attach best MAE for this ticker's best combo
        relevant = {k: v for k, v in mae_analysis.items()
                    if best_gap and k.startswith(best_gap)}
        if relevant:
            best_mae_key = next(iter(relevant))
            config["risk"]["mae_analysis"] = relevant[best_mae_key]

    # Feature 4: Monte Carlo CI
    if mc_results:
        mc_data = {}
        any_fragile = False
        for gt, mc in mc_results.items():
            mc_data[gt] = mc
            if mc.get("fragile"):
                any_fragile = True
        if mc_data:
            config["risk"]["monte_carlo"] = mc_data
            if any_fragile:
                config["risk"]["monte_carlo_warning"] = "Some combos have fragile edge (EV CI crosses 0)"

    # Feature 7: DOW avoid days
    if dow_stats and dow_stats.get("avoid_days"):
        config["avoid_days"] = dow_stats["avoid_days"]

    return config


# ── Main pipeline ─────────────────────────────────────────────────

def process_ticker(symbol: str) -> dict | None:
    """Process a single ticker from analysis_cache and return its config."""
    meta = get_cached("scalp_metadata", symbol=symbol, max_age_seconds=TTL_DAILY)
    gap_records = get_cached("scalp_gap_analysis", symbol=symbol, max_age_seconds=TTL_DAILY)
    prob_records = get_cached("scalp_prob_matrix", symbol=symbol, max_age_seconds=TTL_DAILY)
    tw_records = get_cached("scalp_tw_stats", symbol=symbol, max_age_seconds=TTL_DAILY)

    if not all([meta, gap_records, prob_records, tw_records]):
        return None

    gap_df = pd.DataFrame(gap_records)
    prob_df = pd.DataFrame(prob_records)
    tw_df = pd.DataFrame(tw_records)

    # Step 2: Gap stats
    gap_stats = compute_gap_stats(gap_df)

    # Step 3: EV optimization (with FDR correction + transaction costs)
    ev_combos = compute_ev_combos(prob_df)

    # Step 3b: Walk-forward out-of-sample validation
    oos = validate_oos(prob_df, ev_combos)

    # Feature 6: MAE analysis
    mae_analysis = compute_mae_analysis(prob_df)

    # Feature 4: Monte Carlo CI
    mc_results = monte_carlo_ci(prob_df, ev_combos)

    # Apply MC fragile penalty to edge strength
    mc_fragile = any(mc.get("fragile", False) for mc in mc_results.values())

    # Feature 7: DOW stats
    dow_stats = compute_dow_stats(gap_df)

    # Feature 8: Phase-trap rates
    phase_trap_removals = compute_phase_trap_rates(gap_df, prob_df)

    # Step 4: Phase selection
    active_phases = select_phases(tw_df)

    # Step 5: Gap rules (with phase-trap filtering)
    gap_rules = build_gap_rules(active_phases, ev_combos, gap_stats, phase_trap_removals)

    # Step 6: Edge strength (penalize if OOS degraded or MC fragile)
    edge_strength = compute_edge_strength(ev_combos, meta, active_phases, gap_stats)
    if oos["degraded"]:
        edge_strength = max(1, edge_strength - 1)
    if mc_fragile:
        edge_strength = max(1, edge_strength - 1)

    # Step 7: Enabled decision
    enabled = should_enable(edge_strength, meta, active_phases, ev_combos, gap_stats)

    # Step 8: Full config
    config = derive_config(symbol, meta, ev_combos, gap_stats,
                           active_phases, gap_rules, edge_strength, enabled,
                           mae_analysis=mae_analysis, mc_results=mc_results,
                           dow_stats=dow_stats)

    # Attach internal data for summary
    config["_meta"] = meta
    config["_ev_combos"] = ev_combos
    config["_active_phases"] = active_phases
    config["_gap_stats"] = gap_stats
    config["_oos"] = oos
    config["_mae_analysis"] = mae_analysis
    config["_mc_results"] = mc_results
    config["_dow_stats"] = dow_stats

    return config


def _numpy_representer(dumper, data):
    """Represent numpy types as native Python for YAML serialization."""
    if isinstance(data, (np.integer,)):
        return dumper.represent_int(int(data))
    if isinstance(data, (np.floating,)):
        return dumper.represent_float(float(data))
    if isinstance(data, np.ndarray):
        return dumper.represent_list(data.tolist())
    return dumper.represent_data(data)

yaml.add_multi_representer(np.integer, _numpy_representer)
yaml.add_multi_representer(np.floating, _numpy_representer)
yaml.add_multi_representer(np.ndarray, _numpy_representer)


def load_existing_config() -> dict:
    """Load existing scalp_config.yaml to preserve global/ranking/positions.

    Falls back gracefully if the YAML contains unserializable types.
    """
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return yaml.safe_load(f) or {}
        except yaml.YAMLError:
            # Config has numpy types or is corrupt — load with full loader
            try:
                with open(CONFIG_PATH) as f:
                    return yaml.full_load(f) or {}
            except Exception:
                return {}
    return {}


def build_yaml(configs: list[dict], existing: dict) -> str:
    """Build the final YAML string."""
    lines = []

    lines.append("# " + "═" * 62)
    lines.append("# Scalp Scanner Configuration")
    lines.append(f"# Auto-generated by generate_scalp_config.py")
    lines.append(f"# {len(configs)} tickers analyzed from output/ data (126 trading days)")
    lines.append("# " + "═" * 62)
    lines.append("")

    # Global section — preserve from existing, or create defaults
    global_cfg = existing.get("global", {})
    if not global_cfg:
        global_cfg = {
            "benchmark": "^NSEI",
            "capital": 1000000,
            "phases": {
                "PRE_MARKET":      {"start": "08:00", "end": "09:15"},
                "AVOID_ZONE":      {"start": "09:15", "end": "09:30"},
                "MORNING_SCALP":   {"start": "09:30", "end": "10:30"},
                "LATE_MORNING":    {"start": "10:30", "end": "11:30"},
                "LUNCH_HOUR":      {"start": "11:30", "end": "12:30"},
                "EARLY_AFTERNOON": {"start": "12:30", "end": "13:30"},
                "PRE_CLOSE_SETUP": {"start": "13:30", "end": "14:30"},
                "AFTERNOON_SCALP": {"start": "14:30", "end": "15:15"},
                "CLOSING":         {"start": "15:15", "end": "15:30"},
            },
            "data_periods": {
                "intraday": "5d",
                "daily": "6mo",
            },
        }
    # Ensure capital always exists
    if "capital" not in global_cfg:
        global_cfg["capital"] = 1000000
    # Ensure phases always exist even if global was preserved without them
    if "phases" not in global_cfg:
        global_cfg["phases"] = {
            "PRE_MARKET":      {"start": "08:00", "end": "09:15"},
            "AVOID_ZONE":      {"start": "09:15", "end": "09:30"},
            "MORNING_SCALP":   {"start": "09:30", "end": "10:30"},
            "MIDDAY":          {"start": "10:30", "end": "14:30"},
            "AFTERNOON_SCALP": {"start": "14:30", "end": "15:15"},
            "CLOSING":         {"start": "15:15", "end": "15:30"},
        }
    lines.append("# ── Global Settings " + "─" * 42)
    lines.append(yaml.dump({"global": global_cfg}, default_flow_style=False,
                           sort_keys=False, allow_unicode=True).rstrip())
    lines.append("")

    # Ranking section — preserve from existing
    ranking_cfg = existing.get("ranking", {})
    if ranking_cfg:
        lines.append("# ── Ranking & Selection " + "─" * 38)
        lines.append(yaml.dump({"ranking": ranking_cfg}, default_flow_style=False,
                               sort_keys=False, allow_unicode=True).rstrip())
    lines.append("")

    # Tickers section
    lines.append("# ── Tickers " + "─" * 49)
    lines.append("tickers:")

    for cfg in configs:
        # Strip internal data
        clean = {k: v for k, v in cfg.items() if not k.startswith("_")}

        # Build comment header
        meta = cfg.get("_meta", {})
        ev_combos = cfg.get("_ev_combos", {})
        active_phases = cfg.get("_active_phases", [])

        lines.append("")
        lines.append(f"  # ── {clean['symbol'].replace('.NS', '')} " + "─" * max(1, 55 - len(clean['symbol'])))
        lines.append(f"  # Tradability: {meta.get('score_overall', '?')}/100 | ATR: {meta.get('atr_pct', '?')}% | Beta: {meta.get('beta', '?')}")

        # Best combo info
        best_c = None
        best_g = None
        for g, c in ev_combos.items():
            if c and (best_c is None or c["ev_adjusted"] > best_c["ev_adjusted"]):
                best_c = c
                best_g = g
        if best_c:
            lines.append(f"  # Best: {best_g} +{best_c['target_pct']}%/-{best_c['stop_pct']}% "
                         f"EV={best_c['ev_adjusted']:.2f} ({best_c['win_rate']:.0f}% hit, N={best_c['n']})")
        if active_phases:
            bp = max(active_phases, key=lambda p: p["win_rate"])
            lines.append(f"  # Best window: {bp['window']} ({bp['win_rate']:.0f}% WR)")

        # Serialize the ticker config
        # Convert gap_rules to use flow style for preferred_gaps lists
        ticker_yaml = yaml.dump([clean], default_flow_style=False,
                                sort_keys=False, allow_unicode=True)
        # Indent properly under tickers:
        for tl in ticker_yaml.split("\n"):
            if tl.strip():
                lines.append("  " + tl)

    lines.append("")

    # Positions section — preserve from existing
    lines.append("# ── Open Positions " + "─" * 43)
    positions = existing.get("positions")
    if positions:
        lines.append(yaml.dump({"positions": positions}, default_flow_style=False,
                               sort_keys=False, allow_unicode=True).rstrip())
    else:
        lines.append("positions:")
        lines.append("  # No open positions")

    lines.append("")
    return "\n".join(lines)


def print_summary(configs: list[dict]):
    """Print a terminal summary table."""
    total = len(configs)
    enabled_count = sum(1 for c in configs if c["enabled"])

    print()
    print(f"┌{'─' * 78}┐")
    print(f"│ SCALP CONFIG GENERATOR — {total} tickers analyzed, {enabled_count} enabled{' ' * (78 - 55 - len(str(total)) - len(str(enabled_count)))}│")
    print(f"├{'─' * 14}┬{'─' * 3}┬{'─' * 4}┬{'─' * 9}┬{'─' * 22}┬{'─' * 20}┤")
    print(f"│ {'Ticker':<12} │ E │ On │ {'Score':>7} │ {'Best Combo':<20} │ {'Best Phase':<18} │")
    print(f"├{'─' * 14}┼{'─' * 3}┼{'─' * 4}┼{'─' * 9}┼{'─' * 22}┼{'─' * 20}┤")

    for cfg in configs:
        sym = cfg["symbol"].replace(".NS", "")
        if len(sym) > 12:
            sym = sym[:12]
        edge = cfg["edge_strength"]
        on = "✓" if cfg["enabled"] else "✗"
        score = cfg.get("_meta", {}).get("score_overall", "?")

        ev_combos = cfg.get("_ev_combos", {})
        best_c = None
        best_g = None
        for g, c in ev_combos.items():
            if c and (best_c is None or c["ev_adjusted"] > best_c["ev_adjusted"]):
                best_c = c
                best_g = g

        if best_c:
            combo_str = f"+{best_c['target_pct']}/-{best_c['stop_pct']} EV={best_c['ev_adjusted']:.2f}"
        else:
            combo_str = "N/A"
        combo_str = combo_str[:20]

        active_phases = cfg.get("_active_phases", [])
        if active_phases:
            bp = max(active_phases, key=lambda p: p["win_rate"])
            phase_str = f"{bp['phase'][:10]} {bp['win_rate']:.0f}%"
        else:
            phase_str = "N/A"
        phase_str = phase_str[:18]

        print(f"│ {sym:<12} │ {edge} │ {on:<2} │ {score:>3}/100 │ {combo_str:<20} │ {phase_str:<18} │")

    print(f"└{'─' * 14}┴{'─' * 3}┴{'─' * 4}┴{'─' * 9}┴{'─' * 22}┴{'─' * 20}┘")
    print()


# ── Documentation Generation ──────────────────────────────────────

def _call_llm(config, messages):
    """Call LLM via common.llm. Returns string or None."""
    from common.llm import call_llm
    return call_llm(messages)


def _generate_template_explanation(cfg: dict) -> str:
    """Template-based fallback explanation for a single ticker."""
    symbol = cfg["symbol"].replace(".NS", "")
    meta = cfg["_meta"]
    ev_combos = cfg["_ev_combos"]
    active_phases = cfg["_active_phases"]
    gap_stats = cfg["_gap_stats"]
    oos = cfg.get("_oos", {})
    risk = cfg.get("risk", {})

    paras = []

    # ── Para 1: Overall profile ──
    score = meta.get("score_overall", 0)
    edge = cfg["edge_strength"]
    atr = meta.get("atr_pct", 0)
    beta = meta.get("beta", 1.0)
    status = "**enabled**" if cfg["enabled"] else "**disabled**"

    vol_desc = "low-volatility" if atr < 2.0 else ("high-volatility" if atr > 3.5 else "moderate-volatility")
    beta_desc = (
        f"moves roughly in line with Nifty (beta {beta:.2f})" if 0.8 <= beta <= 1.2
        else f"amplifies Nifty moves by ~{beta:.1f}x (high beta — bigger swings both ways)"
        if beta > 1.2 else f"is relatively defensive with beta {beta:.2f} (moves less than Nifty)"
    )

    paras.append(
        f"{symbol} scores **{score}/100** on tradability (a composite of liquidity, volatility, "
        f"predictability, and trap safety) with an edge strength of **{edge}/5**. "
        f"It is currently {status} for scalping. "
        f"This is a {vol_desc} stock (ATR {atr:.1f}% — meaning it typically moves about "
        f"{atr:.1f}% from its daily low to high) and {beta_desc}."
    )

    # ── Para 2: Best combo deep-dive ──
    best_c, best_g = None, None
    for g, c in ev_combos.items():
        if c and (best_c is None or c["ev_adjusted"] > best_c["ev_adjusted"]):
            best_c = c
            best_g = g
    if best_c:
        target = best_c["target_pct"]
        stop = best_c["stop_pct"]
        wr = best_c["win_rate"]
        n = best_c["n"]
        ev = best_c["ev_adjusted"]
        approx_wins = int(round(wr * n / 100))
        approx_losses = n - approx_wins
        rr = target / stop if stop > 0 else 0

        # EV in rupee terms per ₹100 risked
        ev_per_100 = ev * 100 / stop if stop > 0 else 0

        paras.append(
            f"**Best setup:** On **{best_g}** days, enter long targeting +{target}% with a "
            f"-{stop}% stop (risk:reward = 1:{rr:.1f}). Over {n} historical trades, "
            f"{approx_wins} hit target and {approx_losses} stopped out — a **{wr:.0f}% hit rate**. "
            f"The expected value (EV) is **{ev:.3f}%** per trade after accounting for "
            f"brokerage + STT + slippage ({ROUND_TRIP_COST_PCT}% round-trip). "
            f"In practical terms: for every ₹1,00,000 deployed, you'd expect to make roughly "
            f"₹{ev * 1000:.0f} per trade on average — small per trade, but compounds over "
            f"dozens of setups."
        )

        # FDR significance
        if best_c.get("fdr_significant"):
            paras.append(
                "This combo **passed FDR correction** (Benjamini-Hochberg at 10%), which means "
                "even after testing many target/stop combinations, this edge is statistically "
                "unlikely to be a fluke. Think of it like p-hacking protection — we tested dozens "
                "of combos and this one still holds up."
            )
        else:
            paras.append(
                "**Caveat:** This combo did *not* pass FDR correction — when we account for the "
                "many target/stop combinations tested, the apparent edge could be due to chance. "
                "Trade with smaller size until more data confirms the pattern."
            )

        # Kelly sizing interpretation
        kelly = risk.get("kelly_fraction", 0)
        aggr = risk.get("aggressiveness", "low")
        if kelly > 0:
            paras.append(
                f"The half-Kelly fraction is **{kelly:.3f}**, suggesting **{aggr}** position sizing. "
                + (
                    "Kelly says you have a meaningful edge worth betting on — size up within your risk limits."
                    if aggr == "high" else
                    "Kelly suggests a moderate edge — standard position sizes are appropriate."
                    if aggr == "medium" else
                    "Kelly fraction is low — the edge is thin. Keep positions small and focus on volume of trades."
                )
            )
    else:
        paras.append(
            "No target/stop combination produced a positive expected value after transaction costs. "
            "This means that across all gap types tested, the stock doesn't show a reliable, "
            "repeatable scalping edge in the backtest period."
        )

    # ── Para 3: Phase timing ──
    if active_phases:
        bp = max(active_phases, key=lambda p: p["win_rate"])
        phase_details = []
        for p in sorted(active_phases, key=lambda p: -p["win_rate"]):
            phase_details.append(f"{p['phase']} ({p['window']}, {p['win_rate']:.0f}% WR, N={p['n']})")
        paras.append(
            f"**When to trade:** The best window is **{bp['phase']}** ({bp['window']}) with a "
            f"**{bp['win_rate']:.0f}% win rate** across {bp['n']} trading days. "
            + (
                f"Other active phases: {', '.join(phase_details[1:])}. "
                if len(phase_details) > 1 else ""
            )
            + "These win rates passed either a binomial significance test (p < 0.10) or the "
            "practical threshold (> 55% WR), confirming the time-of-day pattern isn't random."
        )
    else:
        paras.append(
            "No time window showed a statistically significant or practically meaningful win rate "
            "advantage. Without a clear time-of-day edge, entries become less predictable."
        )

    # ── Para 4: Gap landscape ──
    bullish_gaps = [g for g, gs in gap_stats.items()
                    if not gs.get("is_bearish") and gs.get("pct_of_days", 0) > 5]
    bearish_gaps = [g for g, gs in gap_stats.items()
                    if gs.get("is_bearish") and gs.get("pct_of_days", 0) > 3]
    tradable_pct = sum(
        gap_stats[g]["pct_of_days"] for g in ev_combos
        if ev_combos[g] is not None and ev_combos[g]["ev_adjusted"] > 0
        and not gap_stats.get(g, {}).get("is_bearish", True)
    )
    if bullish_gaps or bearish_gaps:
        gap_para = (
            f"**Gap type landscape:** Roughly {tradable_pct:.0f}% of trading days fall into "
            f"gap types with a positive EV setup. "
        )
        if bullish_gaps:
            gap_para += f"Favorable gap types: {', '.join(bullish_gaps)}. "
        if bearish_gaps:
            gap_para += (
                f"Avoid: {', '.join(bearish_gaps)} — these show bearish intraday behavior "
                f"(low up-close rate or negative avg open-to-close), meaning the stock tends "
                f"to fade after the open on these days."
            )
        paras.append(gap_para)

    # ── Para 5: Trap warnings ──
    traps = [(g, gs) for g, gs in gap_stats.items() if gs.get("up_close_rate", 100) < 35]
    if traps:
        trap_details = []
        for g, gs in traps:
            uc = gs["up_close_rate"]
            trap_details.append(
                f"**{g}** (only {uc:.0f}% close above open — "
                + ("a severe trap" if uc < 15 else "high reversal risk") + ")"
            )
        paras.append(
            f"**Trap alert:** {'; '.join(trap_details)}. "
            f"On these gap types, the stock opens with apparent direction but reverses intraday "
            f"— entering long on a gap-up that fades is the classic gap-and-trap. "
            f"The scanner automatically excludes these from preferred gap types."
        )

    # ── Para 6: OOS validation ──
    if oos.get("degraded"):
        paras.append(
            "**Out-of-sample warning:** When we split the data 70/30 (train on older data, test "
            "on recent), the edge degraded significantly in the recent period — either win rate "
            "dropped >15 percentage points or EV went negative. This is a red flag: the pattern "
            "may be fading. Edge strength was penalized by 1 point as a result."
        )
    elif oos.get("oos_results"):
        tested = sum(1 for r in oos["oos_results"].values() if r.get("ev") is not None)
        if tested > 0:
            paras.append(
                f"**Out-of-sample validation passed** ({tested} gap type(s) validated). "
                "The edge held up when tested on the most recent 30% of data that wasn't used "
                "to find the pattern — a good sign that this isn't just curve-fitting."
            )

    # ── Para 7: MAE analysis ──
    mae = cfg.get("_mae_analysis", {})
    if mae:
        best_mae = None
        for k, v in mae.items():
            if best_c and best_g and k.startswith(best_g):
                best_mae = v
                break
        if not best_mae and mae:
            best_mae = next(iter(mae.values()))
        if best_mae:
            paras.append(
                f"**Stop-loss optimization (MAE analysis):** Among winning trades, the median "
                f"adverse excursion was **{best_mae['median_mae_pct']:.2f}%** — meaning even "
                f"winners typically dip this much before recovering. The 90th percentile MAE is "
                f"**{best_mae['p90_mae_pct']:.2f}%**, and **{best_mae['pct_winners_beyond_stop']:.0f}%** "
                f"of winners dipped beyond the current stop level. Suggested optimal stop: "
                f"**{best_mae['optimal_stop_pct']:.2f}%** (p90 MAE + 10% buffer)."
            )

    # ── Para 8: DOW stats ──
    dow = cfg.get("_dow_stats", {})
    if dow and dow.get("stats"):
        dow_lines = []
        for day_name, ds in dow["stats"].items():
            dow_lines.append(f"{day_name}: {ds['win_rate']:.0f}% WR, {ds['avg_oc_pct']:+.2f}% avg O→C, {ds['trap_rate']:.0f}% trap rate")
        avoid = dow.get("avoid_days", [])
        day_names_map = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"}
        avoid_str = ", ".join(day_names_map.get(d, str(d)) for d in avoid) if avoid else "none"
        paras.append(
            f"**Day-of-week seasonality:** {'; '.join(dow_lines)}. "
            f"Days to avoid (WR < {DOW_WR_AVOID_THRESHOLD}%): **{avoid_str}**."
        )

    # ── Para 9: Monte Carlo CI ──
    mc = cfg.get("_mc_results", {})
    if mc:
        mc_lines = []
        for gt, mc_data in mc.items():
            fragile_tag = " **FRAGILE**" if mc_data.get("fragile") else ""
            mc_lines.append(
                f"{gt}: EV 95% CI [{mc_data['ev_ci_lower']:.3f}, {mc_data['ev_ci_upper']:.3f}], "
                f"WR [{mc_data['wr_ci_lower']:.0f}%, {mc_data['wr_ci_upper']:.0f}%], "
                f"max DD p95 = {mc_data['max_dd_p95']:.2f}%{fragile_tag}"
            )
        paras.append(
            f"**Monte Carlo confidence intervals** ({MONTE_CARLO_ITERS:,} bootstrap resamples): "
            + "; ".join(mc_lines) + "."
        )

    # ── Para 10: Enabled/disabled verdict ──
    if not cfg["enabled"]:
        reasons = []
        if edge < MIN_EDGE_FOR_ENABLE:
            reasons.append(f"edge strength {edge}/5 is below the minimum {MIN_EDGE_FOR_ENABLE}/5")
        if score < MIN_TRADABILITY:
            reasons.append(f"tradability score {score}/100 is below the {MIN_TRADABILITY}/100 threshold")
        if not any(p["win_rate"] > 50 for p in active_phases):
            reasons.append("no active phase has win rate above 50%")
        reason_str = "; ".join(reasons) if reasons else "combined factors fell below thresholds"
        paras.append(
            f"**Verdict: DISABLED.** {reason_str}. "
            "The stock stays in the watchlist for monitoring but won't generate live signals. "
            "If market conditions change or more data accumulates, re-running the generator may promote it."
        )
    else:
        paras.append(
            f"**Verdict: ENABLED** with edge strength {edge}/5 and {risk.get('aggressiveness', 'medium')} "
            f"aggressiveness. The scanner will generate live signals for this ticker during active phases."
        )

    return "\n\n".join(paras)


def _generate_llm_explanation(cfg: dict, config: dict) -> str | None:
    """Ask LLM to explain a single ticker config. Returns explanation string or None."""
    import json as json_mod

    system_prompt = (
        "You are a seasoned trading mentor writing a detailed, educational explanation of a "
        "scalping configuration for an Indian retail trader who is learning to read data-driven "
        "setups. Your goal is to make the reader *understand trading concepts deeply* — not just "
        "what the numbers say, but WHY they matter and HOW to use them. This explanation should "
        "teach something about trading that the reader can apply tomorrow.\n\n"
        "Write generously — 6-10 paragraphs covering:\n\n"
        "1. **Stock profile & character:** Interpret the tradability sub-scores (liquidity, "
        "volatility, predictability, trap safety). Explain ATR in rupee terms for someone with "
        "₹1-2L capital. Explain beta as 'if Nifty falls 1%, this stock falls X%'. "
        "Paint a picture of the stock's personality.\n\n"
        "2. **Best combo deep-dive:** This is the core — explain it like a sports commentator. "
        "'Out of N times this setup occurred, X times price reached the target before the stop "
        "— that's like a batsman scoring runs X out of N innings.' Convert EV to rupees per "
        "₹1,00,000 deployed. Explain risk:reward — 'you're risking ₹X to make ₹Y'. "
        "Explain what positive EV means for monthly P&L. Mention transaction costs.\n\n"
        "3. **Statistical robustness:** Did it pass FDR correction? Explain in simple terms — "
        "'we tested dozens of combos, and this is like a student who scores well even after "
        "the teacher curves the grades'. If not, warn the edge might be a mirage.\n\n"
        "4. **Kelly & position sizing:** Translate Kelly fraction into practical advice. "
        "'With Kelly at 0.15, you could allocate 15% per trade — but we use half-Kelly because "
        "real markets are messier than backtests.'\n\n"
        "5. **When to trade (timing is everything):** Explain why morning vs afternoon behaves "
        "differently in Indian markets. 'Morning has gap resolution + FII flows, afternoon has "
        "institutional positioning before close.' Tie win rate to the specific window.\n\n"
        "6. **Gap type landscape:** Teach what each gap type means. 'small_up means the stock "
        "opened 0.25-1% above yesterday — often continuation.' Which are friends, which are traps? "
        "What % of days give a tradeable setup?\n\n"
        "7. **Trap warnings (learn from losses):** For trap gaps, explain mechanics: 'Stock gaps "
        "up, retail piles in, institutions sell into strength — by 11am it's red.' Teach "
        "recognition.\n\n"
        "8. **OOS validation (reality check):** Explain walk-forward testing simply — 'like "
        "studying from last year's papers then taking this year's exam.'\n\n"
        "9. **Final verdict & actionable takeaway:** Summary paragraph — trade it or not, "
        "sizing, timing, watchouts. Be opinionated.\n\n"
        "**Style:** Use **bold** for key numbers. Use analogies from cricket, everyday life, "
        "simple math. Be specific — cite actual numbers. Don't use generic phrases like "
        "'solid candidate'. Every sentence should teach or cite a number.\n\n"
        "Return ONLY the explanation text (markdown). No JSON wrapper needed. "
        "Use blank lines between paragraphs."
    )

    sym = cfg["symbol"].replace(".NS", "")
    meta = cfg["_meta"]
    ev_combos = cfg["_ev_combos"]
    active_phases = cfg["_active_phases"]
    gap_stats = cfg["_gap_stats"]
    oos = cfg.get("_oos", {})
    risk = cfg.get("risk", {})

    best_c, best_g = None, None
    for g, c in ev_combos.items():
        if c and (best_c is None or c["ev_adjusted"] > best_c["ev_adjusted"]):
            best_c = c
            best_g = g

    summary = {
        "symbol": sym,
        "name": meta.get("name", ""),
        "enabled": cfg["enabled"],
        "edge_strength": cfg["edge_strength"],
        "tradability": meta.get("score_overall", 0),
        "sub_scores": {
            "liquidity": meta.get("score_liquidity", 0),
            "volatility": meta.get("score_volatility", 0),
            "predictability": meta.get("score_predictability", 0),
            "trap_safety": meta.get("score_trap_safety", 0),
        },
        "atr_pct": float(meta.get("atr_pct", 0)),
        "beta": float(meta.get("beta", 1.0)),
        "kelly_fraction": float(risk.get("kelly_fraction", 0)),
        "aggressiveness": risk.get("aggressiveness", "low"),
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
    }
    if best_c:
        target = best_c["target_pct"]
        stop = best_c["stop_pct"]
        approx_wins = int(round(best_c["win_rate"] * best_c["n"] / 100))
        summary["best_combo"] = {
            "gap_type": best_g,
            "target": float(target),
            "stop": float(stop),
            "risk_reward": f"1:{target / stop:.1f}" if stop > 0 else "N/A",
            "win_rate": float(round(best_c["win_rate"], 1)),
            "wins": approx_wins,
            "losses": best_c["n"] - approx_wins,
            "n": int(best_c["n"]),
            "ev": float(round(best_c["ev_adjusted"], 3)),
            "ev_per_lakh": f"₹{best_c['ev_adjusted'] * 1000:.0f}",
            "fdr_significant": bool(best_c.get("fdr_significant", False)),
        }
    summary["gap_types"] = {}
    for gt, gs in gap_stats.items():
        combo = ev_combos.get(gt)
        summary["gap_types"][gt] = {
            "pct_of_days": int(round(gs["pct_of_days"])),
            "avg_oc_pct": float(round(gs["avg_oc_pct"], 2)),
            "up_close_rate": int(round(gs["up_close_rate"])),
            "is_bearish": bool(gs.get("is_bearish", False)),
            "is_trap": bool(gs.get("up_close_rate", 100) < 35),
            "ev": float(round(combo["ev_adjusted"], 3)) if combo else None,
        }
    if active_phases:
        summary["phases"] = []
        for p in sorted(active_phases, key=lambda p: -p["win_rate"]):
            summary["phases"].append({
                "name": p["phase"], "window": p["window"],
                "wr": float(round(p["win_rate"], 1)), "n": int(p["n"]),
            })
    if oos.get("degraded"):
        summary["oos_degraded"] = True
    elif oos.get("oos_results"):
        tested = sum(1 for r in oos["oos_results"].values() if r.get("ev") is not None)
        if tested > 0:
            summary["oos_passed"] = tested

    user_content = json_mod.dumps(summary, indent=2)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Explain this ticker's scalping configuration:\n\n{user_content}"},
    ]

    return _call_llm(config, messages)


def generate_documentation(configs: list[dict], existing_config: dict):
    """Generate scalp_config_guide.md with stats tables and LLM/template explanations."""
    from datetime import date

    today = date.today().strftime("%Y-%m-%d")
    total = len(configs)
    enabled_count = sum(1 for c in configs if c["enabled"])

    lines = []
    lines.append("# Scalp Configuration Guide")
    lines.append(f"*Auto-generated on {today} — {total} tickers ({enabled_count} enabled)*")
    lines.append("")

    # Quick Reference table
    lines.append("## Quick Reference")
    lines.append("")
    lines.append("| Ticker | On | Edge | Best EV | Best Phase | Verdict |")
    lines.append("|--------|:--:|:----:|--------:|------------|---------|")

    for cfg in configs:
        sym = cfg["symbol"].replace(".NS", "")
        on = "Yes" if cfg["enabled"] else "No"
        edge = f"{cfg['edge_strength']}/5"

        ev_combos = cfg["_ev_combos"]
        best_c = None
        for g, c in ev_combos.items():
            if c and (best_c is None or c["ev_adjusted"] > best_c["ev_adjusted"]):
                best_c = c

        best_ev = f"{best_c['ev_adjusted']:.3f}" if best_c else "N/A"

        active_phases = cfg["_active_phases"]
        if active_phases:
            bp = max(active_phases, key=lambda p: p["win_rate"])
            phase_str = f"{bp['phase']} ({bp['win_rate']:.0f}%)"
        else:
            phase_str = "N/A"

        if cfg["enabled"]:
            verdict = f"Trade — edge {cfg['edge_strength']}/5"
        else:
            verdict = "Skip — insufficient edge"

        lines.append(f"| {sym} | {on} | {edge} | {best_ev} | {phase_str} | {verdict} |")

    lines.append("")

    # Generate LLM explanations — one ticker at a time for best quality
    llm_explanations = {}
    llm_failures = 0
    for idx, cfg in enumerate(configs, 1):
        sym = cfg["symbol"].replace(".NS", "")
        print(f"  [{idx}/{total}] Generating explanation for {sym}...", end=" ", flush=True)
        explanation = _generate_llm_explanation(cfg, existing_config)
        if explanation:
            llm_explanations[sym] = explanation
            print("ok")
        else:
            llm_failures += 1
            print("fallback to template")
            if llm_failures >= 3 and not llm_explanations:
                # LLM is completely unavailable, skip remaining calls
                print("  LLM unavailable — using templates for remaining tickers")
                break

    generated = len(llm_explanations)
    templated = total - generated
    if generated > 0:
        print(f"  LLM: {generated} tickers | Templates: {templated} tickers")
    else:
        print("  Using template explanations for all tickers (LLM unavailable)")

    # Per-ticker sections
    lines.append("---")
    lines.append("")

    for cfg in configs:
        sym = cfg["symbol"].replace(".NS", "")
        meta = cfg["_meta"]
        ev_combos = cfg["_ev_combos"]
        active_phases = cfg["_active_phases"]
        gap_stats = cfg["_gap_stats"]
        oos = cfg.get("_oos", {})

        name = meta.get("name", "")
        lines.append(f"## {sym}" + (f" — {name}" if name else ""))
        lines.append("")

        # Key Statistics table
        lines.append("### Key Statistics")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Tradability Score | {meta.get('score_overall', '?')}/100 |")
        lines.append(f"| ATR(14) | {meta.get('atr_pct', '?')}% |")
        lines.append(f"| Beta vs Nifty | {meta.get('beta', '?')} |")
        lines.append(f"| Edge Strength | {cfg['edge_strength']}/5 |")
        kelly = cfg.get("risk", {}).get("kelly_fraction", "?")
        lines.append(f"| Kelly Fraction | {kelly} |")
        lines.append(f"| Aggressiveness | {cfg.get('risk', {}).get('aggressiveness', '?')} |")

        # OOS status
        if oos.get("degraded"):
            lines.append(f"| OOS Validation | **DEGRADED** — edge weakened in recent 30% |")
        else:
            oos_results = oos.get("oos_results", {})
            tested = sum(1 for r in oos_results.values() if r.get("ev") is not None)
            if tested > 0:
                lines.append(f"| OOS Validation | Passed ({tested} gap types validated) |")
            else:
                lines.append(f"| OOS Validation | Insufficient data |")

        lines.append("")

        # Gap Type Analysis table
        if gap_stats:
            lines.append("### Gap Type Analysis")
            lines.append("")
            lines.append("| Gap Type | Freq | Avg O→C | Up-Close% | Bearish? | EV |")
            lines.append("|----------|-----:|--------:|----------:|:--------:|---:|")

            for gap_type in sorted(gap_stats.keys()):
                gs = gap_stats[gap_type]
                combo = ev_combos.get(gap_type)
                ev_str = f"{combo['ev_adjusted']:.3f}" if combo else "N/A"
                bearish = "Yes" if gs.get("is_bearish") else "No"
                lines.append(
                    f"| {gap_type} | {gs['pct_of_days']:.0f}% "
                    f"| {gs['avg_oc_pct']:.2f}% "
                    f"| {gs['up_close_rate']:.0f}% "
                    f"| {bearish} "
                    f"| {ev_str} |"
                )
            lines.append("")

        # What This Means — LLM or template
        lines.append("### What This Means")
        lines.append("")
        explanation = llm_explanations.get(sym) or _generate_template_explanation(cfg)
        lines.append(explanation)
        lines.append("")

        # Trap Warnings
        traps = [(g, gs) for g, gs in gap_stats.items() if gs.get("up_close_rate", 100) < 35]
        if traps:
            lines.append("### Trap Warnings")
            lines.append("")
            for g, gs in traps:
                lines.append(
                    f"- **{g}**: Only {gs['up_close_rate']:.0f}% of days close above open — "
                    f"price frequently reverses after the gap. Avoid longs on these days."
                )
            lines.append("")

        lines.append("---")
        lines.append("")

    # Glossary
    lines.append("## Glossary")
    lines.append("")
    for term, definition in GLOSSARY.items():
        lines.append(f"- **{term}**: {definition}")
    lines.append("")

    DOC_PATH.write_text("\n".join(lines))
    print(f"Wrote {DOC_PATH} ({len(configs)} tickers documented)")


def main():
    parser = argparse.ArgumentParser(description="Generate scalp config (fetch → compute → cache → YAML)")
    parser.add_argument("--skip-explanation", action="store_true",
                        help="Skip generating LLM/template explanations (faster)")
    parser.add_argument("--force", action="store_true",
                        help="Force recomputation even if cache is fresh")
    args = parser.parse_args()

    all_symbols = list(TICKERS.keys())
    print(f"Processing {len(all_symbols)} tickers...")

    # 1. Determine which symbols need recomputation
    if args.force:
        stale_symbols = all_symbols
        print(f"  Force mode: recomputing all {len(stale_symbols)} tickers")
    else:
        stale_symbols = []
        for symbol in all_symbols:
            cached_meta = get_cached("scalp_metadata", symbol=symbol, max_age_seconds=TTL_DAILY)
            if cached_meta is None:
                stale_symbols.append(symbol)
        if stale_symbols:
            print(f"  {len(stale_symbols)} stale tickers need recomputation")
        else:
            print(f"  All tickers cached and fresh — skipping computation")

    # 2. Fetch benchmark once if needed
    bench_daily = None
    if stale_symbols:
        print("  Fetching benchmark data...")
        bench_daily = fetch_yf(BENCHMARK, period="6mo", interval="1d")
        if bench_daily.empty:
            print("[ERROR] Could not fetch benchmark data. Exiting.")
            return

    # 3. For stale symbols: fetch OHLCV in parallel → compute → cache
    if stale_symbols:
        # Pre-fetch all ticker data in parallel
        print(f"  Fetching {len(stale_symbols)} tickers in parallel...")
        _bulk_data = fetch_bulk(stale_symbols, {
            "daily": ("6mo", "1d"),
            "intra": ("60d", "5m"),
        }, max_workers=8, label="ScalpCfg")

        # Pre-fetch sector indices (deduplicated)
        _sector_keys = list({TICKERS[s]["sector"] for s in stale_symbols if TICKERS[s].get("sector")})
        print(f"  Fetching {len(_sector_keys)} sector indices...")
        _sector_cache = fetch_bulk_single(_sector_keys, "6mo", "1d", max_workers=6, label="Sectors")

        for i, symbol in enumerate(stale_symbols, 1):
            cfg = TICKERS[symbol]
            print(f"  [{i}/{len(stale_symbols)}] {symbol} ({cfg['name']})")

            daily_df = _bulk_data.get(symbol, {}).get("daily", pd.DataFrame())
            if daily_df.empty:
                print(f"    [SKIP] No daily data")
                continue

            intraday_df = _bulk_data.get(symbol, {}).get("intra", pd.DataFrame())
            if intraday_df.empty:
                print(f"    [SKIP] No intraday data")
                continue

            sector_key = cfg["sector"]
            sector_daily = _sector_cache.get(sector_key, pd.DataFrame())

            info = fetch_ticker_info(symbol)

            try:
                compute_and_cache_ticker(symbol, cfg, daily_df, intraday_df,
                                          bench_daily, sector_daily, info)
                print(f"    Computed and cached")
            except Exception as e:
                print(f"    [ERROR] {e}")
                continue

    # 4. Process ALL symbols from cache → generate config
    configs = []
    skipped = []
    for symbol in all_symbols:
        result = process_ticker(symbol)
        if result:
            configs.append(result)
        else:
            skipped.append(symbol)

    if skipped:
        print(f"Skipped {len(skipped)} tickers (no cached data): {', '.join(skipped)}")

    # 5. Re-score using PCA-derived weights (data-driven)
    configs = compute_pca_edge_strengths(configs)

    # 6. Sort by edge_strength descending, then by overall score
    configs.sort(key=lambda c: (-c["edge_strength"], -c.get("_meta", {}).get("score_overall", 0)))

    # 7. Load existing config for global/ranking/positions
    existing = load_existing_config()

    # 8. Build and write YAML
    yaml_str = build_yaml(configs, existing)
    CONFIG_PATH.write_text(yaml_str)
    print(f"Wrote {CONFIG_PATH} with {len(configs)} tickers")

    # 9. Generate documentation guide (unless skipped)
    if args.skip_explanation:
        print("Skipping explanation generation (--skip-explanation)")
    else:
        generate_documentation(configs, existing)

    # 10. Print summary
    print_summary(configs)


if __name__ == "__main__":
    main()
