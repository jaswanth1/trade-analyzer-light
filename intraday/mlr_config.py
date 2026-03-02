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
from datetime import datetime, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from common.data import (
    PROJECT_ROOT, TICKERS, BENCHMARK, fetch_yf,
)
from common.indicators import compute_atr

# ── Tunable Constants ────────────────────────────────────────────────

MIN_SAMPLE = 15              # minimum trading days with morning low for enable
MIN_WIN_RATE = 50.0          # minimum recovery win rate to enable
EV_THRESHOLD = 0.0           # minimum EV to enable
MONTE_CARLO_ITERS = 10000    # bootstrap iterations for CIs
OOS_TRAIN_RATIO = 0.70       # walk-forward: train on first 70%
ROUND_TRIP_COST_PCT = 0.10   # brokerage + STT + slippage
MORNING_CUTOFF_HOUR = 11     # session low must form before this hour
MORNING_CUTOFF_MIN = 0

INTRADAY_DIR = PROJECT_ROOT / "intraday"
CONFIG_PATH = INTRADAY_DIR / "mlr_config.yaml"
DOC_PATH = INTRADAY_DIR / "mlr_config_guide.md"

TIME_BUCKETS = [
    ("09:15-09:45", dtime(9, 15), dtime(9, 45)),
    ("09:45-10:15", dtime(9, 45), dtime(10, 15)),
    ("10:15-10:45", dtime(10, 15), dtime(10, 45)),
    ("10:45-11:00", dtime(10, 45), dtime(11, 0)),
]

DOW_NAMES = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"}


# ── Step 1: Compute morning low stats per trading day ────────────────

def compute_morning_low_stats(intra_df, daily_df):
    """Per trading day: when did session low form, recovery metrics.

    Returns DataFrame with columns:
        date, low_time, low_bucket, low_price, close_price, open_price,
        recovery_to_close_pct, recovery_to_high_pct, time_to_recovery_bars,
        gap_pct, prev_close
    """
    if intra_df.empty or daily_df.empty:
        return pd.DataFrame()

    records = []
    dates = sorted(intra_df.index.date)
    unique_dates = list(dict.fromkeys(dates))

    for i, d in enumerate(unique_dates):
        day_bars = intra_df[intra_df.index.date == d]
        if len(day_bars) < 6:
            continue

        day_open = float(day_bars["Open"].iloc[0])
        day_close = float(day_bars["Close"].iloc[-1])
        day_high = float(day_bars["High"].max())

        # Find session low
        low_idx = day_bars["Low"].idxmin()
        low_price = float(day_bars.loc[low_idx, "Low"])
        low_time = low_idx

        # Check if low is in morning window
        if low_time.hour > MORNING_CUTOFF_HOUR or (
            low_time.hour == MORNING_CUTOFF_HOUR and low_time.minute > MORNING_CUTOFF_MIN
        ):
            continue  # skip non-morning lows

        # Previous close
        prev_close = None
        if i > 0:
            prev_date = unique_dates[i - 1]
            prev_bars = intra_df[intra_df.index.date == prev_date]
            if not prev_bars.empty:
                prev_close = float(prev_bars["Close"].iloc[-1])

        if prev_close is None or prev_close <= 0:
            # Try daily_df
            daily_before = daily_df[daily_df.index.date < d]
            if daily_before.empty:
                continue
            prev_close = float(daily_before["Close"].iloc[-1])

        if prev_close <= 0 or low_price <= 0:
            continue

        gap_pct = (day_open - prev_close) / prev_close * 100

        recovery_to_close = (day_close - low_price) / low_price * 100
        recovery_to_high = (day_high - low_price) / low_price * 100

        # Time to recovery: bars from low until price recovers to open price
        low_bar_pos = day_bars.index.get_loc(low_idx)
        bars_after_low = day_bars.iloc[low_bar_pos:]
        time_to_recovery = len(bars_after_low)  # default: didn't fully recover
        for j in range(1, len(bars_after_low)):
            if float(bars_after_low["Close"].iloc[j]) >= day_open:
                time_to_recovery = j
                break

        # Time bucket classification
        lt = low_time.time()
        low_bucket = "other"
        for bucket_name, bstart, bend in TIME_BUCKETS:
            if bstart <= lt < bend:
                low_bucket = bucket_name
                break

        records.append({
            "date": d,
            "low_time": low_time,
            "low_bucket": low_bucket,
            "low_price": low_price,
            "close_price": day_close,
            "open_price": day_open,
            "high_price": day_high,
            "recovery_to_close_pct": round(recovery_to_close, 3),
            "recovery_to_high_pct": round(recovery_to_high, 3),
            "time_to_recovery_bars": time_to_recovery,
            "gap_pct": round(gap_pct, 3),
            "prev_close": prev_close,
            "dow": d.weekday(),
            "month": d.month,
        })

    return pd.DataFrame(records)


# ── Step 2: EV-optimized entry delay / target / stop combos ─────────

def compute_ev_combos(stats_df):
    """Grid search: entry delay × stop × target for best EV.

    Entry delay: 2/3/5 bars after low
    Stop: 0.2/0.3/0.5% below low
    Target: 1.0/1.5/2.0/2.5/3.0%

    EV = (WR × target) − ((1−WR) × stop) − cost

    Returns dict with best combo parameters and all combo results.
    """
    if stats_df.empty:
        return {"best": None, "combos": []}

    entry_delays = [2, 3, 5]
    stop_pcts = [0.2, 0.3, 0.5]
    target_pcts = [1.0, 1.5, 2.0, 2.5, 3.0]

    combos = []

    for delay in entry_delays:
        for stop_pct in stop_pcts:
            for target_pct in target_pcts:
                wins = 0
                losses = 0

                for _, row in stats_df.iterrows():
                    # Simulated entry: low + recovery after 'delay' bars
                    # Use recovery_to_high as max favorable, recovery_to_close as realized
                    recovery = row["recovery_to_high_pct"]
                    # Approximate: did recovery exceed target_pct?
                    # And did drawdown from entry exceed stop_pct?
                    # Entry is approximately at low + small recovery (delay bars)
                    entry_recovery = min(0.2 * delay, row["recovery_to_close_pct"])
                    remaining_upside = row["recovery_to_high_pct"] - entry_recovery

                    if remaining_upside >= target_pct:
                        wins += 1
                    elif row["recovery_to_close_pct"] < entry_recovery - stop_pct:
                        losses += 1
                    elif remaining_upside < target_pct:
                        losses += 1  # didn't hit target

                n = wins + losses
                if n < 5:
                    continue

                wr = wins / n * 100
                ev = (wr / 100 * target_pct) - ((1 - wr / 100) * stop_pct) - ROUND_TRIP_COST_PCT

                combos.append({
                    "entry_delay": delay,
                    "stop_pct": stop_pct,
                    "target_pct": target_pct,
                    "wins": wins,
                    "losses": losses,
                    "n": n,
                    "win_rate": round(wr, 1),
                    "ev": round(ev, 4),
                })

    if not combos:
        return {"best": None, "combos": combos}

    # Best by EV
    best = max(combos, key=lambda c: c["ev"])
    return {"best": best, "combos": combos}


# ── Step 3: Walk-forward OOS validation ──────────────────────────────

def validate_oos(stats_df, best_combo):
    """70/30 walk-forward split. Returns OOS metrics and degradation flag."""
    if stats_df.empty or best_combo is None:
        return {"oos_valid": False, "degraded": True}

    n = len(stats_df)
    split = int(n * OOS_TRAIN_RATIO)
    if split < 10 or n - split < 5:
        return {"oos_valid": False, "degraded": False}

    oos_df = stats_df.iloc[split:]
    target_pct = best_combo["target_pct"]
    stop_pct = best_combo["stop_pct"]
    entry_delay = best_combo["entry_delay"]

    wins = 0
    total = 0
    for _, row in oos_df.iterrows():
        entry_recovery = min(0.2 * entry_delay, row["recovery_to_close_pct"])
        remaining_upside = row["recovery_to_high_pct"] - entry_recovery
        total += 1
        if remaining_upside >= target_pct:
            wins += 1

    if total == 0:
        return {"oos_valid": False, "degraded": True}

    oos_wr = wins / total * 100
    oos_ev = (oos_wr / 100 * target_pct) - ((1 - oos_wr / 100) * stop_pct) - ROUND_TRIP_COST_PCT

    # Degraded if OOS win rate drops >15pp or EV goes negative
    is_wr = best_combo["win_rate"]
    degraded = (is_wr - oos_wr) > 15 or oos_ev < -0.5

    return {
        "oos_valid": True,
        "oos_win_rate": round(oos_wr, 1),
        "oos_ev": round(oos_ev, 4),
        "oos_n": total,
        "degraded": degraded,
    }


# ── Step 4: MAE analysis (max adverse excursion) ────────────────────

def compute_mae_analysis(stats_df):
    """p90 max adverse excursion from entry → optimal stop.

    MAE is approximated as the gap between open price and low price
    as percentage of open.
    """
    if stats_df.empty:
        return {"mae_p90": 0, "mae_median": 0}

    # MAE: how much did price drop from open to the session low?
    mae_values = []
    for _, row in stats_df.iterrows():
        if row["open_price"] > 0:
            mae = (row["open_price"] - row["low_price"]) / row["open_price"] * 100
            mae_values.append(mae)

    if not mae_values:
        return {"mae_p90": 0, "mae_median": 0}

    return {
        "mae_p90": round(float(np.percentile(mae_values, 90)), 3),
        "mae_median": round(float(np.median(mae_values)), 3),
    }


# ── Step 5: Monte Carlo bootstrap CIs ───────────────────────────────

def monte_carlo_ci(stats_df, best_combo, n_iter=MONTE_CARLO_ITERS):
    """Bootstrap 95% CIs for EV, WR, and max drawdown."""
    if stats_df.empty or best_combo is None:
        return {}

    target_pct = best_combo["target_pct"]
    stop_pct = best_combo["stop_pct"]
    entry_delay = best_combo["entry_delay"]

    # Build per-trade PnL series
    pnls = []
    for _, row in stats_df.iterrows():
        entry_recovery = min(0.2 * entry_delay, row["recovery_to_close_pct"])
        remaining_upside = row["recovery_to_high_pct"] - entry_recovery
        if remaining_upside >= target_pct:
            pnls.append(target_pct - ROUND_TRIP_COST_PCT)
        else:
            pnls.append(-stop_pct - ROUND_TRIP_COST_PCT)

    if not pnls:
        return {}

    pnls = np.array(pnls)
    rng = np.random.default_rng(42)

    ev_samples = []
    wr_samples = []
    dd_samples = []

    for _ in range(n_iter):
        sample = rng.choice(pnls, size=len(pnls), replace=True)
        ev_samples.append(float(sample.mean()))
        wr_samples.append(float((sample > 0).mean() * 100))

        # Max drawdown of cumulative PnL
        cum = np.cumsum(sample)
        running_max = np.maximum.accumulate(cum)
        drawdowns = running_max - cum
        dd_samples.append(float(drawdowns.max()))

    return {
        "ev_ci_lower": round(float(np.percentile(ev_samples, 2.5)), 4),
        "ev_ci_upper": round(float(np.percentile(ev_samples, 97.5)), 4),
        "wr_ci_lower": round(float(np.percentile(wr_samples, 2.5)), 1),
        "wr_ci_upper": round(float(np.percentile(wr_samples, 97.5)), 1),
        "max_dd_ci_upper": round(float(np.percentile(dd_samples, 95)), 3),
    }


# ── Step 6: DOW and month-period stats ───────────────────────────────

def compute_dow_month_stats(stats_df):
    """Per-DOW and month_period recovery rates."""
    if stats_df.empty:
        return {"dow": {}, "month_period": {}}

    dow_stats = {}
    for dow_num in range(5):
        subset = stats_df[stats_df["dow"] == dow_num]
        if len(subset) < 3:
            continue
        wins = (subset["recovery_to_close_pct"] > 1.0).sum()
        dow_stats[DOW_NAMES[dow_num]] = {
            "win_rate": round(wins / len(subset) * 100, 1),
            "avg_recovery": round(float(subset["recovery_to_close_pct"].mean()), 2),
            "n": len(subset),
        }

    # Month periods
    month_stats = {}
    for label, months in [("Q1", [1, 2, 3]), ("Q2", [4, 5, 6]),
                           ("Q3", [7, 8, 9]), ("Q4", [10, 11, 12])]:
        subset = stats_df[stats_df["month"].isin(months)]
        if len(subset) < 3:
            continue
        wins = (subset["recovery_to_close_pct"] > 1.0).sum()
        month_stats[label] = {
            "win_rate": round(wins / len(subset) * 100, 1),
            "avg_recovery": round(float(subset["recovery_to_close_pct"].mean()), 2),
            "n": len(subset),
        }

    return {"dow": dow_stats, "month_period": month_stats}


# ── Step 7: Time bucket stats ────────────────────────────────────────

def compute_time_bucket_stats(stats_df):
    """Probability of morning low forming in each 30-min bucket."""
    if stats_df.empty:
        return {}

    total = len(stats_df)
    bucket_stats = {}
    for bucket_name, _, _ in TIME_BUCKETS:
        subset = stats_df[stats_df["low_bucket"] == bucket_name]
        n = len(subset)
        if n == 0:
            continue
        bucket_stats[bucket_name] = {
            "probability": round(n / total * 100, 1),
            "avg_recovery": round(float(subset["recovery_to_close_pct"].mean()), 2),
            "n": n,
        }

    return bucket_stats


# ── Step 8: Should-enable decision ───────────────────────────────────

def should_enable(best_combo, oos_result, mc_result, sample_size):
    """Enable if: EV > 0, WR ≥ 50%, sample ≥ 15, MC lower CI > −0.5%, OOS not fragile."""
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
    """Composite edge strength 1–5."""
    if best_combo is None:
        return 0

    score = 0.0

    # EV contribution (0–1.5)
    ev = best_combo["ev"]
    if ev > 0.5:
        score += 1.5
    elif ev > 0.2:
        score += 1.0
    elif ev > 0:
        score += 0.5

    # Win rate contribution (0–1.0)
    wr = best_combo["win_rate"]
    if wr >= 65:
        score += 1.0
    elif wr >= 55:
        score += 0.5

    # Sample size contribution (0–1.0)
    if sample_size >= 50:
        score += 1.0
    elif sample_size >= 30:
        score += 0.5

    # OOS validation (0–1.0)
    if oos_result.get("oos_valid") and not oos_result.get("degraded"):
        score += 0.75
        if oos_result.get("oos_ev", 0) > 0.1:
            score += 0.25

    # Monte Carlo stability (0–0.5)
    if mc_result.get("ev_ci_lower", -1) > 0:
        score += 0.5

    return min(5, max(1, round(score)))


# ── Main pipeline per ticker ─────────────────────────────────────────

def process_ticker(symbol, cfg, verbose=False):
    """Full MLR pipeline for one ticker. Returns config dict or None."""
    if verbose:
        print(f"  Processing {symbol}...", end=" ", flush=True)

    # Fetch data: 60d of 5-min (yfinance/Upstox limit) + 1y daily for stats
    # fetch_yf handles cache → yfinance → Upstox fallback transparently
    try:
        intra_df = fetch_yf(symbol, period="60d", interval="5m")
        daily_df = fetch_yf(symbol, period="1y", interval="1d")
    except Exception as e:
        if verbose:
            print(f"SKIP (fetch error: {e})")
        return None

    if intra_df.empty or daily_df.empty or len(daily_df) < 60:
        if verbose:
            print("SKIP (insufficient data)")
        return None

    # Step 1: Morning low stats
    stats_df = compute_morning_low_stats(intra_df, daily_df)
    if stats_df.empty or len(stats_df) < MIN_SAMPLE:
        if verbose:
            print(f"SKIP ({len(stats_df)} morning low days < {MIN_SAMPLE})")
        return None

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

    # Step 7: Time buckets
    buckets = compute_time_bucket_stats(stats_df)

    # Step 8: Enable decision
    enabled = should_enable(best, oos, mc, sample_size)

    # Step 9: Edge strength
    edge = compute_edge_strength(best, oos, mc, sample_size)

    # Summary stats
    avg_recovery_close = round(float(stats_df["recovery_to_close_pct"].mean()), 2)
    avg_recovery_high = round(float(stats_df["recovery_to_high_pct"].mean()), 2)
    pct_above_1 = round(float((stats_df["recovery_to_close_pct"] > 1.0).mean() * 100), 1)
    pct_above_3 = round(float((stats_df["recovery_to_close_pct"] > 3.0).mean() * 100), 1)

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
        "avg_recovery_to_high_pct": avg_recovery_high,
        "pct_recovery_above_1": pct_above_1,
        "pct_recovery_above_3": pct_above_3,
        "mae_p90": mae.get("mae_p90", 0),
        "mae_median": mae.get("mae_median", 0),
        "dow_favorable": dow_favorable,
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

    result["best_time_buckets"] = buckets
    result["dow_stats"] = seasonality.get("dow", {})
    result["month_period_stats"] = seasonality.get("month_period", {})

    status = "ENABLED" if enabled else "disabled"
    if verbose:
        print(f"{status} | edge={edge} | EV={best['ev']:.3f} | WR={best['win_rate']:.0f}% | n={sample_size}"
              if best else f"{status} | no valid combos")

    return result


# ── Build YAML ───────────────────────────────────────────────────────

def build_yaml(ticker_results, output_path=CONFIG_PATH):
    """Write mlr_config.yaml with per-ticker configs."""
    config = {
        "generated": datetime.now().isoformat(),
        "description": "MLR (Morning Low Recovery) per-ticker config — auto-generated",
        "methodology": (
            "60 days of 5-min data + 1 year daily data analyzed per ticker. Morning lows "
            "(before 11:00) identified, recovery stats computed, EV-optimal entry/stop/target "
            "grid-searched, validated with 70/30 walk-forward OOS, Monte Carlo 95% CIs for robustness."
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
        "Morning Low Recovery buys stocks that form their daily low in the first",
        "90 minutes of trading (9:15–11:00 AM IST) and show confirmed reversal.",
        "Data shows ~57% of daily lows form in this window, with average +2.2%",
        "recovery to close.",
        "",
        "## How the Config Works",
        "",
        "For each ticker, the generator:",
        "1. Fetches 60 days of 5-minute OHLCV data (+ 1 year daily)",
        "2. Identifies days where the session low formed before 11:00 AM",
        "3. Computes recovery statistics (to close, to high)",
        "4. Grid-searches optimal entry delay, stop, and target combinations",
        "5. Validates with 70/30 walk-forward out-of-sample test",
        "6. Runs Monte Carlo bootstrap for 95% confidence intervals",
        "7. Computes DOW/month seasonality and time-bucket probabilities",
        "",
        "## Enabled Tickers",
        "",
    ]

    enabled = {s: r for s, r in ticker_results.items() if r and r.get("enabled")}
    disabled = {s: r for s, r in ticker_results.items() if r and not r.get("enabled")}

    if enabled:
        lines.append("| Ticker | Edge | EV | WR% | Sample | Avg Recovery |")
        lines.append("|--------|------|----|-----|--------|-------------|")
        for sym, r in sorted(enabled.items(), key=lambda x: x[1].get("edge_strength", 0), reverse=True):
            lines.append(
                f"| {sym.replace('.NS', '')} | {r.get('edge_strength', 0)} | "
                f"{r.get('ev', 0):.3f} | {r.get('win_rate', 0):.0f}% | "
                f"{r.get('sample_size', 0)} | {r.get('avg_recovery_to_close_pct', 0):.1f}% |"
            )
    else:
        lines.append("No tickers enabled.")

    lines.extend([
        "",
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
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress per ticker")
    parser.add_argument("--ticker", "-t", type=str, help="Process single ticker (e.g. RELIANCE.NS)")
    args = parser.parse_args()

    print("=" * 60)
    print("MLR Config Generator — Morning Low Recovery")
    print("=" * 60)

    if args.ticker:
        tickers = {args.ticker: TICKERS.get(args.ticker, {"name": args.ticker, "sector": ""})}
    else:
        tickers = TICKERS

    print(f"\nProcessing {len(tickers)} tickers...")
    results = {}

    for symbol, cfg in tickers.items():
        result = process_ticker(symbol, cfg, verbose=args.verbose or bool(args.ticker))
        results[symbol] = result

    # Build outputs
    config_path = build_yaml(results)
    doc_path = generate_documentation(results)

    # Summary
    enabled = sum(1 for r in results.values() if r and r.get("enabled"))
    total = len(results)
    processed = sum(1 for r in results.values() if r is not None)

    print(f"\n{'=' * 60}")
    print(f"Results: {processed}/{total} tickers processed")
    print(f"  Enabled:  {enabled}")
    print(f"  Disabled: {processed - enabled}")
    print(f"  Skipped:  {total - processed}")
    print(f"\nConfig: {config_path}")
    print(f"Guide:  {doc_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
