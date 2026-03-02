"""Shared utilities for intraday strategy modules."""

from common.risk import NSE_ROUND_TRIP_COST_PCT


def _build_result(strategy, direction, entry_price, stop_price, target_price,
                  confidence, conditions, reason, **extra):
    """Build standardised candidate trade dict.

    Deducts NSE round-trip transaction cost from target_pct and recalculates RR.
    """
    stop_pct = abs(entry_price - stop_price) / entry_price * 100 if entry_price > 0 else 0
    raw_target_pct = abs(target_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

    # Deduct transaction cost for realistic RR
    effective_target_pct = max(0, raw_target_pct - NSE_ROUND_TRIP_COST_PCT)
    rr = effective_target_pct / stop_pct if stop_pct > 0 else 0

    result = {
        "strategy": strategy,
        "direction": direction,
        "entry_price": round(entry_price, 2),
        "stop_price": round(stop_price, 2),
        "target_price": round(target_price, 2),
        "stop_pct": round(stop_pct, 2),
        "target_pct": round(effective_target_pct, 2),
        "rr_ratio": round(rr, 2),
        "confidence": round(confidence, 2),
        "conditions": conditions,
        "reason": reason,
    }
    result.update(extra)
    return result
