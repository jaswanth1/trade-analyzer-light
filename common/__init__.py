"""
Common utilities for the trading system.
Re-exports the most-used symbols for convenience.
"""

from common.data import fetch_yf, fetch_ticker_info, TICKERS, BENCHMARK, OUTPUT_DIR, GAP_THRESHOLDS, load_universe_for_tier
from common.indicators import compute_atr, compute_beta, compute_vwap, _to_ist, classify_gaps
from common.market import fetch_india_vix, vix_position_scale, detect_nifty_regime
from common.risk import (
    compute_position_size, compute_correlation_clusters,
    compute_portfolio_heat, compute_individual_beta_scale,
    NSE_ROUND_TRIP_COST_PCT, MAX_SAME_DIRECTION,
)
from common.db import (
    log_signal_supa, log_scan_run,
    get_portfolio_metrics_supa, close_trade_supa, get_today_realized_pnl,
)
from common.display import fmt, box_top, box_mid, box_bot, box_line, W
