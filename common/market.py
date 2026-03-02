"""
Market-level utilities: VIX, Nifty regime, earnings, pattern detection.
"""

import numpy as np
import pandas as pd
import yfinance as yf

from common.data import fetch_yf
from common.indicators import compute_atr


def fetch_india_vix():
    """Fetch India VIX value and classify regime.

    Returns (vix_value, vix_regime) tuple.
    Regimes: low_vol (<14), normal (14-18), elevated (18-22), stress (>22).
    """
    from common.analysis_cache import get_cached, set_cached, TTL_MARKET
    cached = get_cached("vix", max_age_seconds=TTL_MARKET)
    if cached:
        return cached["vix_val"], cached["vix_regime"]

    try:
        vix_df = fetch_yf("^INDIAVIX", period="5d", interval="1d")
        if vix_df.empty:
            return None, "unknown"
        vix_val = float(vix_df["Close"].iloc[-1])
        if vix_val < 14:
            regime = "low_vol"
        elif vix_val < 18:
            regime = "normal"
        elif vix_val < 22:
            regime = "elevated"
        else:
            regime = "stress"
        set_cached("vix", {"vix_val": round(vix_val, 2), "vix_regime": regime})
        return round(vix_val, 2), regime
    except Exception:
        return None, "unknown"


def vix_position_scale(vix_val):
    """Scale position size based on VIX regime.

    Returns multiplier: 1.2 (low_vol), 1.0 (normal), 0.7 (elevated), 0.0 (stress).
    """
    if vix_val is None:
        return 1.0
    if vix_val < 14:
        return 1.2
    elif vix_val < 18:
        return 1.0
    elif vix_val < 22:
        return 0.7
    else:
        return 0.0


def detect_nifty_regime(nifty_daily):
    """Detect Nifty market regime: bullish / bearish / range-bound.

    Uses 20-day SMA crossover and recent ATR for classification.
    Returns: regime string and a beta_scale factor (0.5-1.0).
    """
    from common.analysis_cache import get_cached, set_cached, TTL_MARKET
    cached = get_cached("nifty_regime", max_age_seconds=TTL_MARKET)
    if cached:
        return cached["regime"], cached["beta_scale"]

    if nifty_daily.empty or len(nifty_daily) < 20:
        return "unknown", 1.0

    close = nifty_daily["Close"]
    sma20 = close.rolling(20).mean().iloc[-1]
    current = close.iloc[-1]

    atr = compute_atr(nifty_daily) if len(nifty_daily) >= 14 else np.nan
    atr_pct = atr / current * 100 if not np.isnan(atr) and current > 0 else 1.0

    ret_5d = (current / close.iloc[-6] - 1) * 100 if len(close) >= 6 else 0

    if current > sma20 and ret_5d > 0:
        regime, beta_scale = "bullish", 1.0
    elif current < sma20 and ret_5d < -1:
        regime, beta_scale = "bearish", 0.5
    else:
        regime, beta_scale = "range", 0.75

    set_cached("nifty_regime", {"regime": regime, "beta_scale": beta_scale})
    return regime, beta_scale


def check_earnings_proximity(symbol, days_ahead=3):
    """Check if symbol has earnings within N days.

    Returns (is_near_earnings, earnings_date_str) tuple.
    """
    from common.analysis_cache import get_cached, set_cached, TTL_EARNINGS
    cached = get_cached("earnings_proximity", symbol=symbol, max_age_seconds=TTL_EARNINGS)
    if cached is not None:
        return cached["is_near"], cached["date_str"]

    try:
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None or (isinstance(cal, pd.DataFrame) and cal.empty):
            return False, None
        if isinstance(cal, dict):
            earnings_date = cal.get("Earnings Date")
            if isinstance(earnings_date, list) and earnings_date:
                earnings_date = earnings_date[0]
        elif isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.columns:
                earnings_date = cal["Earnings Date"].iloc[0]
            elif "Earnings Date" in cal.index:
                earnings_date = cal.loc["Earnings Date"].iloc[0]
            else:
                return False, None
        else:
            return False, None

        if earnings_date is None:
            return False, None

        if isinstance(earnings_date, str):
            earnings_date = pd.Timestamp(earnings_date)

        now = pd.Timestamp.now(tz="Asia/Kolkata")
        if hasattr(earnings_date, 'tz') and earnings_date.tz is None:
            earnings_date = earnings_date.tz_localize("Asia/Kolkata")
        elif not hasattr(earnings_date, 'tz'):
            earnings_date = pd.Timestamp(earnings_date).tz_localize("Asia/Kolkata")

        days_until = (earnings_date - now).days
        if 0 <= days_until <= days_ahead:
            date_str = earnings_date.strftime("%Y-%m-%d")
            set_cached("earnings_proximity", {"is_near": True, "date_str": date_str}, symbol=symbol)
            return True, date_str
        set_cached("earnings_proximity", {"is_near": False, "date_str": None}, symbol=symbol)
        return False, None
    except Exception:
        return False, None


def nifty_making_new_lows(nifty_ist):
    """Check if Nifty making fresh intraday lows in last 3 bars."""
    if len(nifty_ist) < 6:
        return False
    today = nifty_ist.index[-1].date()
    today_bars = nifty_ist[nifty_ist.index.date == today]
    if len(today_bars) < 6:
        return False
    day_low = today_bars["Low"].min()
    recent_low = today_bars["Low"].iloc[-3:].min()
    return recent_low <= day_low


def higher_lows_pattern(intra_ist):
    """Higher lows in recent bars."""
    today = intra_ist.index[-1].date()
    today_bars = intra_ist[intra_ist.index.date == today]
    if len(today_bars) < 9:
        return False
    recent = today_bars["Low"].iloc[-3:].min()
    prior = today_bars["Low"].iloc[-6:-3].min()
    return recent > prior


def estimate_institutional_flow(nifty_bees_df=None):
    """Estimate institutional (FII/DII) flow direction using Nifty BeES ETF proxy.

    Logic: volume spike in Nifty BeES + price direction = proxy for FII buying/selling.
    Returns "net_buying" | "neutral" | "net_selling".
    """
    from common.analysis_cache import get_cached, set_cached, TTL_FLOW
    cached = get_cached("institutional_flow", max_age_seconds=TTL_FLOW)
    if cached is not None:
        return cached["flow"]

    if nifty_bees_df is None:
        try:
            nifty_bees_df = fetch_yf("0P0000XVSO.BO", period="5d", interval="1d")
        except Exception:
            return "neutral"

    if nifty_bees_df.empty or len(nifty_bees_df) < 3:
        return "neutral"

    # Today vs 5-day median volume
    today_vol = float(nifty_bees_df["Volume"].iloc[-1])
    median_vol = float(nifty_bees_df["Volume"].iloc[:-1].median())

    if median_vol == 0:
        return "neutral"

    vol_ratio = today_vol / median_vol

    # Price direction (today's return)
    today_ret = (
        float(nifty_bees_df["Close"].iloc[-1]) / float(nifty_bees_df["Close"].iloc[-2]) - 1
    ) * 100

    # Volume spike (> 1.3x median) + direction = institutional flow signal
    if vol_ratio > 1.3 and today_ret > 0.2:
        flow = "net_buying"
    elif vol_ratio > 1.3 and today_ret < -0.2:
        flow = "net_selling"
    else:
        flow = "neutral"

    set_cached("institutional_flow", {"flow": flow})
    return flow


def outperforming_nifty(stock_ist, nifty_ist):
    """Stock intraday return > Nifty intraday return."""
    def intraday_ret(df):
        today = df.index[-1].date()
        bars = df[df.index.date == today]
        if len(bars) < 2:
            return 0.0
        return (bars["Close"].iloc[-1] / bars["Open"].iloc[0] - 1) * 100
    return intraday_ret(stock_ist) > intraday_ret(nifty_ist)
