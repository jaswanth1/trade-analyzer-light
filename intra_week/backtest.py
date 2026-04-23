"""
IntraWeek Backtest — historical simulation of IntraWeek strategies.

Replays week-by-week:
  1. Monday: run scanner → select candidates
  2. Track through week using daily OHLC
  3. Exit on target hit / stop hit / Friday close

Usage:
    python -m intra_week.backtest --start 2025-01-01 --end 2026-04-01
    python -m intra_week.backtest --last-quarter
    python -m intra_week.backtest --start 2025-06-01 --end 2026-01-01 --capital 500000
"""

import argparse
import logging
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from common.data import fetch_yf, BENCHMARK, PROJECT_ROOT, load_universe_for_tier
from common.indicators import compute_atr
from common.market import detect_nifty_regime
from intraday.features import compute_rsi, compute_bollinger, compute_keltner, compute_ema, compute_macd

from intra_week.strategies import (
    evaluate_oversold_recovery, evaluate_vol_compression, evaluate_weekly_context,
)
from intra_week.convergence import compute_weekly_convergence, compute_weekly_hit_rate
from intra_week.scoring import compute_composite_score, compute_regime_alignment, rank_signals
from intra_week.weekly_context import get_weekly_context, _is_trading_day
from intra_week.backtest_report import generate_report

warnings.filterwarnings("ignore")
log = logging.getLogger(__name__)

REPORT_DIR = PROJECT_ROOT / "intra_week" / "reports"

# ── Trade dataclass ───────────────────────────────────────────────────────

@dataclass
class WeeklyTrade:
    symbol: str
    strategy: str
    entry_date: date
    entry_price: float
    target_pct: float
    stop_pct: float
    score: float = 0.0
    tier: str = ""
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str = ""  # "target" | "stop" | "time_exit"
    pnl_pct: float = 0.0
    mfe_pct: float = 0.0  # max favorable excursion
    mae_pct: float = 0.0  # max adverse excursion
    holding_days: int = 0

    def close(self, exit_date, exit_price, reason):
        self.exit_date = exit_date
        self.exit_price = exit_price
        self.exit_reason = reason
        self.pnl_pct = (exit_price / self.entry_price - 1) * 100
        self.holding_days = (exit_date - self.entry_date).days


# ── Backtest Engine ───────────────────────────────────────────────────────

MAX_POSITIONS = 5


def _get_week_mondays(start_date, end_date):
    """Generate Monday dates between start and end."""
    mondays = []
    d = start_date
    # Advance to first Monday
    while d.weekday() != 0:
        d += timedelta(days=1)
    while d <= end_date:
        if _is_trading_day(d):
            mondays.append(d)
        d += timedelta(days=7)
    return mondays


def _simulate_week(trade, daily_df, entry_idx):
    """Simulate a trade through the week using daily OHLC.

    Checks target/stop each day, force exits on Friday or last available day.
    """
    target_price = trade.entry_price * (1 + trade.target_pct / 100)
    stop_price = trade.entry_price * (1 - trade.stop_pct / 100)

    max_high = trade.entry_price
    min_low = trade.entry_price

    # Walk forward up to 5 trading days
    for offset in range(1, 6):
        idx = entry_idx + offset
        if idx >= len(daily_df):
            break

        row = daily_df.iloc[idx]
        day_high = float(row["High"])
        day_low = float(row["Low"])
        day_close = float(row["Close"])
        trade_date = daily_df.index[idx]
        if hasattr(trade_date, 'date'):
            trade_date = trade_date.date()

        max_high = max(max_high, day_high)
        min_low = min(min_low, day_low)

        # Update MFE/MAE
        trade.mfe_pct = max(trade.mfe_pct, (max_high / trade.entry_price - 1) * 100)
        trade.mae_pct = max(trade.mae_pct, (trade.entry_price - min_low) / trade.entry_price * 100)

        # Check stop hit (assume intraday — check low first)
        if day_low <= stop_price:
            trade.close(trade_date, stop_price, "stop")
            return

        # Check target hit
        if day_high >= target_price:
            trade.close(trade_date, target_price, "target")
            return

    # Time exit — close at last available day's close
    last_idx = min(entry_idx + 5, len(daily_df) - 1)
    last_row = daily_df.iloc[last_idx]
    last_date = daily_df.index[last_idx]
    if hasattr(last_date, 'date'):
        last_date = last_date.date()
    trade.close(last_date, float(last_row["Close"]), "time_exit")


def run_backtest(start_date, end_date, capital=1_000_000):
    """Run week-by-week backtest.

    For each Monday:
    1. Use data up to Monday to evaluate strategies
    2. Select top candidates
    3. Simulate through the week
    """
    print(f"[Backtest] {start_date} to {end_date}, capital: {capital:,.0f}")

    # Load universe
    try:
        tickers = load_universe_for_tier("intra_week")
    except Exception:
        tickers = load_universe_for_tier("btst")
    symbols = list(tickers.keys())

    # Fetch full history for all symbols
    print(f"[Backtest] Fetching data for {len(symbols)} stocks...")
    # We need data from before start_date for indicators (3 months buffer)
    fetch_start = start_date - timedelta(days=120)
    # Compute period string large enough to cover fetch_start → end_date
    total_days = (end_date - fetch_start).days + 30  # extra buffer
    if total_days <= 365:
        fetch_period = "1y"
    elif total_days <= 730:
        fetch_period = "2y"
    else:
        fetch_period = "5y"

    all_daily = {}
    for sym in symbols:
        try:
            df = fetch_yf(sym, period=fetch_period, interval="1d")
            if df is not None and not df.empty:
                # Slice to relevant date range
                df = df[df.index >= pd.Timestamp(fetch_start)]
                df = df[df.index <= pd.Timestamp(end_date + timedelta(days=7))]
                if not df.empty:
                    all_daily[sym] = df
        except Exception:
            pass

    # Nifty data
    nifty_daily = fetch_yf(BENCHMARK, period=fetch_period, interval="1d")
    if nifty_daily is not None and not nifty_daily.empty:
        nifty_daily = nifty_daily[nifty_daily.index >= pd.Timestamp(fetch_start)]
        nifty_daily = nifty_daily[nifty_daily.index <= pd.Timestamp(end_date + timedelta(days=7))]

    # Sector data
    sectors_needed = set()
    for sym, cfg in tickers.items():
        sec = cfg.get("sector", "")
        if sec:
            sectors_needed.add(sec)

    sector_data = {}
    for sec_sym in sectors_needed:
        try:
            df = fetch_yf(sec_sym, period=fetch_period, interval="1d")
            if df is not None and not df.empty:
                df = df[df.index >= pd.Timestamp(fetch_start)]
                df = df[df.index <= pd.Timestamp(end_date + timedelta(days=7))]
                if not df.empty:
                    sector_data[sec_sym] = df
        except Exception:
            pass

    # Generate week mondays
    mondays = _get_week_mondays(start_date, end_date)
    print(f"[Backtest] Simulating {len(mondays)} weeks...")

    all_trades = []

    for monday in mondays:
        weekly_ctx = get_weekly_context(monday)

        # Slice data up to Monday for evaluation
        candidates = []

        # Simple market context for backtest
        if nifty_daily is not None and not nifty_daily.empty:
            nifty_slice = nifty_daily[nifty_daily.index.date <= monday] if hasattr(nifty_daily.index, 'date') else nifty_daily.loc[:str(monday)]
            if len(nifty_slice) >= 20:
                nifty_regime, _, _ = detect_nifty_regime(nifty_slice)
            else:
                nifty_regime = "unknown"
        else:
            nifty_slice = pd.DataFrame()
            nifty_regime = "unknown"

        market_ctx = {
            "vix_val": None,
            "vix_regime": "normal",
            "nifty_regime": nifty_regime,
            "beta_scale": 1.0,
            "regime_strength": 0.5,
            "inst_flow": "neutral",
            "remaining_trading_days": weekly_ctx["remaining_trading_days"],
        }

        for sym in symbols:
            full_df = all_daily.get(sym, pd.DataFrame())
            if full_df.empty:
                continue

            # Slice to data available on Monday
            daily_slice = full_df[full_df.index.date <= monday] if hasattr(full_df.index, 'date') else full_df.loc[:str(monday)]
            if len(daily_slice) < 50:
                continue

            cfg = tickers.get(sym, {})
            sec_sym = cfg.get("sector", "")
            sec_df = sector_data.get(sec_sym, pd.DataFrame())
            if not sec_df.empty:
                sec_slice = sec_df[sec_df.index.date <= monday] if hasattr(sec_df.index, 'date') else sec_df.loc[:str(monday)]
            else:
                sec_slice = pd.DataFrame()

            for strategy_fn in [evaluate_oversold_recovery, evaluate_vol_compression, evaluate_weekly_context]:
                try:
                    result = strategy_fn(sym, daily_slice, nifty_slice, sec_slice, weekly_ctx, market_ctx)
                except Exception:
                    continue

                if result is None:
                    continue

                # Score it
                convergence = compute_weekly_convergence(daily_slice, None, nifty_slice)
                hit_rate = compute_weekly_hit_rate(daily_slice)
                regime_score = 0.5  # simplified for backtest

                scoring = compute_composite_score(result, convergence, hit_rate, regime_score, market_ctx)
                result["score"] = scoring["score"]
                result["tier"] = scoring["tier"]
                result["scoring"] = scoring

                if scoring["tier"] != "AVOID":
                    candidates.append(result)

        # Rank and select top N
        ranked = rank_signals(candidates)
        selected = ranked[:MAX_POSITIONS]

        # Simulate each trade
        for cand in selected:
            sym = cand["symbol"]
            full_df = all_daily.get(sym, pd.DataFrame())
            if full_df.empty:
                continue

            # Find Monday's index in full data
            monday_indices = [i for i, d in enumerate(full_df.index) if (d.date() if hasattr(d, 'date') else d) >= monday]
            if not monday_indices:
                continue

            entry_idx = monday_indices[0]
            entry_price = float(full_df.iloc[entry_idx]["Close"])

            trade = WeeklyTrade(
                symbol=sym,
                strategy=cand["strategy"],
                entry_date=monday,
                entry_price=entry_price,
                target_pct=cand["target_pct"],
                stop_pct=cand["stop_pct"],
                score=cand["score"],
                tier=cand["tier"],
            )

            _simulate_week(trade, full_df, entry_idx)
            all_trades.append(trade)

    print(f"[Backtest] Completed: {len(all_trades)} trades across {len(mondays)} weeks")
    return all_trades


# ── Metrics Computation ──────────────────────────────────────────────────

def compute_metrics(trades):
    """Compute aggregate backtest metrics."""
    if not trades:
        return {"n_trades": 0}

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # By strategy
    strategies = {}
    for t in trades:
        s = t.strategy
        if s not in strategies:
            strategies[s] = []
        strategies[s].append(t.pnl_pct)

    by_strategy = {}
    for s, pnl_list in strategies.items():
        arr = np.array(pnl_list)
        by_strategy[s] = {
            "n_trades": len(arr),
            "win_rate": round(float(np.mean(arr > 0)) * 100, 1),
            "avg_pnl": round(float(np.mean(arr)), 2),
            "total_pnl": round(float(np.sum(arr)), 2),
        }

    # By exit reason
    exit_reasons = {}
    for t in trades:
        r = t.exit_reason
        if r not in exit_reasons:
            exit_reasons[r] = []
        exit_reasons[r].append(t.pnl_pct)

    by_exit = {}
    for r, pnl_list in exit_reasons.items():
        arr = np.array(pnl_list)
        by_exit[r] = {
            "n_trades": len(arr),
            "avg_pnl": round(float(np.mean(arr)), 2),
        }

    # Equity curve for max drawdown
    cumulative = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = running_max - cumulative
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0

    # Hit rate at different thresholds
    arr = np.array(pnls)
    pct_10plus = round(float(np.mean(arr >= 10)) * 100, 1)
    pct_20plus = round(float(np.mean(arr >= 20)) * 100, 1)

    # Profit factor
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 1
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')

    # Sharpe (weekly)
    if len(pnls) > 1:
        sharpe = round(float(np.mean(pnls) / np.std(pnls) * np.sqrt(52)), 2) if np.std(pnls) > 0 else 0
    else:
        sharpe = 0

    # Avg holding days
    holding_days = [t.holding_days for t in trades if t.holding_days > 0]
    avg_hold = round(float(np.mean(holding_days)), 1) if holding_days else 0

    return {
        "n_trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "avg_pnl": round(float(np.mean(pnls)), 2),
        "total_pnl": round(float(np.sum(pnls)), 2),
        "pct_10plus": pct_10plus,
        "pct_20plus": pct_20plus,
        "avg_holding_days": avg_hold,
        "max_drawdown": round(max_dd, 2),
        "profit_factor": profit_factor,
        "sharpe": sharpe,
        "avg_mfe": round(float(np.mean([t.mfe_pct for t in trades])), 2),
        "avg_mae": round(float(np.mean([t.mae_pct for t in trades])), 2),
        "by_strategy": by_strategy,
        "by_exit_reason": by_exit,
    }


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IntraWeek Backtest")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--last-quarter", action="store_true", help="Backtest last 3 months")
    parser.add_argument("--capital", type=int, default=1_000_000, help="Starting capital")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    if args.last_quarter:
        end_date = date.today()
        start_date = end_date - timedelta(days=90)
    elif args.start and args.end:
        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end)
    else:
        print("Usage: python -m intra_week.backtest --start 2025-01-01 --end 2026-04-01")
        print("       python -m intra_week.backtest --last-quarter")
        return

    trades = run_backtest(start_date, end_date, capital=args.capital)
    metrics = compute_metrics(trades)

    # Generate report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"backtest_{start_date}_{end_date}.md"
    generate_report(trades, metrics, report_path)
    print(f"\nReport saved: {report_path}")

    # Print summary
    print(f"\n{'='*60}")
    print(f"BACKTEST SUMMARY: {start_date} → {end_date}")
    print(f"{'='*60}")
    print(f"Total Trades:     {metrics['n_trades']}")
    print(f"Win Rate:         {metrics['win_rate']}%")
    print(f"Avg PnL:          {metrics['avg_pnl']:.2f}%")
    print(f"Total PnL:        {metrics['total_pnl']:.2f}%")
    print(f"% Reaching 10%+:  {metrics['pct_10plus']}%")
    print(f"% Reaching 20%+:  {metrics['pct_20plus']}%")
    print(f"Avg Hold Days:    {metrics['avg_holding_days']}")
    print(f"Max Drawdown:     {metrics['max_drawdown']:.2f}%")
    print(f"Profit Factor:    {metrics['profit_factor']}")
    print(f"Sharpe Ratio:     {metrics['sharpe']}")
    print(f"Avg MFE:          {metrics['avg_mfe']:.2f}%")
    print(f"Avg MAE:          {metrics['avg_mae']:.2f}%")

    if metrics.get("by_strategy"):
        print(f"\nBy Strategy:")
        for s, m in metrics["by_strategy"].items():
            print(f"  {s}: {m['n_trades']} trades, WR {m['win_rate']}%, avg {m['avg_pnl']:.2f}%")

    if metrics.get("by_exit_reason"):
        print(f"\nBy Exit Reason:")
        for r, m in metrics["by_exit_reason"].items():
            print(f"  {r}: {m['n_trades']} trades, avg {m['avg_pnl']:.2f}%")


if __name__ == "__main__":
    main()
