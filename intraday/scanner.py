"""
Intraday Scanner — main orchestrator (institutional grade).

Evaluates all tickers across multiple intraday strategies, ranks signals,
applies portfolio risk overlays, renders dashboard, generates reports,
and calls LLM for advisory.

Time-aware: auto-detects market phase and adapts behavior:
  PRE_MARKET  (before 9:00)  — conditional IF-THEN gap-scenario setups
  PRE_LIVE    (9:00-9:15)    — pre-market auction data → refine scenarios
  LIVE        (9:15-15:15)   — full scanner with time-relevance per strategy
  POST_MARKET (after 15:15)  — session review + tomorrow's watchlist

Usage:
    python -m intraday.scanner            # auto-detects phase and runs
    python -m intraday.scanner --force    # force LIVE mode anytime (testing)
    python -m intraday.scanner --manage   # position management only
"""

import argparse
import warnings
from datetime import datetime, time as dtime
from pathlib import Path

import yaml
from zoneinfo import ZoneInfo

from common.data import TICKERS, PROJECT_ROOT, CONFIG_PATH
from intraday.strategies import (
    evaluate_orb, evaluate_pullback, evaluate_compression,
    evaluate_mean_revert, evaluate_swing, evaluate_mlr,
)

warnings.filterwarnings("ignore")

IST = ZoneInfo("Asia/Kolkata")

# ── Constants ─────────────────────────────────────────────────────────────

MAX_INTRADAY_POSITIONS = 5
MAX_INTRADAY_CAPITAL_PCT = 50.0
MAX_SECTOR_EXPOSURE = 2
MAX_DAILY_DRAWDOWN_PCT = 2.0
MIN_RR_RATIO = 1.2  # minimum RR gate — discard below this
LONG_ONLY = True  # equity cash segment — BUY only, no short selling
ENTRY_WINDOW = (dtime(9, 15), dtime(14, 30))
EXIT_DEADLINE = dtime(15, 0)
LUNCH_WINDOW = (dtime(12, 0), dtime(13, 0))
INTRADAY_REPORT_DIR = PROJECT_ROOT / "intraday" / "reports"

# Phase 2 hook: ML model path (not used yet)
ML_MODEL_PATH = PROJECT_ROOT / "models" / "intraday_scorer.joblib"

SIGNAL_TIERS = {
    "STRONG": "score >= 0.80, RR >= 2.0, regime aligned, DOW+month favorable",
    "ACTIVE": "score >= 0.65, RR >= 1.5, regime compatible",
    "WATCH":  "score 0.50-0.65 or one gate failed",
    "AVOID":  "VIX stress, earnings, illiquid, regime mismatch",
}

STRATEGY_DAILY_LOSS_BUDGET = {
    "orb": 0.5,         # max 0.5% of capital lost on ORB today
    "pullback": 0.5,
    "compression": 0.3,
    "mean_revert": 0.3,
    "swing": 0.5,
    "mlr": 0.5,
}

INTRADAY_CONDITION_WEIGHTS = {
    "vwap_gate": 2.5,
    "nifty_ok": 2.5,
    "not_illiquid": 2.0,
    "rr_ratio": 2.0,
    "strategy_confidence": 3.0,
    "volume_ok": 1.5,
    "regime_aligned": 1.5,
    "dow_favorable": 1.0,
    "month_favorable": 1.0,
}

MUST_HAVE_GATES = ["vwap_gate", "nifty_ok", "not_illiquid"]

# ── Strategy Time Windows (LIVE mode) ────────────────────────────────────

STRATEGY_TIME_WINDOWS = {
    "orb":         (dtime(9, 15), dtime(12, 0)),
    "pullback":    (dtime(9, 30), dtime(14, 30)),
    "compression": (dtime(10, 0), dtime(14, 0)),
    "mean_revert": (dtime(10, 0), dtime(14, 30)),
    "swing":       (dtime(9, 15), dtime(15, 0)),
    "mlr":         (dtime(10, 0), dtime(11, 30)),
}

STRATEGY_FN_MAP = {
    "orb": evaluate_orb,
    "pullback": evaluate_pullback,
    "compression": evaluate_compression,
    "mean_revert": evaluate_mean_revert,
    "swing": evaluate_swing,
    "mlr": evaluate_mlr,
}

# ── MLR Config (precomputed per-ticker calibration) ──────────────────
MLR_CONFIG_PATH = PROJECT_ROOT / "intraday" / "mlr_config.yaml"
_mlr_config = {}
if MLR_CONFIG_PATH.exists():
    try:
        with open(MLR_CONFIG_PATH) as f:
            _raw = yaml.safe_load(f) or {}
        # Build per-ticker lookup: only enabled tickers
        for sym, cfg in _raw.get("tickers", {}).items():
            if cfg.get("enabled", False):
                _mlr_config[sym] = cfg
    except Exception:
        pass  # gracefully degrade without config


# ── Phase Detection ──────────────────────────────────────────────────────

def detect_market_phase(now_ist=None):
    """Auto-detect market phase based on IST clock.

    Returns: "pre_market" | "pre_live" | "live" | "post_market"
    """
    if now_ist is None:
        now_ist = datetime.now(IST)
    t = now_ist.time()
    if t < dtime(9, 0):
        return "pre_market"
    if t < dtime(9, 15):
        return "pre_live"
    if t <= dtime(15, 15):
        return "live"
    return "post_market"


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Intraday Scanner (Time-Aware)")
    parser.add_argument("--force", action="store_true",
                        help="Force LIVE mode anytime (for testing)")
    parser.add_argument("--manage", action="store_true",
                        help="Position management mode only")
    args = parser.parse_args()

    now_ist = datetime.now(IST)
    phase = "live" if args.force else detect_market_phase(now_ist)
    symbols = list(TICKERS.keys())

    print(f"\n  Intraday Scanner - {now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"  Phase: {phase.upper()} | Tickers: {len(TICKERS)} | Strategies: {len(STRATEGY_FN_MAP)}")

    # Weekend check (applies to all phases)
    if now_ist.weekday() >= 5 and not args.force:
        print("  Market closed (weekend). Use --force to override.")
        return

    # Load config
    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}

    # Import phase functions here to avoid circular imports at module level
    from intraday.phases import (
        run_pre_market_scan, run_pre_live_scan,
        run_post_market_scan, _run_live_scan,
    )

    # Dispatch based on phase
    if phase == "pre_market":
        run_pre_market_scan(config, symbols)
    elif phase == "pre_live":
        run_pre_live_scan(config, symbols)
    elif phase == "live":
        _run_live_scan(config, symbols)
    elif phase == "post_market":
        run_post_market_scan(config, symbols)


if __name__ == "__main__":
    main()
