#!/usr/bin/env python3
"""
MLR Compute/Stats Functions

Extracted from mlr_config.py — pure computation functions for Morning Low
Recovery analysis: per-day stats, EV grid search, OOS validation, MAE,
Monte Carlo CIs, DOW/month seasonality, open-type profiles.

Used by mlr_config.py pipeline.
"""

from datetime import time as dtime

import numpy as np
import pandas as pd

# ── Tunable Constants (shared with mlr_config.py via re-export) ──────

MONTE_CARLO_ITERS = 10000    # bootstrap iterations for CIs
OOS_TRAIN_RATIO = 0.70       # walk-forward: train on first 70%
ROUND_TRIP_COST_PCT = 0.10   # brokerage + STT + slippage
MORNING_CUTOFF_HOUR = 11     # default session low cutoff (overridden by per-ticker config)
MORNING_CUTOFF_MIN = 30
# Ignore opening noise: lows/highs before this time are opening volatility, not signal
SETTLE_TIME = dtime(10, 0)

# Granular intraday phase windows — discover when each stock forms lows/highs
# Starts at 10:00 (post-settling) — the first 45 minutes are noise
PHASE_WINDOWS = [
    ("10:00-10:30", dtime(10, 0), dtime(10, 30)),    # post-settle window 1
    ("10:30-11:00", dtime(10, 30), dtime(11, 0)),     # late morning
    ("11:00-11:30", dtime(11, 0), dtime(11, 30)),     # pre-lunch
    ("11:30-12:00", dtime(11, 30), dtime(12, 0)),     # early lunch
    ("12:00-12:30", dtime(12, 0), dtime(12, 30)),     # lunch
    ("12:30-13:00", dtime(12, 30), dtime(13, 0)),     # post-lunch
    ("13:00-13:30", dtime(13, 0), dtime(13, 30)),     # early afternoon
    ("13:30-14:00", dtime(13, 30), dtime(14, 0)),     # mid-afternoon
    ("14:00-14:30", dtime(14, 0), dtime(14, 30)),     # pre-close setup
    ("14:30-15:15", dtime(14, 30), dtime(15, 15)),    # closing scalp
]

# Opening type thresholds for gap classification (5 types)
GAP_UP_LARGE = 1.0          # >= +1.0% = large gap up
GAP_UP_SMALL = 0.3          # +0.3% to +1.0% = small gap up
GAP_DOWN_SMALL = -0.3       # -0.3% to -1.0% = small gap down
GAP_DOWN_LARGE = -1.0       # <= -1.0% = large gap down
# Profiles below this predictability or sample size are excluded
MIN_PROFILE_PREDICTABILITY = 0.4
MIN_PROFILE_SAMPLE = 3

DOW_NAMES = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday"}


# ── Phase / open-type classification helpers ─────────────────────────

def _classify_phase(t):
    """Map a time to its phase window name."""
    for name, start, end in PHASE_WINDOWS:
        if start <= t < end:
            return name
    return "other"


def _classify_open_type(gap_pct):
    """Classify opening type from gap percentage (5 types)."""
    if gap_pct >= GAP_UP_LARGE:
        return "gap_up_large"
    if gap_pct >= GAP_UP_SMALL:
        return "gap_up_small"
    if gap_pct <= GAP_DOWN_LARGE:
        return "gap_down_large"
    if gap_pct <= GAP_DOWN_SMALL:
        return "gap_down_small"
    return "flat"


# ── Step 1: Compute morning low stats per trading day ────────────────

def compute_morning_low_stats(intra_df, daily_df, low_cutoff_hour=None,
                               low_cutoff_min=None):
    """Per trading day: morning window low + afternoon high, recovery metrics.

    Key design: opening 45 minutes (9:15-10:00) are noise — every stock makes
    extreme moves there. We find the low within the MORNING WINDOW (10:00 to
    cutoff) specifically, and the high from the REST OF THE SESSION (cutoff to
    close). Every day has such a low/high — the config decides which stocks
    show meaningful dip-and-recover patterns.

    Also classifies each day's opening type (gap_up/gap_down/flat) for
    correlation analysis: "when this stock gaps down, where does the
    post-settle low typically form?"

    Args:
        low_cutoff_hour/min: end of morning low search window. Defaults to
            module constants. Pass 15/15 to search all post-settle bars.

    Returns DataFrame with columns:
        date, low_time, low_phase, high_time, high_phase,
        open_type, low_price, high_price, close_price, open_price,
        recovery_to_close_pct, recovery_to_high_pct, time_to_recovery_bars,
        gap_pct, prev_close, dow, month,
        drop_from_open_pct, high_from_open_pct, low_before_high,
        recovered_past_open, recovery_by_phase,
        day_atr, day_atr_pct, drop_norm, high_norm, recovery_norm,
        range_norm, low_to_high_bars
    """
    if intra_df.empty or daily_df.empty:
        return pd.DataFrame()

    cutoff_h = low_cutoff_hour if low_cutoff_hour is not None else MORNING_CUTOFF_HOUR
    cutoff_m = low_cutoff_min if low_cutoff_min is not None else MORNING_CUTOFF_MIN
    cutoff_time = dtime(cutoff_h, cutoff_m)

    # Pre-compute rolling 14-day ATR series from daily data for normalization
    d_high = daily_df["High"]
    d_low = daily_df["Low"]
    d_prev_close = daily_df["Close"].shift(1)
    d_tr = pd.concat([d_high - d_low, (d_high - d_prev_close).abs(),
                       (d_low - d_prev_close).abs()], axis=1).max(axis=1)
    atr_series = d_tr.rolling(14, min_periods=7).mean()

    records = []
    dates = sorted(intra_df.index.date)
    unique_dates = list(dict.fromkeys(dates))

    for i, d in enumerate(unique_dates):
        day_bars = intra_df[intra_df.index.date == d]
        if len(day_bars) < 10:
            continue

        day_open = float(day_bars["Open"].iloc[0])
        day_close = float(day_bars["Close"].iloc[-1])

        # Morning window: 10:00 to cutoff — find the low HERE
        morning_window = day_bars[
            (day_bars.index.time >= SETTLE_TIME)
            & (day_bars.index.time <= cutoff_time)
        ]
        if len(morning_window) < 2:
            continue

        # Low within the morning window specifically
        low_idx = morning_window["Low"].idxmin()
        low_price = float(morning_window.loc[low_idx, "Low"])
        low_time = low_idx

        # High from the entire post-settle session (10:00 to close)
        post_settle = day_bars[day_bars.index.time >= SETTLE_TIME]
        high_idx = post_settle["High"].idxmax()
        high_price = float(post_settle.loc[high_idx, "High"])
        high_time = high_idx

        # Previous close
        prev_close = None
        if i > 0:
            prev_date = unique_dates[i - 1]
            prev_bars = intra_df[intra_df.index.date == prev_date]
            if not prev_bars.empty:
                prev_close = float(prev_bars["Close"].iloc[-1])

        if prev_close is None or prev_close <= 0:
            daily_before = daily_df[daily_df.index.date < d]
            if daily_before.empty:
                continue
            prev_close = float(daily_before["Close"].iloc[-1])

        if prev_close <= 0 or low_price <= 0:
            continue

        gap_pct = (day_open - prev_close) / prev_close * 100
        open_type = _classify_open_type(gap_pct)
        recovery_to_close = (day_close - low_price) / low_price * 100
        recovery_to_high = (high_price - low_price) / low_price * 100

        # Time to recovery: bars from morning low until price recovers to open
        low_bar_pos_in_post = post_settle.index.get_loc(low_idx)
        bars_after_low = post_settle.iloc[low_bar_pos_in_post:]
        time_to_recovery = len(bars_after_low)
        for j in range(1, len(bars_after_low)):
            if float(bars_after_low["Close"].iloc[j]) >= day_open:
                time_to_recovery = j
                break

        # Phase window classification for both low and high
        low_phase = _classify_phase(low_time.time())
        high_phase = _classify_phase(high_time.time())

        # Richer stats — drop depth, high above open, sequencing, completion
        drop_from_open_pct = (day_open - low_price) / day_open * 100 if day_open > 0 else 0
        high_from_open_pct = (high_price - day_open) / day_open * 100 if day_open > 0 else 0
        low_before_high = low_time < high_time
        recovered_past_open = day_close >= day_open

        # Bars between low and high — the tradeable window duration
        low_bar_pos_in_day = post_settle.index.get_loc(low_idx)
        high_bar_pos_in_day = post_settle.index.get_loc(high_idx)
        low_to_high_bars = max(0, high_bar_pos_in_day - low_bar_pos_in_day)

        # ATR normalization: look up the 14-day ATR as of this date
        daily_before_d = atr_series[atr_series.index.date <= d]
        day_atr = float(daily_before_d.iloc[-1]) if not daily_before_d.empty and not np.isnan(daily_before_d.iloc[-1]) else 0
        day_atr_pct = day_atr / day_open * 100 if day_open > 0 and day_atr > 0 else 0
        # Normalized metrics: dip/high/recovery as multiples of ATR
        drop_norm = round(drop_from_open_pct / day_atr_pct, 3) if day_atr_pct > 0 else 0
        high_norm = round(high_from_open_pct / day_atr_pct, 3) if day_atr_pct > 0 else 0
        recovery_norm = round(recovery_to_close / day_atr_pct, 3) if day_atr_pct > 0 else 0
        range_norm = round((high_price - low_price) / day_atr, 3) if day_atr > 0 else 0

        # Recovery-by-phase: which phase window did price first recover to open?
        recovery_by_phase = None
        if recovered_past_open:
            bars_after_low_recov = post_settle.iloc[low_bar_pos_in_post:]
            for k in range(1, len(bars_after_low_recov)):
                if float(bars_after_low_recov["Close"].iloc[k]) >= day_open:
                    recov_time = bars_after_low_recov.index[k].time()
                    recovery_by_phase = _classify_phase(recov_time)
                    break

        records.append({
            "date": d,
            "low_time": low_time,
            "low_phase": low_phase,
            "high_time": high_time,
            "high_phase": high_phase,
            "open_type": open_type,
            "low_price": low_price,
            "high_price": high_price,
            "close_price": day_close,
            "open_price": day_open,
            "recovery_to_close_pct": round(recovery_to_close, 3),
            "recovery_to_high_pct": round(recovery_to_high, 3),
            "time_to_recovery_bars": time_to_recovery,
            "gap_pct": round(gap_pct, 3),
            "prev_close": prev_close,
            "dow": d.weekday(),
            "month": d.month,
            "drop_from_open_pct": round(drop_from_open_pct, 3),
            "high_from_open_pct": round(high_from_open_pct, 3),
            "low_before_high": low_before_high,
            "recovered_past_open": recovered_past_open,
            "recovery_by_phase": recovery_by_phase,
            # ATR-normalized fields
            "day_atr": round(day_atr, 4),
            "day_atr_pct": round(day_atr_pct, 3),
            "drop_norm": drop_norm,
            "high_norm": high_norm,
            "recovery_norm": recovery_norm,
            "range_norm": range_norm,
            "low_to_high_bars": low_to_high_bars,
        })

    return pd.DataFrame(records)


# ── Step 2: EV-optimized entry delay / target / stop combos ─────────

def compute_ev_combos(stats_df):
    """Grid search: entry delay x stop x target for best EV.

    Entry delay: 2/3/5 bars after low
    Stop: 0.2/0.3/0.5% below low
    Target: 1.0/1.5/2.0/2.5/3.0%

    Simulation: for each historical day, the entry price is estimated as
    low_price x (1 + recovery proportion). MFE (max favorable excursion)
    is recovery_to_high minus entry recovery. MAE is checked against the
    stop. Target hit is checked before stop using MFE ordering.

    EV = (WR x target) - ((1-WR) x stop) - cost

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
                pnls = []

                for _, row in stats_df.iterrows():
                    # Entry is after 'delay' bars of recovery.
                    # Proportional estimate: delay bars out of time_to_recovery
                    # gives us a fraction of total recovery at entry.
                    ttr = max(1, row["time_to_recovery_bars"])
                    recovery_fraction = min(1.0, delay / ttr)
                    entry_recovery_pct = recovery_fraction * row["recovery_to_close_pct"]

                    # MFE from entry = max upside remaining after entry
                    mfe = row["recovery_to_high_pct"] - entry_recovery_pct
                    # Close-to-entry PnL (exit at close if neither target nor stop hit)
                    close_vs_entry = row["recovery_to_close_pct"] - entry_recovery_pct

                    # Simulate trade outcome
                    if mfe >= target_pct:
                        pnls.append(target_pct)          # target hit
                    elif close_vs_entry < -stop_pct:
                        pnls.append(-stop_pct)            # stopped out
                    else:
                        pnls.append(close_vs_entry)       # exit at close — actual PnL

                n = len(pnls)
                if n < 5:
                    continue

                wins = sum(1 for p in pnls if p > 0)
                wr = wins / n * 100
                ev = sum(pnls) / n - ROUND_TRIP_COST_PCT

                combos.append({
                    "entry_delay": delay,
                    "stop_pct": stop_pct,
                    "target_pct": target_pct,
                    "wins": wins,
                    "losses": n - wins,
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

    pnls = []
    for _, row in oos_df.iterrows():
        # Same proportional entry model as compute_ev_combos
        ttr = max(1, row["time_to_recovery_bars"])
        recovery_fraction = min(1.0, entry_delay / ttr)
        entry_recovery = recovery_fraction * row["recovery_to_close_pct"]

        mfe = row["recovery_to_high_pct"] - entry_recovery
        close_vs_entry = row["recovery_to_close_pct"] - entry_recovery

        if mfe >= target_pct:
            pnls.append(target_pct)
        elif close_vs_entry < -stop_pct:
            pnls.append(-stop_pct)
        else:
            pnls.append(close_vs_entry)

    if not pnls:
        return {"oos_valid": False, "degraded": True}

    total = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    oos_wr = wins / total * 100
    oos_ev = sum(pnls) / total - ROUND_TRIP_COST_PCT

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
    """p90 max adverse excursion from entry -> optimal stop.

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

    # Build per-trade PnL series — same model as compute_ev_combos
    pnls = []
    for _, row in stats_df.iterrows():
        ttr = max(1, row["time_to_recovery_bars"])
        recovery_fraction = min(1.0, entry_delay / ttr)
        entry_recovery = recovery_fraction * row["recovery_to_close_pct"]

        mfe = row["recovery_to_high_pct"] - entry_recovery
        close_vs_entry = row["recovery_to_close_pct"] - entry_recovery

        if mfe >= target_pct:
            pnls.append(target_pct - ROUND_TRIP_COST_PCT)
        elif close_vs_entry < -stop_pct:
            pnls.append(-stop_pct - ROUND_TRIP_COST_PCT)
        else:
            pnls.append(close_vs_entry - ROUND_TRIP_COST_PCT)

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


# ── Step 7a: Normalized phase heatmap ────────────────────────────────

def compute_phase_heatmap(stats_df):
    """ATR-normalized low/high heatmap across all 10 phase windows.

    For each phase, computes:
      Low side:  pct_is_session_low, avg_drop_norm, median_drop_norm, low_score
      High side: pct_is_session_high, avg_high_norm, median_high_norm, high_score

    Plus derived summary: best_low_phase, best_high_phase,
    avg_low_to_high_bars, avg_trade_window_mins.

    Returns dict with 'phases' (per-phase data), 'best_low_phase',
    'best_high_phase', 'avg_low_to_high_bars', 'avg_trade_window_mins'.
    """
    if stats_df.empty:
        return {}

    n_days = len(stats_df)
    has_norm = "drop_norm" in stats_df.columns and stats_df["drop_norm"].sum() > 0

    phases = {}
    for phase_name, _, _ in PHASE_WINDOWS:
        low_in_phase = stats_df[stats_df["low_phase"] == phase_name]
        high_in_phase = stats_df[stats_df["high_phase"] == phase_name]

        n_low = len(low_in_phase)
        n_high = len(high_in_phase)
        pct_low = round(n_low / n_days * 100, 1) if n_days > 0 else 0
        pct_high = round(n_high / n_days * 100, 1) if n_days > 0 else 0

        # Normalized low stats (in ATR units)
        if has_norm and n_low > 0:
            avg_drop_norm = round(float(low_in_phase["drop_norm"].mean()), 3)
            median_drop_norm = round(float(low_in_phase["drop_norm"].median()), 3)
        else:
            avg_drop_norm = 0.0
            median_drop_norm = 0.0

        # Normalized high stats (in ATR units)
        if has_norm and n_high > 0:
            avg_high_norm = round(float(high_in_phase["high_norm"].mean()), 3)
            median_high_norm = round(float(high_in_phase["high_norm"].median()), 3)
        else:
            avg_high_norm = 0.0
            median_high_norm = 0.0

        # Composite scores: probability × magnitude
        # A good low phase has high pct_low AND deep normalized dip
        low_score = round(pct_low / 100 * avg_drop_norm, 3)
        high_score = round(pct_high / 100 * avg_high_norm, 3)

        phases[phase_name] = {
            "pct_is_session_low": pct_low,
            "avg_drop_norm": avg_drop_norm,
            "median_drop_norm": median_drop_norm,
            "low_score": low_score,
            "pct_is_session_high": pct_high,
            "avg_high_norm": avg_high_norm,
            "median_high_norm": median_high_norm,
            "high_score": high_score,
        }

    # Best phases by composite score
    best_low = max(phases.items(), key=lambda x: x[1]["low_score"])[0] if phases else None
    best_high = max(phases.items(), key=lambda x: x[1]["high_score"])[0] if phases else None

    # Trade window: bars between low and high
    if "low_to_high_bars" in stats_df.columns:
        valid = stats_df[stats_df["low_before_high"] == True]
        if not valid.empty:
            avg_l2h = round(float(valid["low_to_high_bars"].mean()), 1)
            median_l2h = round(float(valid["low_to_high_bars"].median()), 1)
        else:
            avg_l2h = 0
            median_l2h = 0
    else:
        avg_l2h = 0
        median_l2h = 0

    return {
        "phases": phases,
        "best_low_phase": best_low,
        "best_high_phase": best_high,
        "avg_low_to_high_bars": avg_l2h,
        "median_low_to_high_bars": median_l2h,
        "avg_trade_window_mins": round(avg_l2h * 5, 0),
    }


# ── Step 7b: Predictability-scored open-type profiles ────────────────

def _compute_predictability(concentration, cov, completion_rate, sequencing_rate):
    """Composite predictability score (0-1).

    - concentration (Herfindahl index of low-phase distribution): 0.3 weight
    - 1 - CoV (coefficient of variation of recovery): 0.3 weight
    - completion_rate (% days recovery > 0): 0.2 weight
    - sequencing_rate (% days low before high): 0.2 weight
    """
    cov_score = max(0, min(1, 1 - cov))
    return round(
        0.3 * concentration + 0.3 * cov_score + 0.2 * completion_rate + 0.2 * sequencing_rate,
        2,
    )


def compute_open_type_profiles(stats_df, full_session_df=None):
    """Predictability-scored profiles per opening type.

    For each of 5 open types, computes:
    - Overall stats: n, pct_of_days, predictability, low_before_high_pct,
      recovered_past_open_pct, avg_drop_from_open_pct, avg_high_from_open_pct,
      high_window
    - ATR-normalized metrics: avg_drop_norm, median_drop_norm,
      avg_high_norm, median_high_norm
    - Top 1-2 low windows with: probability, avg_drop_pct, drop_std,
      avg_recovery_pct, recovery_std, median_recovery_pct,
      recovered_past_open_pct, recovery_by, avg_drop_norm, n
    - Top 1-2 high windows with: probability, avg_high_pct, avg_high_norm, n
    - Trade window: avg_low_to_high_bars, avg_trade_window_mins,
      best_low_phase, best_high_phase

    Only includes open types with predictability >= MIN_PROFILE_PREDICTABILITY
    and n >= MIN_PROFILE_SAMPLE.

    Also returns low_cutoff_recommendation (phase that captures >=80% of lows)
    and overall_heatmap (phase heatmap across all open types).
    """
    if stats_df.empty:
        return {"profiles": {}, "low_cutoff_recommendation": "11:30"}

    # Use full-session data for cutoff recommendation
    cutoff_df = full_session_df if full_session_df is not None and not full_session_df.empty else stats_df
    total = len(cutoff_df)

    # ── Cutoff recommendation from full-session low distribution ──
    low_phase_counts = cutoff_df["low_phase"].value_counts()
    cumulative = 0
    recommended_cutoff = "11:30"
    for phase_name, _, end in PHASE_WINDOWS:
        if phase_name in low_phase_counts.index:
            cumulative += low_phase_counts[phase_name] / total * 100
        if cumulative >= 80:
            recommended_cutoff = f"{end.hour:02d}:{end.minute:02d}"
            break

    # ── Build profiles per open type ──
    open_types = ["gap_down_large", "gap_down_small", "flat", "gap_up_small", "gap_up_large"]
    profiles = {}

    for otype in open_types:
        subset = stats_df[stats_df["open_type"] == otype]
        n = len(subset)
        if n < MIN_PROFILE_SAMPLE:
            continue

        # Overall stats for this open type
        low_before_high_pct = round(float(subset["low_before_high"].mean() * 100), 1)
        recovered_past_open_pct = round(float(subset["recovered_past_open"].mean() * 100), 1)
        avg_drop = round(float(subset["drop_from_open_pct"].mean()), 2)
        avg_high = round(float(subset["high_from_open_pct"].mean()), 2)

        # High-phase distribution
        high_counts = subset["high_phase"].value_counts()
        high_window = high_counts.index[0] if not high_counts.empty else None

        # ATR-normalized aggregate metrics for this open type
        has_norm = "drop_norm" in subset.columns and subset["drop_norm"].sum() > 0
        if has_norm:
            avg_drop_norm = round(float(subset["drop_norm"].mean()), 3)
            median_drop_norm = round(float(subset["drop_norm"].median()), 3)
            avg_high_norm_val = round(float(subset["high_norm"].mean()), 3)
            median_high_norm_val = round(float(subset["high_norm"].median()), 3)
        else:
            avg_drop_norm = median_drop_norm = avg_high_norm_val = median_high_norm_val = 0.0

        # Trade window: bars between low and high
        if "low_to_high_bars" in subset.columns:
            valid_l2h = subset[subset["low_before_high"] == True]
            avg_l2h = round(float(valid_l2h["low_to_high_bars"].mean()), 1) if not valid_l2h.empty else 0
        else:
            avg_l2h = 0

        # Per-open-type phase heatmap for best_low/best_high phase
        otype_heatmap = compute_phase_heatmap(subset) if len(subset) >= MIN_PROFILE_SAMPLE else {}

        # ── Low-phase distribution -> Herfindahl for concentration ──
        low_counts = subset["low_phase"].value_counts()
        proportions = low_counts / n
        herfindahl = float((proportions ** 2).sum())  # 1.0 = all in one window

        # ── Recovery consistency (CoV) within top window ──
        top_phase = low_counts.index[0] if not low_counts.empty else None
        top_subset = subset[subset["low_phase"] == top_phase] if top_phase else pd.DataFrame()

        if not top_subset.empty and len(top_subset) >= 2:
            rec_mean = float(top_subset["recovery_to_close_pct"].mean())
            rec_std = float(top_subset["recovery_to_close_pct"].std())
            cov = rec_std / abs(rec_mean) if abs(rec_mean) > 0.01 else 1.0
        else:
            cov = 1.0

        # ── Completion rate: % of days with positive recovery ──
        completion = float((subset["recovery_to_close_pct"] > 0).mean())

        # ── Sequencing: % of days low forms before high ──
        sequencing = float(subset["low_before_high"].mean())

        predictability = _compute_predictability(herfindahl, cov, completion, sequencing)

        if predictability < MIN_PROFILE_PREDICTABILITY:
            continue

        profile = {
            "n": n,
            "pct_of_days": round(n / len(stats_df) * 100, 1),
            "predictability": predictability,
            "low_before_high_pct": low_before_high_pct,
            "recovered_past_open_pct": recovered_past_open_pct,
            "avg_drop_from_open_pct": avg_drop,
            "avg_high_from_open_pct": avg_high,
            "high_window": high_window,
            # ATR-normalized metrics
            "avg_drop_norm": avg_drop_norm,
            "median_drop_norm": median_drop_norm,
            "avg_high_norm": avg_high_norm_val,
            "median_high_norm": median_high_norm_val,
            # Trade window
            "avg_low_to_high_bars": avg_l2h,
            "avg_trade_window_mins": round(avg_l2h * 5, 0),
            "best_low_phase": otype_heatmap.get("best_low_phase"),
            "best_high_phase": otype_heatmap.get("best_high_phase"),
        }

        # ── Top low windows (up to 2) ──
        for rank, (phase_name, phase_count) in enumerate(low_counts.items()):
            if rank >= 2:
                break
            if phase_count < 2:
                continue

            phase_subset = subset[subset["low_phase"] == phase_name]
            p_n = len(phase_subset)
            prob = round(p_n / n * 100, 1)

            p_drop_vals = phase_subset["drop_from_open_pct"]
            p_rec_vals = phase_subset["recovery_to_close_pct"]
            p_rec_open = phase_subset["recovered_past_open"]

            # Normalized drop for this specific phase window
            p_drop_norm = round(float(phase_subset["drop_norm"].mean()), 3) if has_norm and p_n > 0 else 0.0

            # Median recovery-by phase for this window
            recov_phases = phase_subset["recovery_by_phase"].dropna()
            recovery_by = None
            if not recov_phases.empty:
                recovery_by = recov_phases.mode().iloc[0]

            profile[f"low_{rank + 1}"] = {
                "window": phase_name,
                "probability": prob,
                "avg_drop_pct": round(float(p_drop_vals.mean()), 2),
                "drop_std": round(float(p_drop_vals.std()), 2) if p_n >= 2 else 0.0,
                "avg_drop_norm": p_drop_norm,
                "avg_recovery_pct": round(float(p_rec_vals.mean()), 2),
                "recovery_std": round(float(p_rec_vals.std()), 2) if p_n >= 2 else 0.0,
                "median_recovery_pct": round(float(p_rec_vals.median()), 2),
                "recovered_past_open_pct": round(float(p_rec_open.mean() * 100), 1),
                "recovery_by": recovery_by,
                "n": p_n,
            }

        # ── Top high windows (up to 2) — parallel to low windows ──
        for rank, (phase_name, phase_count) in enumerate(high_counts.items()):
            if rank >= 2:
                break
            if phase_count < 2:
                continue

            h_subset = subset[subset["high_phase"] == phase_name]
            h_n = len(h_subset)
            h_prob = round(h_n / n * 100, 1)
            h_avg_pct = round(float(h_subset["high_from_open_pct"].mean()), 2)
            h_avg_norm = round(float(h_subset["high_norm"].mean()), 3) if has_norm and h_n > 0 else 0.0

            profile[f"high_{rank + 1}"] = {
                "window": phase_name,
                "probability": h_prob,
                "avg_high_pct": h_avg_pct,
                "avg_high_norm": h_avg_norm,
                "n": h_n,
            }

        profiles[otype] = profile

    # Overall heatmap across all open types
    overall_heatmap = compute_phase_heatmap(stats_df) if not stats_df.empty else {}

    return {
        "profiles": profiles,
        "low_cutoff_recommendation": recommended_cutoff,
        "heatmap": overall_heatmap,
    }


# ── Numpy type sanitizer ─────────────────────────────────────────────

def _sanitize(obj):
    """Recursively convert numpy types to native Python for YAML serialization."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj
