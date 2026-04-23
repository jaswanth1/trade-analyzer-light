"""
Human-readable signal explanations for IntraWeek candidates.
"""

STRATEGY_DESCRIPTIONS = {
    "oversold_recovery": "Oversold Recovery — stock dropped sharply while sector held, recovery expected",
    "vol_compression": "Volatility Compression — tight consolidation with energy building, breakout imminent",
    "weekly_context": "Weekly Context — calendar-driven opportunity (holiday/expiry week dislocation)",
}

STRATEGY_SHORT = {
    "oversold_recovery": "Oversold Recovery",
    "vol_compression": "Vol Compression",
    "weekly_context": "Weekly Context",
}


def generate_explanation(candidate):
    """Generate human-readable explanation for why this stock is flagged.

    Returns list of reason strings.
    """
    strategy = candidate.get("strategy", "")
    metrics = candidate.get("metrics", {})
    scoring = candidate.get("scoring", {})
    reasons = []

    if strategy == "oversold_recovery":
        dd = metrics.get("drawdown_pct", 0)
        rsi = metrics.get("rsi", 50)
        vol_r = metrics.get("down_vol_ratio", 1)
        sec = metrics.get("sector_change", 0)

        reasons.append(f"{dd:.1f}% drawdown in {metrics.get('n_down_days', 0)} days, sector {sec:+.1f}%")
        reasons.append(f"RSI(14) = {rsi:.0f} — {'deeply oversold' if rsi < 30 else 'oversold'}")
        if vol_r >= 1.3:
            reasons.append(f"Volume {vol_r:.1f}x median on down day (capitulation)")

    elif strategy == "vol_compression":
        bw = metrics.get("bb_bandwidth", 0)
        squeeze = metrics.get("squeeze_active", False)
        atr_p = metrics.get("atr_percentile")
        vol_dec = metrics.get("vol_declining", False)

        reasons.append(f"Bollinger bandwidth {bw:.4f} at 20-day low")
        if squeeze:
            reasons.append("Keltner squeeze active — breakout imminent")
        if metrics.get("ema_bullish"):
            reasons.append("EMA 9 > 20 > 50 — bullish structure")
        if vol_dec:
            reasons.append("Volume declining into squeeze (energy building)")
        if atr_p is not None:
            reasons.append(f"ATR percentile {atr_p:.0f}% — low volatility state")

    elif strategy == "weekly_context":
        if metrics.get("early_week_drop"):
            reasons.append(f"Early-week weakness: {metrics.get('drawdown_pct', 0):.1f}% drop")
        sec = metrics.get("sector_change", 0)
        if sec > 0:
            reasons.append(f"Sector still positive ({sec:+.1f}%) — stock-specific selloff")
        if metrics.get("is_holiday_week"):
            reasons.append("Holiday week — pre-holiday selloff reversal pattern")
        if metrics.get("is_expiry_week"):
            reasons.append("Expiry week — F&O unwinding creates opportunity")
        reasons.append(f"{metrics.get('remaining_days', 0)} trading days remaining this week")

    # Convergence
    conv_aligned = scoring.get("convergence_aligned", [])
    conv_total = len(conv_aligned) + len(scoring.get("convergence_conflicting", []))
    if conv_total > 0:
        reasons.append(f"Convergence: {len(conv_aligned)}/{conv_total} indicators aligned")

    # Historical hit rate
    hr = scoring.get("historical_hit_rate", 0)
    samples = scoring.get("historical_samples", 0)
    if samples >= 5:
        reasons.append(f"Historical: {hr:.0f}% hit rate for 10%+ move ({samples} samples)")

    return reasons


def generate_risk_notes(candidate):
    """Generate risk flag notes."""
    scoring = candidate.get("scoring", {})
    flags = scoring.get("risk_flags", [])
    notes = []
    for flag in flags:
        notes.append(flag)
    return notes
