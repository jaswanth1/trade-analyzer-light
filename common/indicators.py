"""
Technical indicator computations shared across scanners and reports.
"""

import numpy as np
import pandas as pd

from common.data import GAP_THRESHOLDS, IST_WINDOWS, TARGET_PCTS, STOP_PCTS


def compute_atr(daily_df, period=14):
    """Standard ATR from True Range rolling mean."""
    h = daily_df["High"]
    l = daily_df["Low"]
    c = daily_df["Close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]


def compute_beta(daily_df, bench_df):
    """Cov/Var regression beta on daily returns."""
    ret_s = daily_df["Close"].pct_change().dropna()
    ret_b = bench_df["Close"].pct_change().dropna()
    aligned = pd.concat([ret_s, ret_b], axis=1, join="inner").dropna()
    if len(aligned) < 20:
        return np.nan
    aligned.columns = ["stock", "bench"]
    cov = aligned["stock"].cov(aligned["bench"])
    var = aligned["bench"].var()
    return cov / var if var != 0 else np.nan


def compute_relative_performance(daily_df, bench_df, sector_df, n_days=63):
    """3-month cumulative returns: stock, benchmark, sector, alpha."""
    def cum_ret(df, n):
        if len(df) < n:
            n = len(df)
        return (df["Close"].iloc[-1] / df["Close"].iloc[-n] - 1) * 100

    stock_ret = cum_ret(daily_df, n_days)
    bench_ret = cum_ret(bench_df, n_days)
    sector_ret = cum_ret(sector_df, n_days) if not sector_df.empty else np.nan
    return {
        "stock_return": stock_ret,
        "bench_return": bench_ret,
        "sector_return": sector_ret,
        "alpha_vs_bench": stock_ret - bench_ret,
        "alpha_vs_sector": stock_ret - sector_ret if not np.isnan(sector_ret) else np.nan,
    }


def classify_gaps(daily_df):
    """Classify each day's gap: flat / small_up / small_down / large_up / large_down."""
    df = daily_df.copy()
    df["prev_close"] = df["Close"].shift(1)
    df = df.dropna(subset=["prev_close"])
    df["gap_pct"] = (df["Open"] - df["prev_close"]) / df["prev_close"]
    df["gap_abs"] = df["gap_pct"].abs()

    def _classify(row):
        if row["gap_abs"] <= GAP_THRESHOLDS["flat"]:
            return "flat"
        elif row["gap_abs"] <= GAP_THRESHOLDS["small"]:
            return "small_up" if row["gap_pct"] > 0 else "small_down"
        else:
            return "large_up" if row["gap_pct"] > 0 else "large_down"

    df["gap_type"] = df.apply(_classify, axis=1)
    df["day_of_week"] = df.index.dayofweek
    df["day_name"] = df.index.day_name()
    df["open_to_close_pct"] = (df["Close"] - df["Open"]) / df["Open"] * 100
    df["day_range_pct"] = (df["High"] - df["Low"]) / df["Open"] * 100
    df["open_to_close_dir"] = df["open_to_close_pct"].apply(lambda x: "up" if x > 0 else "down")
    return df


def _to_ist(intraday_df):
    """Convert index to IST timezone."""
    df = intraday_df.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("Asia/Kolkata")
    return df


def compute_vwap(intraday_df):
    """Compute daily VWAP reset per day."""
    df = intraday_df.copy()
    df["typical"] = (df["High"] + df["Low"] + df["Close"]) / 3
    df["tp_vol"] = df["typical"] * df["Volume"]
    df["date"] = df.index.date
    df["cum_tp_vol"] = df.groupby("date")["tp_vol"].cumsum()
    df["cum_vol"] = df.groupby("date")["Volume"].cumsum()
    df["vwap"] = df["cum_tp_vol"] / df["cum_vol"].replace(0, np.nan)
    return df


def _assign_window(t):
    """Map a time to an IST window label or None."""
    for start, end in IST_WINDOWS:
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        s = sh * 60 + sm
        e = eh * 60 + em
        cur = t.hour * 60 + t.minute
        if s <= cur < e:
            return f"{start}-{end}"
    return None


def compute_time_window_stats(intraday_df):
    """Bucket 5-min bars into IST windows, compute per-window stats."""
    df = _to_ist(intraday_df)
    df = compute_vwap(df)
    df["window"] = df.index.map(lambda x: _assign_window(x.time()))
    df = df.dropna(subset=["window"])
    df["bar_return"] = (df["Close"] - df["Open"]) / df["Open"] * 100
    df["date"] = df.index.date
    df["above_vwap"] = df["Close"] > df["vwap"]

    day_win = df.groupby(["date", "window"]).agg(
        move=("bar_return", "sum"),
        volume=("Volume", "sum"),
        bars_above_vwap=("above_vwap", "sum"),
        bar_count=("bar_return", "count"),
    ).reset_index()

    total_vol_per_day = df.groupby("date")["Volume"].sum().rename("day_vol")
    day_win = day_win.merge(total_vol_per_day, on="date")
    day_win["vol_share"] = day_win["volume"] / day_win["day_vol"].replace(0, np.nan) * 100
    day_win["vwap_interaction_pct"] = day_win["bars_above_vwap"] / day_win["bar_count"].replace(0, np.nan) * 100
    day_win["win"] = (day_win["move"] > 0).astype(int)

    stats = day_win.groupby("window").agg(
        avg_move_pct=("move", "mean"),
        win_rate=("win", "mean"),
        avg_vol_share=("vol_share", "mean"),
        avg_vwap_interaction=("vwap_interaction_pct", "mean"),
        day_count=("move", "count"),
    ).reset_index()
    stats["win_rate"] = stats["win_rate"] * 100

    window_order = [f"{s}-{e}" for s, e in IST_WINDOWS]
    stats["window"] = pd.Categorical(stats["window"], categories=window_order, ordered=True)
    stats = stats.sort_values("window").reset_index(drop=True)
    return stats


def compute_volume_profile(intraday_df):
    """20-day rolling median volume per time slot, tag expansion/contraction."""
    df = _to_ist(intraday_df)
    df["window"] = df.index.map(lambda x: _assign_window(x.time()))
    df = df.dropna(subset=["window"])
    df["date"] = df.index.date

    day_win_vol = df.groupby(["date", "window"])["Volume"].sum().reset_index()

    medians = {}
    for win in day_win_vol["window"].unique():
        subset = day_win_vol[day_win_vol["window"] == win].sort_values("date")
        subset["median_20d"] = subset["Volume"].rolling(20, min_periods=10).median()
        medians[win] = subset

    result = pd.concat(medians.values()).sort_values(["date", "window"])
    result["vol_ratio"] = result["Volume"] / result["median_20d"].replace(0, np.nan)
    result["vol_tag"] = result["vol_ratio"].apply(
        lambda x: "expansion" if x >= 1.5 else ("contraction" if x <= 0.5 else "normal")
        if not np.isnan(x) else "N/A"
    )
    return result


def compute_probability_matrix(intraday_df, gap_df):
    """First-touch probability matrix: for each day scan 5-min bars from open."""
    df = _to_ist(intraday_df)
    df["date"] = df.index.date

    gap_lookup = {}
    for idx, row in gap_df.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        gap_lookup[d] = row["gap_type"]

    records = []
    for date, day_bars in df.groupby("date"):
        if len(day_bars) < 2:
            continue
        day_open = day_bars["Open"].iloc[0]
        if day_open == 0:
            continue
        gap_type = gap_lookup.get(date, "unknown")

        for tgt_pct in TARGET_PCTS:
            for stp_pct in STOP_PCTS:
                target_price = day_open * (1 + tgt_pct / 100)
                stop_price = day_open * (1 - stp_pct / 100)
                result = "none"
                bars_to_hit = np.nan
                mae = 0.0

                for i, (ts, bar) in enumerate(day_bars.iterrows()):
                    low_diff = (bar["Low"] - day_open) / day_open * 100
                    mae = min(mae, low_diff)

                    hit_target = bar["High"] >= target_price
                    hit_stop = bar["Low"] <= stop_price

                    if hit_target and hit_stop:
                        dist_to_target = target_price - bar["Open"]
                        dist_to_stop = bar["Open"] - stop_price
                        if dist_to_stop <= dist_to_target:
                            result = "stop"
                        else:
                            result = "target"
                        bars_to_hit = i
                        break
                    elif hit_target:
                        result = "target"
                        bars_to_hit = i
                        break
                    elif hit_stop:
                        result = "stop"
                        bars_to_hit = i
                        break

                records.append({
                    "date": date,
                    "gap_type": gap_type,
                    "target_pct": tgt_pct,
                    "stop_pct": stp_pct,
                    "result": result,
                    "bars_to_hit": bars_to_hit,
                    "mae_pct": mae,
                })

    return pd.DataFrame(records)


def compute_atr_thresholds(atr, price):
    """Map ATR fractions to % and absolute thresholds."""
    if np.isnan(atr) or price == 0:
        return {}
    return {
        "micro (0.25x ATR)": {"abs": round(0.25 * atr, 2), "pct": round(0.25 * atr / price * 100, 2)},
        "tactical (0.5x ATR)": {"abs": round(0.5 * atr, 2), "pct": round(0.5 * atr / price * 100, 2)},
        "structural (1.0x ATR)": {"abs": round(atr, 2), "pct": round(atr / price * 100, 2)},
        "exceptional (1.5x ATR)": {"abs": round(1.5 * atr, 2), "pct": round(1.5 * atr / price * 100, 2)},
    }
