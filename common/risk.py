"""
Position sizing, correlation-based risk management, portfolio heat tracking,
and transaction cost modelling.
"""

import numpy as np
import pandas as pd

MAX_RISK_PER_TRADE_PCT = 0.5
CORR_CLUSTER_THRESHOLD = 0.6
CORR_LOOKBACK_DAYS = 20

# Round-trip cost for MIS intraday on NSE (brokerage + STT + exchange fees)
NSE_ROUND_TRIP_COST_PCT = 0.08

# Net direction cap — max positions in same direction
MAX_SAME_DIRECTION = 4


def effective_cost(entry_price, avg_daily_volume=None):
    """Slippage-adjusted transaction cost.

    Low-volume stocks get higher slippage penalty, naturally filtering
    marginal setups.

    Returns total round-trip cost as percentage.
    """
    base_cost = 0.08  # brokerage + STT + exchange fees
    if avg_daily_volume is not None and avg_daily_volume > 0:
        slippage = 0.03 if avg_daily_volume > 1_000_000 else 0.06
    else:
        slippage = 0.05  # conservative default
    return base_cost + slippage


def compute_position_size(capital, kelly_fraction, entry_price, stop_pct,
                          vix_scale=1.0, beta_scale=1.0):
    """Compute position size using Kelly criterion with VIX and beta adjustments.

    Returns dict with quantity, capital_allocated, capital_at_risk, risk_pct.
    """
    if entry_price <= 0 or stop_pct <= 0 or kelly_fraction <= 0:
        return {"quantity": 0, "capital_allocated": 0, "capital_at_risk": 0, "risk_pct": 0}

    effective_kelly = kelly_fraction * vix_scale * beta_scale

    max_risk = capital * MAX_RISK_PER_TRADE_PCT / 100
    risk_per_share = entry_price * stop_pct / 100

    kelly_allocation = capital * effective_kelly
    kelly_qty = int(kelly_allocation / entry_price) if entry_price > 0 else 0

    risk_qty = int(max_risk / risk_per_share) if risk_per_share > 0 else 0

    quantity = min(kelly_qty, risk_qty)
    quantity = max(0, quantity)

    capital_allocated = quantity * entry_price
    capital_at_risk = quantity * risk_per_share
    risk_pct = capital_at_risk / capital * 100 if capital > 0 else 0

    return {
        "quantity": quantity,
        "capital_allocated": round(capital_allocated, 2),
        "capital_at_risk": round(capital_at_risk, 2),
        "risk_pct": round(risk_pct, 3),
        "effective_kelly": round(effective_kelly, 4),
    }


def compute_correlation_clusters(daily_data_dict, threshold=CORR_CLUSTER_THRESHOLD,
                                 lookback=CORR_LOOKBACK_DAYS):
    """Build correlation clusters from daily return data.

    Returns dict of {cluster_id: [symbols]}.
    """
    from common.analysis_cache import get_cached, set_cached, TTL_DAILY
    cached = get_cached("correlation_clusters", max_age_seconds=TTL_DAILY)
    if cached is not None:
        return cached

    returns = {}
    for sym, df in daily_data_dict.items():
        if df.empty or len(df) < lookback:
            continue
        ret = df["Close"].tail(lookback).pct_change().dropna()
        if len(ret) >= lookback - 2:
            returns[sym] = ret

    if len(returns) < 2:
        return {}

    ret_df = pd.DataFrame(returns)
    ret_df = ret_df.dropna(axis=1, thresh=int(len(ret_df) * 0.7))

    if ret_df.shape[1] < 2:
        return {}

    corr_matrix = ret_df.corr()

    assigned = set()
    clusters = {}
    cluster_id = 0

    symbols = list(corr_matrix.columns)
    for sym in symbols:
        if sym in assigned:
            continue
        cluster = [sym]
        assigned.add(sym)
        for other in symbols:
            if other in assigned:
                continue
            if abs(corr_matrix.loc[sym, other]) >= threshold:
                cluster.append(other)
                assigned.add(other)
        clusters[cluster_id] = cluster
        cluster_id += 1

    set_cached("correlation_clusters", clusters)
    return clusters


def compute_portfolio_heat(active_positions):
    """Compute total portfolio heat (risk) from open positions.

    Returns dict with:
        total_risk_pct: sum of all position risk as % of capital
        by_sector: {sector: risk_pct}
        by_direction: {"long": risk_pct, "short": risk_pct}
    """
    if not active_positions:
        return {"total_risk_pct": 0, "by_sector": {}, "by_direction": {"long": 0, "short": 0}}

    total_risk = 0
    sector_risk = {}
    direction_risk = {"long": 0, "short": 0}

    for pos in active_positions:
        risk = pos.get("capital_at_risk", 0)
        total_risk += risk

        sector = pos.get("sector", "unknown")
        sector_risk[sector] = sector_risk.get(sector, 0) + risk

        d = pos.get("direction", "long")
        direction_risk[d] = direction_risk.get(d, 0) + risk

    return {
        "total_risk_pct": total_risk,
        "by_sector": sector_risk,
        "by_direction": direction_risk,
    }


def compute_individual_beta_scale(beta):
    """Per-stock beta scaling for position sizing.

    High-beta stocks get smaller positions automatically.
    beta=2.0 → scale 0.5, beta=1.0 → scale 1.0, beta=0.5 → scale 1.0
    """
    if beta is None or np.isnan(beta):
        return 1.0
    return min(1.0, 1.0 / max(beta, 0.5))
