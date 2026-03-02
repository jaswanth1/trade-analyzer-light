"""
Technical indicators for intraday strategies.

New indicators not in common/indicators.py: EMA, RSI, MACD, Bollinger,
Keltner, squeeze detection, opening range, pivot levels, volume ratio.
"""

import numpy as np
import pandas as pd

from common.indicators import compute_atr, _to_ist
from common.data import IST_WINDOWS


def compute_ema(series, span):
    """EMA with given span. Returns full series."""
    return series.ewm(span=span, adjust=False).mean()


def compute_rsi(series, period=14):
    """RSI on price series. Returns series."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_macd(series, fast=12, slow=26, signal=9):
    """MACD line, signal line, histogram. Returns dict of series."""
    ema_fast = compute_ema(series, fast)
    ema_slow = compute_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def compute_bollinger(series, period=20, std_dev=2):
    """Upper, lower, mid bands + bandwidth. Returns dict of series."""
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    bandwidth = (upper - lower) / mid.replace(0, np.nan)
    return {"upper": upper, "lower": lower, "mid": mid, "bandwidth": bandwidth}


def compute_keltner(df, ema_period=20, atr_period=14, multiplier=1.5):
    """Keltner channels using ATR. Returns dict with upper/lower/mid."""
    mid = compute_ema(df["Close"], ema_period)

    # ATR series (not scalar) for intraday use
    h = df["High"]
    l = df["Low"]
    c = df["Close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    atr_series = tr.rolling(atr_period).mean()

    upper = mid + multiplier * atr_series
    lower = mid - multiplier * atr_series
    return {"upper": upper, "lower": lower, "mid": mid, "atr_series": atr_series}


def compute_squeeze(bollinger, keltner):
    """Bollinger inside Keltner = squeeze. Returns bool series."""
    return (bollinger["lower"] > keltner["lower"]) & (bollinger["upper"] < keltner["upper"])


def compute_ema_slope(ema_series, lookback=5):
    """Slope of EMA over last N bars — angle proxy. Returns float."""
    if len(ema_series) < lookback:
        return 0.0
    recent = ema_series.iloc[-lookback:]
    if recent.iloc[0] == 0:
        return 0.0
    return float((recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0] * 100)


def compute_opening_range(intra_ist, minutes=30):
    """OR high/low/mid from first N minutes of today. Returns dict."""
    if intra_ist.empty:
        return {}
    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]
    if today_bars.empty:
        return {}

    market_open = today_bars.index[0]
    cutoff = market_open + pd.Timedelta(minutes=minutes)
    or_bars = today_bars[today_bars.index <= cutoff]
    if or_bars.empty:
        return {}

    or_high = float(or_bars["High"].max())
    or_low = float(or_bars["Low"].min())
    or_mid = (or_high + or_low) / 2
    or_range = or_high - or_low

    return {
        "or_high": or_high,
        "or_low": or_low,
        "or_mid": or_mid,
        "or_range": or_range,
        "or_range_pct": or_range / or_low * 100 if or_low > 0 else 0,
        "n_bars": len(or_bars),
    }


def compute_intraday_levels(daily_df):
    """Previous day H/L/C, classic pivot points, 52-week H/L. Returns dict."""
    if daily_df.empty or len(daily_df) < 2:
        return {}

    prev = daily_df.iloc[-2]
    ph, pl, pc = float(prev["High"]), float(prev["Low"]), float(prev["Close"])
    pivot = (ph + pl + pc) / 3
    r1 = 2 * pivot - pl
    s1 = 2 * pivot - ph
    r2 = pivot + (ph - pl)
    s2 = pivot - (ph - pl)

    # 52-week high/low
    lookback = min(252, len(daily_df))
    recent = daily_df.iloc[-lookback:]
    w52_high = float(recent["High"].max())
    w52_low = float(recent["Low"].min())

    return {
        "prev_high": ph,
        "prev_low": pl,
        "prev_close": pc,
        "pivot": round(pivot, 2),
        "r1": round(r1, 2),
        "r2": round(r2, 2),
        "s1": round(s1, 2),
        "s2": round(s2, 2),
        "w52_high": w52_high,
        "w52_low": w52_low,
    }


def compute_volume_ratio(intra_ist, lookback_days=20):
    """Current bar volume vs. same-time-window median over N days. Returns series."""
    if intra_ist.empty:
        return pd.Series(dtype=float)

    df = intra_ist.copy()
    df["time_slot"] = df.index.strftime("%H:%M")
    df["date"] = df.index.date

    # Median volume per time slot over lookback
    dates = sorted(df["date"].unique())
    if len(dates) <= 1:
        return pd.Series(1.0, index=df.index)

    lookback_dates = dates[:-1][-lookback_days:]
    hist = df[df["date"].isin(lookback_dates)]
    median_vol = hist.groupby("time_slot")["Volume"].median()

    today = dates[-1]
    today_bars = df[df["date"] == today].copy()
    today_bars["vol_ratio"] = today_bars.apply(
        lambda row: row["Volume"] / median_vol.get(row["time_slot"], np.nan)
        if median_vol.get(row["time_slot"], 0) > 0 else 1.0,
        axis=1,
    )
    # Reindex to full df
    result = pd.Series(np.nan, index=df.index)
    result.loc[today_bars.index] = today_bars["vol_ratio"].values
    return result


def compute_cumulative_return_from_open(intra_ist):
    """Cumulative % return since market open for each bar. Returns series."""
    if intra_ist.empty:
        return pd.Series(dtype=float)

    df = intra_ist.copy()
    df["date"] = df.index.date
    result = pd.Series(np.nan, index=df.index)
    for d, group in df.groupby("date"):
        day_open = group["Open"].iloc[0]
        if day_open > 0:
            cum_ret = (group["Close"] - day_open) / day_open * 100
            result.loc[group.index] = cum_ret.values
    return result


# ── New Institutional-Grade Features ─────────────────────────────────────

def compute_vwap_bands(intra_ist):
    """VWAP ± standard deviation bands for mean-revert entries.

    Adds columns: vwap_upper_1sd, vwap_lower_1sd, vwap_upper_2sd, vwap_lower_2sd
    Returns DataFrame with new columns added.
    """
    if intra_ist.empty or "vwap" not in intra_ist.columns:
        return intra_ist

    df = intra_ist.copy()
    df["date"] = df.index.date

    for col in ("vwap_upper_1sd", "vwap_lower_1sd", "vwap_upper_2sd", "vwap_lower_2sd"):
        df[col] = np.nan

    typical = (df["High"] + df["Low"] + df["Close"]) / 3

    for d, idx in df.groupby("date").groups.items():
        day_mask = df.index.isin(idx)
        day_typical = typical.loc[day_mask]
        day_vwap = df.loc[day_mask, "vwap"]

        # Rolling variance of (typical - vwap) within the session
        deviation = day_typical - day_vwap
        cum_var = deviation.expanding().var().fillna(0)
        cum_std = np.sqrt(cum_var)

        df.loc[day_mask, "vwap_upper_1sd"] = day_vwap + cum_std
        df.loc[day_mask, "vwap_lower_1sd"] = day_vwap - cum_std
        df.loc[day_mask, "vwap_upper_2sd"] = day_vwap + 2 * cum_std
        df.loc[day_mask, "vwap_lower_2sd"] = day_vwap - 2 * cum_std

    return df


def compute_cumulative_rvol(intra_ist, lookback_days=20):
    """Cumulative Relative Volume — institutional participation signal.

    Cumulative volume from open to current bar vs 20-day average at the
    same bar index. Returns series.
    """
    if intra_ist.empty:
        return pd.Series(dtype=float)

    df = intra_ist.copy()
    df["date"] = df.index.date
    dates = sorted(df["date"].unique())

    if len(dates) <= 1:
        return pd.Series(1.0, index=df.index)

    today = dates[-1]
    hist_dates = dates[:-1][-lookback_days:]

    # Build cumulative volume per bar index for historical days
    hist_cum_vols = []
    for d in hist_dates:
        day_data = df[df["date"] == d]
        cum = day_data["Volume"].cumsum().values
        hist_cum_vols.append(cum)

    # Average cumulative volume per bar position
    max_len = max(len(v) for v in hist_cum_vols) if hist_cum_vols else 0
    avg_cum = np.zeros(max_len)
    counts = np.zeros(max_len)
    for cv in hist_cum_vols:
        for i, v in enumerate(cv):
            avg_cum[i] += v
            counts[i] += 1
    avg_cum = np.divide(avg_cum, counts, where=counts > 0, out=np.ones_like(avg_cum))

    # Today's cumulative volume
    today_data = df[df["date"] == today]
    today_cum = today_data["Volume"].cumsum().values

    result = pd.Series(np.nan, index=df.index)
    rvol_vals = []
    for i in range(len(today_cum)):
        if i < len(avg_cum) and avg_cum[i] > 0:
            rvol_vals.append(today_cum[i] / avg_cum[i])
        else:
            rvol_vals.append(1.0)
    result.loc[today_data.index] = rvol_vals

    return result


def compute_candle_imbalance(df):
    """Candle Imbalance — order flow approximation (OFI proxy).

    (close - open) / (high - low), normalized to [-1, 1].
    Positive = buyer dominance, negative = seller dominance.
    Returns series.
    """
    if df.empty:
        return pd.Series(dtype=float)

    candle_range = df["High"] - df["Low"]
    body = df["Close"] - df["Open"]

    # Avoid division by zero for doji candles
    imbalance = body / candle_range.replace(0, np.nan)
    return imbalance.clip(-1, 1).fillna(0)


def compute_session_low_info(intra_ist):
    """Session low analysis for Morning Low Recovery strategy.

    Extracts today's bars, finds the session low, computes recovery metrics.

    Returns dict with:
        low_price, low_time, low_bar_idx, bars_since_low,
        recovery_pct, low_in_morning (before 11:00 AM).
    Returns empty dict if insufficient data.
    """
    if intra_ist.empty:
        return {}

    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]
    if len(today_bars) < 3:
        return {}

    # Find session low
    low_idx = today_bars["Low"].idxmin()
    low_price = float(today_bars.loc[low_idx, "Low"])
    low_time = low_idx
    low_bar_pos = today_bars.index.get_loc(low_idx)
    bars_since_low = len(today_bars) - 1 - low_bar_pos

    # Recovery from low to current close
    current_close = float(today_bars["Close"].iloc[-1])
    recovery_pct = (current_close - low_price) / low_price * 100 if low_price > 0 else 0.0

    # Morning window: low occurred before 11:00 AM
    low_in_morning = low_time.hour < 11 or (low_time.hour == 11 and low_time.minute == 0)

    return {
        "low_price": low_price,
        "low_time": low_time,
        "low_bar_idx": low_bar_pos,
        "bars_since_low": bars_since_low,
        "recovery_pct": recovery_pct,
        "low_in_morning": low_in_morning,
    }
