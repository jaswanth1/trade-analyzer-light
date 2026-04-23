"""
DEPRECATED — This module is no longer part of the scalp pipeline.

Report computations have been merged into scalp/config.py, which now
fetches OHLCV via fetch_yf(), computes indicators, and caches results
in analysis_cache (Supabase). No CSV files or intermediate reports needed.

New pipeline: python -m scalp.config  (fetch → compute → cache → YAML)

This file is kept for reference only.
"""

import logging
import os
import warnings

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

from common.data import (
    BENCHMARK, GAP_THRESHOLDS, TARGET_PCTS, STOP_PCTS, SCALP_OUTPUT_DIR,
    fetch_yf, fetch_ticker_info, load_universe_for_tier,
)

TICKERS = load_universe_for_tier("scalp")

OUTPUT_DIR = SCALP_OUTPUT_DIR
from common.indicators import (
    compute_atr, compute_beta, compute_relative_performance, classify_gaps,
    _to_ist, compute_vwap, compute_time_window_stats, compute_volume_profile,
    compute_probability_matrix, compute_atr_thresholds,
)
from common.display import fmt

warnings.filterwarnings("ignore")


def generate_report(symbol, cfg, daily_df, intraday_df, bench_daily, sector_daily, info):
    """Generate the full 8-section Markdown report and CSVs."""
    ticker_dir = os.path.join(str(OUTPUT_DIR), symbol)
    os.makedirs(ticker_dir, exist_ok=True)

    # Save raw CSVs
    daily_df.to_csv(os.path.join(ticker_dir, "daily_ohlcv.csv"))
    intraday_ist = _to_ist(intraday_df)
    intraday_ist.to_csv(os.path.join(ticker_dir, "intraday_5min.csv"))

    # Core computations
    last_price = daily_df["Close"].iloc[-1]
    atr = compute_atr(daily_df)
    beta = compute_beta(daily_df, bench_daily)
    rel_perf = compute_relative_performance(daily_df, bench_daily, sector_daily)

    gap_df = classify_gaps(daily_df)
    gap_df.to_csv(os.path.join(ticker_dir, "gap_analysis.csv"))

    tw_stats = compute_time_window_stats(intraday_df)
    tw_stats.to_csv(os.path.join(ticker_dir, "time_window_stats.csv"), index=False)

    prob_matrix = compute_probability_matrix(intraday_df, gap_df)
    prob_matrix.to_csv(os.path.join(ticker_dir, "probability_matrix.csv"), index=False)

    vol_profile = compute_volume_profile(intraday_df)
    atr_thresh = compute_atr_thresholds(atr, last_price)

    avg_vol_20d = daily_df["Volume"].tail(20).mean()
    avg_turnover = avg_vol_20d * last_price

    market_cap = info.get("marketCap", np.nan)
    company_name = info.get("longName", cfg["name"])
    sector = info.get("sector", "N/A")

    lines = []

    # ── Section 1: Symbol Overview & Liquidity Profile ──
    lines.append(f"# {symbol} — {company_name}")
    lines.append(f"\n*Report generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} IST*\n")
    lines.append("## 1. Symbol Overview & Liquidity Profile\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Company | {company_name} |")
    lines.append(f"| Sector | {sector} |")
    lines.append(f"| Last Price | {fmt(last_price)} |")
    lines.append(f"| Market Cap | {fmt(market_cap / 1e7, 0) + ' Cr' if not np.isnan(market_cap) else 'N/A'} |")
    lines.append(f"| Avg Volume (20d) | {fmt(avg_vol_20d, 0)} |")
    lines.append(f"| Avg Turnover (20d) | ₹{fmt(avg_turnover / 1e7, 2)} Cr |")
    lines.append(f"| ATR(14) | {fmt(atr)} ({fmt(atr / last_price * 100 if last_price else np.nan)}%) |")
    lines.append(f"| Beta vs Nifty 50 | {fmt(beta)} |")
    lines.append(f"| 3M Stock Return | {fmt(rel_perf['stock_return'])}% |")
    lines.append(f"| 3M Nifty Return | {fmt(rel_perf['bench_return'])}% |")
    lines.append(f"| 3M Sector Return | {fmt(rel_perf['sector_return'])}% |")
    lines.append(f"| Alpha vs Benchmark | {fmt(rel_perf['alpha_vs_bench'])}% |")
    lines.append(f"| Alpha vs Sector | {fmt(rel_perf['alpha_vs_sector'])}% |")

    # ── Section 2: Historical Open-Type Behavior ──
    lines.append("\n## 2. Historical Open-Type Behavior\n")
    n_days = len(gap_df)
    lines.append(f"Analysis period: **{n_days} trading days**\n")
    lines.append("### Gap Classification Distribution\n")
    lines.append("| Gap Type | Count | % of Days | Avg Open→Close % | Up Days | Down Days |")
    lines.append("|----------|-------|-----------|-------------------|---------|-----------|")

    for gt in ["flat", "small_up", "small_down", "large_up", "large_down"]:
        subset = gap_df[gap_df["gap_type"] == gt]
        cnt = len(subset)
        pct_days = cnt / n_days * 100 if n_days else 0
        avg_otc = subset["open_to_close_pct"].mean()
        up = (subset["open_to_close_dir"] == "up").sum()
        down = (subset["open_to_close_dir"] == "down").sum()
        lines.append(f"| {gt} | {cnt} | {fmt(pct_days)}% | {fmt(avg_otc)}% | {up} | {down} |")

    lines.append("\n### Daily Range Stats\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Avg Day Range | {fmt(gap_df['day_range_pct'].mean())}% |")
    lines.append(f"| Median Day Range | {fmt(gap_df['day_range_pct'].median())}% |")
    lines.append(f"| Max Day Range | {fmt(gap_df['day_range_pct'].max())}% |")
    lines.append(f"| Avg Open→Close | {fmt(gap_df['open_to_close_pct'].mean())}% |")

    # ── Section 3: Time-of-Day Edge Table ──
    lines.append("\n## 3. Time-of-Day Edge Table\n")
    lines.append("| Window (IST) | Avg Move % | Win Rate % | Avg Vol Share % | VWAP Interaction % | Days |")
    lines.append("|--------------|-----------|------------|-----------------|-------------------|------|")
    for _, row in tw_stats.iterrows():
        lines.append(
            f"| {row['window']} | {fmt(row['avg_move_pct'])} | {fmt(row['win_rate'])} | "
            f"{fmt(row['avg_vol_share'])} | {fmt(row['avg_vwap_interaction'])} | {int(row['day_count'])} |"
        )

    # ── Section 4: Condition-Based Trade Triggers ──
    lines.append("\n## 4. Condition-Based Trade Triggers\n")
    lines.append("### ATR-Fraction Thresholds\n")
    lines.append("| Level | Absolute (₹) | Percentage |")
    lines.append("|-------|--------------|------------|")
    for level, vals in atr_thresh.items():
        lines.append(f"| {level} | ₹{vals['abs']} | {vals['pct']}% |")

    lines.append("\n### Gap Thresholds\n")
    lines.append(f"- **Flat open**: gap within ±{GAP_THRESHOLDS['flat']*100:.1f}%")
    lines.append(f"- **Small gap**: {GAP_THRESHOLDS['flat']*100:.1f}% – {GAP_THRESHOLDS['small']*100:.1f}%")
    lines.append(f"- **Large gap**: > {GAP_THRESHOLDS['small']*100:.1f}%")

    lines.append("\n### Volume Tags\n")
    vol_tags = vol_profile.dropna(subset=["vol_tag"])
    if not vol_tags.empty:
        tag_counts = vol_tags["vol_tag"].value_counts()
        total_tagged = tag_counts.sum()
        lines.append("| Tag | Count | % |")
        lines.append("|-----|-------|---|")
        for tag in ["expansion", "normal", "contraction", "N/A"]:
            cnt = tag_counts.get(tag, 0)
            lines.append(f"| {tag} | {cnt} | {fmt(cnt/total_tagged*100 if total_tagged else 0)}% |")
    lines.append("\n*Expansion = ≥1.5x 20-day window median, Contraction = ≤0.5x*")

    # ── Section 5: Probabilistic Outcome Modelling ──
    lines.append("\n## 5. Probabilistic Outcome Modelling\n")

    pm_decided = prob_matrix[prob_matrix["result"].isin(["target", "stop"])]
    pm_clean = prob_matrix[prob_matrix["result"] != "none"]

    for gap_type in ["all", "flat", "small_up", "small_down", "large_up", "large_down"]:
        if gap_type == "all":
            sub = pm_decided
            sub_full = pm_clean
            label = "All Days"
        else:
            sub = pm_decided[pm_decided["gap_type"] == gap_type]
            sub_full = pm_clean[pm_clean["gap_type"] == gap_type]
            label = f"Gap: {gap_type}"

        if sub.empty:
            continue

        lines.append(f"\n### {label}\n")
        lines.append("| Target % | Stop % | P(Target) | P(Stop) | Median Bars→Target | Avg MAE % | N |")
        lines.append("|----------|--------|-----------|---------|--------------------|-----------|---|")

        for tgt in TARGET_PCTS:
            for stp in STOP_PCTS:
                combo = sub[(sub["target_pct"] == tgt) & (sub["stop_pct"] == stp)]
                combo_full = sub_full[(sub_full["target_pct"] == tgt) & (sub_full["stop_pct"] == stp)]
                n_total = len(combo)
                if n_total == 0:
                    continue
                n_target = (combo["result"] == "target").sum()
                n_stop = (combo["result"] == "stop").sum()
                p_target = n_target / n_total * 100
                p_stop = n_stop / n_total * 100
                target_hits = combo[combo["result"] == "target"]
                med_bars = target_hits["bars_to_hit"].median() if not target_hits.empty else np.nan
                avg_mae = combo_full["mae_pct"].mean()
                lines.append(
                    f"| +{tgt}% | -{stp}% | {fmt(p_target)}% | {fmt(p_stop)}% | "
                    f"{fmt(med_bars, 0)} | {fmt(avg_mae)} | {n_total} |"
                )

    # ── Section 6: Sample Daily Playbook ──
    lines.append("\n## 6. Sample Daily Playbook\n")

    for scenario, gap_types in [("Gap-Up", ["small_up", "large_up"]),
                                 ("Flat Open", ["flat"]),
                                 ("Gap-Down", ["small_down", "large_down"])]:
        lines.append(f"\n### {scenario} Scenario\n")
        scenario_gaps = gap_df[gap_df["gap_type"].isin(gap_types)]
        n_sc = len(scenario_gaps)
        if n_sc == 0:
            lines.append("*Insufficient data for this scenario.*\n")
            continue

        avg_otc = scenario_gaps["open_to_close_pct"].mean()
        up_pct = (scenario_gaps["open_to_close_dir"] == "up").sum() / n_sc * 100
        avg_range = scenario_gaps["day_range_pct"].mean()

        lines.append(f"- **Occurrences**: {n_sc} days")
        lines.append(f"- **Avg Open→Close**: {fmt(avg_otc)}%")
        lines.append(f"- **Up-close probability**: {fmt(up_pct)}%")
        lines.append(f"- **Avg day range**: {fmt(avg_range)}%")

        sc_prob = pm_decided[pm_decided["gap_type"].isin(gap_types)]
        if not sc_prob.empty:
            best = sc_prob[sc_prob["target_pct"] == 1.0]
            if not best.empty:
                best_stop = best.groupby("stop_pct").apply(
                    lambda x: (x["result"] == "target").sum() / len(x) * 100
                ).reset_index()
                best_stop.columns = ["stop_pct", "p_target"]
                if not best_stop.empty:
                    top = best_stop.loc[best_stop["p_target"].idxmax()]
                    lines.append(f"- **Best +1% target**: {fmt(top['p_target'])}% hit rate with -{fmt(top['stop_pct'])}% stop")

    # ── Section 7: Risk & Failure Conditions ──
    lines.append("\n## 7. Risk & Failure Conditions\n")

    atr_val = atr if not np.isnan(atr) else 0
    range_anomaly_days = gap_df[gap_df["day_range_pct"] > 1.5 * (atr_val / last_price * 100)] if atr_val > 0 else pd.DataFrame()
    n_range_anomaly = len(range_anomaly_days)

    median_vol = daily_df["Volume"].median()
    vol_anomaly_days = daily_df[daily_df["Volume"] > 2 * median_vol]
    n_vol_anomaly = len(vol_anomaly_days)

    ret_s = daily_df["Close"].pct_change().dropna()
    if not sector_daily.empty:
        ret_sec = sector_daily["Close"].pct_change().dropna()
        aligned = pd.concat([ret_s, ret_sec], axis=1, join="inner").dropna()
        if len(aligned) > 10:
            sector_corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        else:
            sector_corr = np.nan
    else:
        sector_corr = np.nan

    gap_traps = gap_df[
        ((gap_df["gap_type"].isin(["small_up", "large_up"])) & (gap_df["open_to_close_dir"] == "down")) |
        ((gap_df["gap_type"].isin(["small_down", "large_down"])) & (gap_df["open_to_close_dir"] == "up"))
    ]
    n_gap_trap = len(gap_traps)
    gap_non_flat = gap_df[~gap_df["gap_type"].isin(["flat"])]
    trap_pct = n_gap_trap / len(gap_non_flat) * 100 if len(gap_non_flat) > 0 else 0

    lines.append("| Risk Metric | Value |")
    lines.append("|-------------|-------|")
    lines.append(f"| Range anomaly days (>1.5x ATR) | {n_range_anomaly} ({fmt(n_range_anomaly/n_days*100 if n_days else 0)}%) |")
    lines.append(f"| Volume anomaly days (>2x median) | {n_vol_anomaly} ({fmt(n_vol_anomaly/len(daily_df)*100 if len(daily_df) else 0)}%) |")
    lines.append(f"| Sector correlation | {fmt(sector_corr)} |")
    lines.append(f"| Gap-and-trap frequency | {n_gap_trap}/{len(gap_non_flat)} ({fmt(trap_pct)}%) |")

    # ── Section 8: Final Verdict ──
    lines.append("\n## 8. Final Verdict\n")

    scores = {}
    scores["liquidity"] = min(100, avg_vol_20d / 500_000 * 100) if not np.isnan(avg_vol_20d) else 0

    atr_pct = atr / last_price * 100 if last_price > 0 and not np.isnan(atr) else 0
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

    scores["trap_safety"] = max(0, 100 - trap_pct * 2)

    overall = np.mean(list(scores.values()))

    if not tw_stats.empty:
        best_window_row = tw_stats.loc[tw_stats["win_rate"].idxmax()]
        best_window = best_window_row["window"]
    else:
        best_window = "N/A"

    flat_gaps = gap_df[gap_df["gap_type"] == "flat"]
    gapup_days = gap_df[gap_df["gap_type"].isin(["small_up", "large_up"])]
    gapdown_days = gap_df[gap_df["gap_type"].isin(["small_down", "large_down"])]

    flat_wr = (flat_gaps["open_to_close_dir"] == "up").mean() * 100 if len(flat_gaps) > 0 else 0
    gapup_wr = (gapup_days["open_to_close_dir"] == "up").mean() * 100 if len(gapup_days) > 0 else 0
    gapdown_wr = (gapdown_days["open_to_close_dir"] == "up").mean() * 100 if len(gapdown_days) > 0 else 0

    setups = {"Flat open (bullish bias)": flat_wr, "Gap-up continuation": gapup_wr, "Gap-down reversal": gapdown_wr}
    preferred = max(setups, key=setups.get)

    lines.append("### Confidence Scores\n")
    lines.append("| Dimension | Score |")
    lines.append("|-----------|-------|")
    for dim, sc in scores.items():
        lines.append(f"| {dim.replace('_', ' ').title()} | {fmt(sc, 0)}/100 |")
    lines.append(f"| **Overall** | **{fmt(overall, 0)}/100** |")

    lines.append("\n### Summary\n")
    lines.append(f"- **Tradability**: {'Good' if overall >= 60 else 'Moderate' if overall >= 40 else 'Low'} ({fmt(overall, 0)}/100)")
    lines.append(f"- **Best time window**: {best_window} IST")
    lines.append(f"- **Preferred setup**: {preferred} ({fmt(setups[preferred], 0)}% up-close rate)")
    lines.append(f"- **ATR(14)**: ₹{fmt(atr)} ({fmt(atr_pct)}% of price)")
    lines.append(f"- **Beta**: {fmt(beta)} — {'high' if beta and not np.isnan(beta) and beta > 1.2 else 'moderate' if beta and not np.isnan(beta) and beta > 0.8 else 'low'} market sensitivity")

    report = "\n".join(lines) + "\n"
    report_path = os.path.join(ticker_dir, "report.md")
    with open(report_path, "w") as f:
        f.write(report)

    return report_path


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("Fetching benchmark data...")
    bench_daily = fetch_yf(BENCHMARK, period="6mo", interval="1d")
    if bench_daily.empty:
        log.error("Could not fetch benchmark data. Exiting.")
        return

    for symbol, cfg in TICKERS.items():
        log.info("=" * 60)
        log.info("Processing %s (%s)", symbol, cfg['name'])
        log.info("=" * 60)

        log.info("Fetching daily OHLCV (6mo)...")
        daily_df = fetch_yf(symbol, period="6mo", interval="1d")
        if daily_df.empty:
            log.warning("%s: SKIP — no daily data", symbol)
            continue

        log.info("Fetching 5-min OHLCV (60d)...")
        intraday_df = fetch_yf(symbol, period="60d", interval="5m")
        if intraday_df.empty:
            log.warning("%s: SKIP — no intraday data", symbol)
            continue

        log.info("Fetching sector index (%s)...", cfg['sector'])
        sector_daily = fetch_yf(cfg["sector"], period="6mo", interval="1d")

        log.info("Fetching ticker info...")
        info = fetch_ticker_info(symbol)

        log.info("Generating report...")
        report_path = generate_report(symbol, cfg, daily_df, intraday_df, bench_daily, sector_daily, info)
        log.info("Report saved: %s", report_path)

    log.info("Done. Output in ./%s/", OUTPUT_DIR)


if __name__ == "__main__":
    main()
