"""
IntraWeek composite scoring and signal ranking.

Weighted condition scoring (follows BTST/intraday patterns) with
convergence overlay, historical hit rate, and regime alignment.
"""

import numpy as np

from common.market import check_earnings_proximity


# ── Condition Weights ─────────────────────────────────────────────────────

INTRAWEEK_CONDITION_WEIGHTS = {
    # Core signal (highest weights)
    "downside_exhaustion":    3.5,
    "momentum_reversal":      3.0,
    "volume_expansion":       2.5,
    # Structural support
    "sector_strength":        2.5,
    "relative_strength":      2.0,
    "ema_alignment":          2.0,
    # Context modifiers
    "weekly_context":         1.5,
    "vwap_reclaim":           1.5,
    "atr_range_ok":           1.5,
    "not_overextended":       1.0,
}

INTRAWEEK_MUST_HAVE = [
    "atr_range_ok",
]

# ── Signal Tiers ──────────────────────────────────────────────────────────

SIGNAL_TIERS = {
    "STRONG":  {"min_score": 0.80, "min_upside": 12.0},
    "ACTIVE":  {"min_score": 0.65, "min_upside": 10.0},
    "WATCH":   {"min_score": 0.50, "min_upside": 8.0},
}


def compute_composite_score(candidate, convergence, hit_rate,
                             regime_score, market_ctx):
    """Compute composite score for an IntraWeek candidate.

    Blends:
    - 40% weighted condition score
    - 25% convergence (7-indicator alignment)
    - 20% historical hit rate
    - 15% regime alignment

    Returns dict with score, tier, risk_flags, etc.
    """
    conditions = candidate["conditions"]

    # ── Weighted condition score ──
    scoreable = {k: v for k, v in conditions.items() if k not in INTRAWEEK_MUST_HAVE}
    total_weight = sum(INTRAWEEK_CONDITION_WEIGHTS.get(k, 1.0) for k in scoreable)
    weighted_hit = sum(
        INTRAWEEK_CONDITION_WEIGHTS.get(k, 1.0)
        for k, v in scoreable.items() if v
    )
    base_score = weighted_hit / total_weight if total_weight > 0 else 0

    # ── Must-have gate ──
    must_have_pass = all(conditions.get(k, False) for k in INTRAWEEK_MUST_HAVE)

    # ── Earnings check ──
    near_earnings, earnings_date = check_earnings_proximity(
        candidate["symbol"], days_ahead=5
    )
    if near_earnings:
        return {
            "score": 0,
            "tier": "AVOID",
            "base_score": base_score,
            "conditions_met": [k for k, v in conditions.items() if v],
            "conditions_failed": [k for k, v in conditions.items() if not v],
            "risk_flags": [f"Earnings on {earnings_date}"],
            "expected_upside": (0, 0),
        }

    # ── Convergence normalization ──
    convergence_norm = convergence.get("score", 0) / 100

    # ── Historical hit rate normalization ──
    hr_10 = hit_rate.get("hit_rate_10pct", 0)
    hist_norm = hr_10 / 100 if hr_10 > 0 else 0.5  # neutral if no data

    # ── Composite blend ──
    composite = (
        0.40 * base_score
        + 0.25 * convergence_norm
        + 0.20 * hist_norm
        + 0.15 * regime_score
    )
    composite = max(0.0, min(1.0, composite))

    # ── Risk flags ──
    risk_flags = []
    vix_regime = market_ctx.get("vix_regime", "normal")
    if vix_regime == "stress":
        risk_flags.append("VIX stress")
        composite *= 0.7
    elif vix_regime == "elevated":
        risk_flags.append("VIX elevated")

    nifty_regime = market_ctx.get("nifty_regime", "unknown")
    if nifty_regime == "bearish":
        risk_flags.append("Bearish market")
        composite *= 0.85

    remaining = market_ctx.get("remaining_trading_days", 5)
    if remaining <= 1:
        risk_flags.append("No trading days left")
        composite *= 0.3

    # ── Tier classification ──
    if not must_have_pass:
        tier = "AVOID"
    elif composite >= SIGNAL_TIERS["STRONG"]["min_score"]:
        tier = "STRONG"
    elif composite >= SIGNAL_TIERS["ACTIVE"]["min_score"]:
        tier = "ACTIVE"
    elif composite >= SIGNAL_TIERS["WATCH"]["min_score"]:
        tier = "WATCH"
    else:
        tier = "AVOID"

    # ── Expected upside estimation ──
    target_pct = candidate.get("target_pct", 12.0)
    regime_mult = {"bullish": 1.15, "range": 1.0, "bearish": 0.75}.get(nifty_regime, 1.0)
    conv_factor = 0.8 + convergence_norm * 0.4
    expected = target_pct * regime_mult * conv_factor
    upside_range = (round(expected * 0.8, 1), round(expected * 1.2, 1))

    return {
        "score": round(composite, 3),
        "tier": tier,
        "base_score": round(base_score, 3),
        "convergence_score": convergence.get("score", 0),
        "convergence_aligned": convergence.get("aligned", []),
        "convergence_conflicting": convergence.get("conflicting", []),
        "historical_hit_rate": hr_10,
        "historical_samples": hit_rate.get("n_samples", 0),
        "regime_score": round(regime_score, 3),
        "conditions_met": [k for k, v in conditions.items() if v],
        "conditions_failed": [k for k, v in conditions.items() if not v],
        "risk_flags": risk_flags,
        "expected_upside": upside_range,
    }


def compute_regime_alignment(symbol_regime):
    """Compute regime alignment score for long direction (0-1)."""
    if not symbol_regime:
        return 0.5

    score = 0.0
    trend = symbol_regime.get("trend", "sideways")
    if trend in ("strong_up", "mild_up"):
        score += 0.4
    elif trend == "sideways":
        score += 0.15

    weekly = symbol_regime.get("weekly_trend", "sideways")
    if weekly == "up":
        score += 0.3
    elif weekly == "sideways":
        score += 0.1

    momentum = symbol_regime.get("momentum", "steady")
    if momentum == "accelerating":
        score += 0.2
    elif momentum == "steady":
        score += 0.05

    rs = symbol_regime.get("relative_strength", "inline")
    if rs == "outperforming":
        score += 0.1

    return min(1.0, score)


def rank_signals(candidates):
    """Rank IntraWeek candidates by composite score.

    Priority: STRONG > ACTIVE > WATCH > AVOID
    Within tier: by score descending.
    """
    tier_order = {"STRONG": 0, "ACTIVE": 1, "WATCH": 2, "AVOID": 3}

    return sorted(
        candidates,
        key=lambda c: (
            tier_order.get(c.get("tier", "AVOID"), 4),
            -c.get("score", 0),
        ),
    )
