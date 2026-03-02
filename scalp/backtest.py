#!/usr/bin/env python3
"""
Backtesting Harness for Scalp Scanner

Replays historical 5-min bars through the scanner logic (no look-ahead).
Generates backtest_report.md with equity curve, per-ticker/phase/gap breakdowns.

Usage:
    python backtest_scalp.py [--start 2025-12-01] [--end 2026-02-25] [--capital 1000000]
"""

import argparse
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from common.data import (
    GAP_THRESHOLDS, IST_WINDOWS, TARGET_PCTS, STOP_PCTS,
    SCALP_CONFIG_PATH, SCALP_DIR, fetch_yf,
)
from common.analysis_cache import get_cached, TTL_DAILY
from common.indicators import compute_atr, compute_vwap, _to_ist, classify_gaps

CONFIG_PATH = SCALP_CONFIG_PATH
REPORT_PATH = SCALP_DIR / "backtest_report.md"

# Phase definitions (mirrors scanner)
PHASE_WINDOWS = {
    "AVOID_ZONE":      (dtime(9, 15), dtime(9, 30)),
    "MORNING_SCALP":   (dtime(9, 30), dtime(10, 30)),
    "LATE_MORNING":    (dtime(10, 30), dtime(11, 30)),
    "LUNCH_HOUR":      (dtime(11, 30), dtime(12, 30)),
    "EARLY_AFTERNOON": (dtime(12, 30), dtime(13, 30)),
    "PRE_CLOSE_SETUP": (dtime(13, 30), dtime(14, 30)),
    "AFTERNOON_SCALP": (dtime(14, 30), dtime(15, 15)),
    "CLOSING":         (dtime(15, 15), dtime(15, 30)),
}


def get_phase(t):
    """Determine phase from time."""
    for name, (start, end) in PHASE_WINDOWS.items():
        if start <= t < end:
            return name
    return "POST_MARKET"


@dataclass
class SimTrade:
    symbol: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime = None
    exit_price: float = 0.0
    target_pct: float = 0.0
    stop_pct: float = 0.0
    pnl_pct: float = 0.0
    mae_pct: float = 0.0
    phase: str = ""
    gap_type: str = ""
    result: str = "open"  # target / stop / time_exit / eod_exit
    bars_held: int = 0
    direction: str = "long"


class BacktestEngine:
    """Replays historical data through scanner-like logic."""

    def __init__(self, capital=1000000, config=None):
        self.capital = capital
        self.initial_capital = capital
        self.config = config or {}
        self.trades: list[SimTrade] = []
        self.ticker_data: dict = {}
        self.ticker_configs: dict = {}
        self.daily_equity: list[dict] = []

    def load_data(self):
        """Load OHLCV via fetch_yf() and gap analysis from analysis_cache."""
        tickers_cfg = self.config.get("tickers", [])
        symbols = {tc["symbol"]: tc for tc in tickers_cfg if tc.get("enabled", True)}

        for sym, tc in symbols.items():
            try:
                # OHLCV from fetch_yf (reads Supabase OHLCV cache)
                intra_df = fetch_yf(sym, period="60d", interval="5m")
                daily_df = fetch_yf(sym, period="6mo", interval="1d")

                if intra_df.empty or daily_df.empty:
                    print(f"  [SKIP] No data for {sym}")
                    continue

                # Gap analysis from analysis_cache, fallback to recompute
                gap_records = get_cached("scalp_gap_analysis", symbol=sym, max_age_seconds=TTL_DAILY)
                if gap_records:
                    gap_df = pd.DataFrame(gap_records)
                    # Restore index if 'Date' column exists from cache serialization
                    if "Date" in gap_df.columns:
                        gap_df["Date"] = pd.to_datetime(gap_df["Date"])
                        gap_df = gap_df.set_index("Date")
                else:
                    gap_df = classify_gaps(daily_df)

                # Ensure IST timezone on intraday
                if intra_df.index.tz is None:
                    intra_df.index = intra_df.index.tz_localize("Asia/Kolkata")
                elif str(intra_df.index.tz) != "Asia/Kolkata":
                    intra_df.index = intra_df.index.tz_convert("Asia/Kolkata")

                self.ticker_data[sym] = {
                    "intraday": intra_df,
                    "daily": daily_df,
                    "gaps": gap_df,
                }
                self.ticker_configs[sym] = tc
            except Exception as e:
                print(f"  [WARN] Failed to load {sym}: {e}")

        print(f"  Loaded {len(self.ticker_data)} tickers with historical data")
        return len(self.ticker_data) > 0

    def _get_gap_type(self, sym, trade_date):
        """Get gap type for a specific date."""
        gap_df = self.ticker_data[sym]["gaps"]
        try:
            if trade_date in gap_df.index:
                return gap_df.loc[trade_date, "gap_type"]
            # Try date match
            for idx in gap_df.index:
                d = idx.date() if hasattr(idx, "date") else idx
                if d == trade_date:
                    return gap_df.loc[idx, "gap_type"]
        except Exception:
            pass
        return "unknown"

    def _compute_vwap_up_to(self, bars):
        """Compute VWAP from bars seen so far (no look-ahead)."""
        if bars.empty or "Volume" not in bars.columns:
            return np.nan
        typical = (bars["High"] + bars["Low"] + bars["Close"]) / 3
        cum_tp_vol = (typical * bars["Volume"]).cumsum()
        cum_vol = bars["Volume"].cumsum()
        vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
        return vwap.iloc[-1] if not vwap.empty else np.nan

    def _check_conditions(self, sym, bars_so_far, day_open, bar, phase, gap_type, daily_df):
        """Check entry conditions using only data available up to current bar.

        Returns (all_met, conditions_dict).
        """
        tc = self.ticker_configs[sym]
        entry_conds = tc.get("entry_conditions", {})
        risk = tc.get("risk", {})
        gap_rules = tc.get("gap_rules", {})
        active_phases = tc.get("active_phases", [])
        avoid_phases = tc.get("avoid_phases", [])

        if phase in avoid_phases or phase not in active_phases:
            return False, {"phase": False}

        conditions = {}

        # Gap preference
        phase_gaps = gap_rules.get(phase, {}).get("preferred_gaps", [])
        conditions["gap_preferred"] = gap_type in phase_gaps if phase_gaps else True

        # VWAP
        vwap = self._compute_vwap_up_to(bars_so_far)
        ltp = bar["Close"]
        conditions["above_vwap"] = ltp > vwap if not np.isnan(vwap) else False

        # VWAP reclaim (2 bars above after being below)
        if entry_conds.get("require_vwap_reclaim", False) and len(bars_so_far) >= 3:
            vwap_series = self._compute_vwap_series(bars_so_far)
            above = bars_so_far["Close"] > vwap_series
            reclaimed = False
            for i in range(2, len(above)):
                if not above.iloc[i - 2] and above.iloc[i - 1] and above.iloc[i]:
                    reclaimed = True
                    break
            conditions["vwap_reclaimed"] = reclaimed
        else:
            conditions["vwap_reclaimed"] = True

        # Higher low
        if entry_conds.get("require_higher_low", False) and len(bars_so_far) >= 6:
            op_low = bars_so_far["Low"].iloc[:3].min()
            recent_low = bars_so_far["Low"].iloc[-6:].min() if len(bars_so_far) >= 6 else bars_so_far["Low"].min()
            conditions["higher_low"] = recent_low > op_low
        else:
            conditions["higher_low"] = True

        # Volume
        min_vol = entry_conds.get("min_volume_ratio", 0)
        if min_vol > 0 and len(daily_df) > 20:
            today_vol = bars_so_far["Volume"].sum()
            median_vol = daily_df["Volume"].iloc[-21:-1].median()
            if median_vol > 0:
                # Rough adjustment: scale by fraction of day elapsed
                n_bars = len(bars_so_far)
                total_bars = 75  # ~6.25 hours at 5-min intervals
                projected_vol = today_vol * (total_bars / max(1, n_bars))
                conditions["volume_ok"] = (projected_vol / median_vol) >= min_vol
            else:
                conditions["volume_ok"] = True
        else:
            conditions["volume_ok"] = True

        # Move from open
        max_move = entry_conds.get("max_move_from_open_pct", 999)
        move_pct = abs((ltp / day_open - 1) * 100) if day_open > 0 else 0
        conditions["move_not_extended"] = move_pct <= max_move

        # Range check
        min_range_mult = entry_conds.get("min_range_multiple_of_atr", 0)
        if min_range_mult > 0 and len(daily_df) >= 14:
            atr = compute_atr(daily_df)
            atr_pct = atr / ltp * 100 if not np.isnan(atr) and ltp > 0 else 0
            day_range = (bars_so_far["High"].max() - bars_so_far["Low"].min()) / day_open * 100
            conditions["range_ok"] = day_range >= atr_pct * min_range_mult
        else:
            conditions["range_ok"] = True

        all_met = all(conditions.values())
        return all_met, conditions

    def _compute_vwap_series(self, bars):
        """Compute running VWAP for each bar."""
        typical = (bars["High"] + bars["Low"] + bars["Close"]) / 3
        cum_tp_vol = (typical * bars["Volume"]).cumsum()
        cum_vol = bars["Volume"].cumsum()
        return cum_tp_vol / cum_vol.replace(0, np.nan)

    def simulate_day(self, trade_date, daily_lookback=60):
        """Simulate one trading day for all tickers.

        For each 5-min bar 09:15->15:15:
        - Build "up to this bar" DataFrame (no look-ahead)
        - Check conditions
        - If conditions met -> enter at bar close
        - If in trade -> check target/stop via bar High/Low
        - Track MAE
        """
        day_trades = []

        for sym, data in self.ticker_data.items():
            tc = self.ticker_configs[sym]
            intra = data["intraday"]
            daily = data["daily"]
            risk = tc.get("risk", {})

            # Get today's bars
            day_bars = intra[intra.index.date == trade_date]
            if day_bars.empty or len(day_bars) < 3:
                continue

            # Daily lookback (up to but not including today)
            daily_before = daily[daily.index.date < trade_date].tail(daily_lookback)
            if daily_before.empty:
                continue

            day_open = day_bars["Open"].iloc[0]
            if day_open <= 0:
                continue

            gap_type = self._get_gap_type(sym, trade_date)
            target_pct = risk.get("base_target_pct", 1.0)
            stop_pct = risk.get("base_stop_pct", 1.5)
            max_hold = risk.get("max_hold_minutes", 45)
            max_trades = risk.get("max_trades_per_day", 1)

            active_trade = None
            trades_today = 0

            for i in range(len(day_bars)):
                bar = day_bars.iloc[i]
                bar_time = day_bars.index[i]
                t = bar_time.time() if hasattr(bar_time, 'time') else bar_time

                # Only trade 09:15-15:15
                if t < dtime(9, 15) or t >= dtime(15, 15):
                    continue

                phase = get_phase(t)
                bars_so_far = day_bars.iloc[:i + 1]

                # If in a trade, check exit conditions
                if active_trade is not None:
                    active_trade.bars_held += 1

                    # Track MAE
                    low_from_entry = (bar["Low"] / active_trade.entry_price - 1) * 100
                    active_trade.mae_pct = min(active_trade.mae_pct, low_from_entry)

                    target_price = active_trade.entry_price * (1 + target_pct / 100)
                    stop_price = active_trade.entry_price * (1 - stop_pct / 100)

                    # Check target hit
                    if bar["High"] >= target_price:
                        active_trade.exit_time = bar_time
                        active_trade.exit_price = target_price
                        active_trade.pnl_pct = target_pct
                        active_trade.result = "target"
                        day_trades.append(active_trade)
                        active_trade = None
                        continue

                    # Check stop hit
                    if bar["Low"] <= stop_price:
                        active_trade.exit_time = bar_time
                        active_trade.exit_price = stop_price
                        active_trade.pnl_pct = -stop_pct
                        active_trade.result = "stop"
                        day_trades.append(active_trade)
                        active_trade = None
                        continue

                    # Time exit
                    entry_t = active_trade.entry_time
                    if hasattr(entry_t, 'timestamp'):
                        mins_held = (bar_time - entry_t).total_seconds() / 60
                    else:
                        mins_held = active_trade.bars_held * 5
                    if mins_held >= max_hold:
                        active_trade.exit_time = bar_time
                        active_trade.exit_price = bar["Close"]
                        active_trade.pnl_pct = (bar["Close"] / active_trade.entry_price - 1) * 100
                        active_trade.result = "time_exit"
                        day_trades.append(active_trade)
                        active_trade = None
                        continue

                    continue

                # No active trade — check if we should enter
                if trades_today >= max_trades:
                    continue

                # Need at least 3 bars before entering
                if i < 3:
                    continue

                all_met, conditions = self._check_conditions(
                    sym, bars_so_far, day_open, bar, phase, gap_type, daily_before
                )

                if all_met:
                    active_trade = SimTrade(
                        symbol=sym,
                        entry_time=bar_time,
                        entry_price=bar["Close"],
                        target_pct=target_pct,
                        stop_pct=stop_pct,
                        phase=phase,
                        gap_type=gap_type,
                    )
                    trades_today += 1

            # EOD exit for any remaining trade
            if active_trade is not None:
                last_bar = day_bars.iloc[-1]
                active_trade.exit_time = day_bars.index[-1]
                active_trade.exit_price = last_bar["Close"]
                active_trade.pnl_pct = (last_bar["Close"] / active_trade.entry_price - 1) * 100
                active_trade.result = "eod_exit"
                day_trades.append(active_trade)

        return day_trades

    def run(self, start_date=None, end_date=None):
        """Run the backtest across all dates."""
        # Collect all available dates
        all_dates = set()
        for sym, data in self.ticker_data.items():
            intra = data["intraday"]
            dates = set(intra.index.date)
            all_dates.update(dates)

        all_dates = sorted(all_dates)

        if start_date:
            all_dates = [d for d in all_dates if d >= start_date]
        if end_date:
            all_dates = [d for d in all_dates if d <= end_date]

        # Filter weekends
        all_dates = [d for d in all_dates if d.weekday() < 5]

        print(f"  Backtesting {len(all_dates)} trading days: {all_dates[0]} to {all_dates[-1]}")

        cumulative_pnl = 0.0
        peak_equity = self.initial_capital

        for i, trade_date in enumerate(all_dates):
            day_trades = self.simulate_day(trade_date)
            self.trades.extend(day_trades)

            day_pnl = sum(t.pnl_pct for t in day_trades)
            day_pnl_abs = sum(
                t.entry_price * t.pnl_pct / 100 for t in day_trades
            )
            cumulative_pnl += day_pnl_abs
            equity = self.initial_capital + cumulative_pnl
            peak_equity = max(peak_equity, equity)
            drawdown = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0

            self.daily_equity.append({
                "date": trade_date,
                "trades": len(day_trades),
                "day_pnl_pct": round(day_pnl, 3),
                "cumulative_pnl": round(cumulative_pnl, 2),
                "equity": round(equity, 2),
                "drawdown_pct": round(drawdown, 2),
            })

            if (i + 1) % 10 == 0 or i == len(all_dates) - 1:
                print(f"  [{i+1}/{len(all_dates)}] {trade_date} | Trades: {len(self.trades)} | Equity: {equity:,.0f}")

    def compute_metrics(self) -> dict:
        """Compute overall backtest metrics."""
        if not self.trades:
            return {"error": "No trades"}

        pnls = [t.pnl_pct for t in self.trades]
        n = len(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        # Sharpe
        sharpe = None
        if n > 1:
            mean_r = np.mean(pnls)
            std_r = np.std(pnls, ddof=1)
            if std_r > 0:
                sharpe = round((mean_r / std_r) * math.sqrt(250), 2)

        # Sortino
        sortino = None
        if losses:
            down_std = np.std(losses, ddof=1)
            if down_std > 0:
                sortino = round((np.mean(pnls) / down_std) * math.sqrt(250), 2)

        # Max drawdown from equity curve
        equities = [e["equity"] for e in self.daily_equity]
        if equities:
            peak = np.maximum.accumulate(equities)
            dd = (peak - equities) / peak * 100
            max_dd = float(np.max(dd))
        else:
            max_dd = 0

        # Profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')

        # Win/loss streaks
        max_win_streak = max_loss_streak = win_streak = loss_streak = 0
        for p in pnls:
            if p > 0:
                win_streak += 1
                loss_streak = 0
                max_win_streak = max(max_win_streak, win_streak)
            else:
                loss_streak += 1
                win_streak = 0
                max_loss_streak = max(max_loss_streak, loss_streak)

        # Per-result breakdown
        results = {}
        for r in ["target", "stop", "time_exit", "eod_exit"]:
            rt = [t for t in self.trades if t.result == r]
            results[r] = len(rt)

        return {
            "total_trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / n * 100, 1) if n > 0 else 0,
            "avg_pnl_pct": round(np.mean(pnls), 3),
            "total_pnl_pct": round(sum(pnls), 2),
            "sharpe": sharpe,
            "sortino": sortino,
            "max_drawdown_pct": round(max_dd, 2),
            "profit_factor": profit_factor,
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "avg_bars_held": round(np.mean([t.bars_held for t in self.trades]), 1),
            "avg_mae_pct": round(np.mean([t.mae_pct for t in self.trades]), 3),
            "result_breakdown": results,
        }

    def _ticker_breakdown(self):
        """Per-ticker stats."""
        syms = {}
        for t in self.trades:
            if t.symbol not in syms:
                syms[t.symbol] = []
            syms[t.symbol].append(t)

        rows = []
        for sym, trades in sorted(syms.items()):
            pnls = [t.pnl_pct for t in trades]
            wins = sum(1 for p in pnls if p > 0)
            rows.append({
                "symbol": sym.replace(".NS", ""),
                "trades": len(trades),
                "wins": wins,
                "losses": len(trades) - wins,
                "win_rate": round(wins / len(trades) * 100, 1),
                "avg_pnl": round(np.mean(pnls), 3),
                "total_pnl": round(sum(pnls), 2),
                "avg_mae": round(np.mean([t.mae_pct for t in trades]), 3),
            })
        return sorted(rows, key=lambda x: -x["total_pnl"])

    def _phase_breakdown(self):
        """Per-phase stats."""
        phases = {}
        for t in self.trades:
            if t.phase not in phases:
                phases[t.phase] = []
            phases[t.phase].append(t)

        rows = []
        for phase, trades in sorted(phases.items()):
            pnls = [t.pnl_pct for t in trades]
            wins = sum(1 for p in pnls if p > 0)
            rows.append({
                "phase": phase,
                "trades": len(trades),
                "wins": wins,
                "win_rate": round(wins / len(trades) * 100, 1),
                "avg_pnl": round(np.mean(pnls), 3),
                "total_pnl": round(sum(pnls), 2),
            })
        return sorted(rows, key=lambda x: -x["total_pnl"])

    def _gap_breakdown(self):
        """Per-gap-type stats."""
        gaps = {}
        for t in self.trades:
            if t.gap_type not in gaps:
                gaps[t.gap_type] = []
            gaps[t.gap_type].append(t)

        rows = []
        for gap_type, trades in sorted(gaps.items()):
            pnls = [t.pnl_pct for t in trades]
            wins = sum(1 for p in pnls if p > 0)
            rows.append({
                "gap_type": gap_type,
                "trades": len(trades),
                "wins": wins,
                "win_rate": round(wins / len(trades) * 100, 1),
                "avg_pnl": round(np.mean(pnls), 3),
                "total_pnl": round(sum(pnls), 2),
                "avg_mae": round(np.mean([t.mae_pct for t in trades]), 3),
            })
        return sorted(rows, key=lambda x: -x["total_pnl"])

    def generate_report(self):
        """Generate backtest_report.md."""
        metrics = self.compute_metrics()

        lines = []
        lines.append("# Scalp Backtest Report")
        lines.append(f"\n*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")

        if self.daily_equity:
            lines.append(f"**Period**: {self.daily_equity[0]['date']} to {self.daily_equity[-1]['date']}")
        lines.append(f"**Initial Capital**: {self.initial_capital:,.0f}")
        final_equity = self.daily_equity[-1]["equity"] if self.daily_equity else self.initial_capital
        lines.append(f"**Final Equity**: {final_equity:,.0f}")
        total_return = (final_equity / self.initial_capital - 1) * 100
        lines.append(f"**Total Return**: {total_return:+.2f}%\n")

        # Summary table
        lines.append("## Summary\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Trades | {metrics['total_trades']} |")
        lines.append(f"| Win Rate | {metrics['win_rate']}% |")
        lines.append(f"| Avg P&L per Trade | {metrics['avg_pnl_pct']:+.3f}% |")
        lines.append(f"| Total P&L | {metrics['total_pnl_pct']:+.2f}% |")
        sharpe = f"{metrics['sharpe']:.2f}" if metrics['sharpe'] is not None else "N/A"
        sortino = f"{metrics['sortino']:.2f}" if metrics['sortino'] is not None else "N/A"
        lines.append(f"| Sharpe Ratio | {sharpe} |")
        lines.append(f"| Sortino Ratio | {sortino} |")
        lines.append(f"| Max Drawdown | {metrics['max_drawdown_pct']}% |")
        lines.append(f"| Profit Factor | {metrics['profit_factor']} |")
        lines.append(f"| Max Win Streak | {metrics['max_win_streak']} |")
        lines.append(f"| Max Loss Streak | {metrics['max_loss_streak']} |")
        lines.append(f"| Avg Bars Held | {metrics['avg_bars_held']} |")
        lines.append(f"| Avg MAE | {metrics['avg_mae_pct']}% |")

        # Result breakdown
        rb = metrics.get("result_breakdown", {})
        lines.append(f"| Targets Hit | {rb.get('target', 0)} |")
        lines.append(f"| Stops Hit | {rb.get('stop', 0)} |")
        lines.append(f"| Time Exits | {rb.get('time_exit', 0)} |")
        lines.append(f"| EOD Exits | {rb.get('eod_exit', 0)} |")
        lines.append("")

        # Daily equity curve
        lines.append("## Daily Equity Curve\n")
        lines.append("| Date | Trades | Day P&L% | Cumulative P&L | Equity | Drawdown% |")
        lines.append("|------|--------|----------|----------------|--------|-----------|")
        for e in self.daily_equity:
            lines.append(
                f"| {e['date']} | {e['trades']} | {e['day_pnl_pct']:+.3f}% | "
                f"{e['cumulative_pnl']:+,.0f} | {e['equity']:,.0f} | {e['drawdown_pct']:.2f}% |"
            )
        lines.append("")

        # Per-ticker breakdown
        ticker_rows = self._ticker_breakdown()
        if ticker_rows:
            lines.append("## Per-Ticker Breakdown\n")
            lines.append("| Ticker | Trades | W | L | WR% | Avg P&L% | Total P&L% | Avg MAE% |")
            lines.append("|--------|--------|---|---|-----|----------|------------|----------|")
            for r in ticker_rows:
                lines.append(
                    f"| {r['symbol']} | {r['trades']} | {r['wins']} | {r['losses']} | "
                    f"{r['win_rate']}% | {r['avg_pnl']:+.3f}% | {r['total_pnl']:+.2f}% | {r['avg_mae']:.3f}% |"
                )
            lines.append("")

        # Per-phase breakdown
        phase_rows = self._phase_breakdown()
        if phase_rows:
            lines.append("## Per-Phase Breakdown\n")
            lines.append("| Phase | Trades | W | WR% | Avg P&L% | Total P&L% |")
            lines.append("|-------|--------|---|-----|----------|------------|")
            for r in phase_rows:
                lines.append(
                    f"| {r['phase']} | {r['trades']} | {r['wins']} | "
                    f"{r['win_rate']}% | {r['avg_pnl']:+.3f}% | {r['total_pnl']:+.2f}% |"
                )
            lines.append("")

        # Per-gap breakdown
        gap_rows = self._gap_breakdown()
        if gap_rows:
            lines.append("## Per-Gap-Type Breakdown\n")
            lines.append("| Gap Type | Trades | W | WR% | Avg P&L% | Total P&L% | Avg MAE% |")
            lines.append("|----------|--------|---|-----|----------|------------|----------|")
            for r in gap_rows:
                lines.append(
                    f"| {r['gap_type']} | {r['trades']} | {r['wins']} | "
                    f"{r['win_rate']}% | {r['avg_pnl']:+.3f}% | {r['total_pnl']:+.2f}% | {r['avg_mae']:.3f}% |"
                )
            lines.append("")

        # Worst drawdown periods
        if self.daily_equity:
            lines.append("## Worst Drawdown Periods\n")
            lines.append("| Start | End | Duration | Max DD% |")
            lines.append("|-------|-----|----------|---------|")

            # Find top 5 drawdown periods
            dd_periods = []
            in_dd = False
            dd_start = None
            max_dd = 0

            for e in self.daily_equity:
                if e["drawdown_pct"] > 0.1:
                    if not in_dd:
                        dd_start = e["date"]
                        in_dd = True
                    max_dd = max(max_dd, e["drawdown_pct"])
                else:
                    if in_dd:
                        dd_periods.append({
                            "start": dd_start,
                            "end": e["date"],
                            "max_dd": max_dd,
                        })
                        in_dd = False
                        max_dd = 0

            if in_dd and dd_start:
                dd_periods.append({
                    "start": dd_start,
                    "end": self.daily_equity[-1]["date"],
                    "max_dd": max_dd,
                })

            dd_periods.sort(key=lambda x: -x["max_dd"])
            for dd in dd_periods[:5]:
                duration = (dd["end"] - dd["start"]).days if hasattr(dd["end"], "__sub__") else "?"
                lines.append(f"| {dd['start']} | {dd['end']} | {duration}d | {dd['max_dd']:.2f}% |")
            if not dd_periods:
                lines.append("| No significant drawdowns | | | |")
            lines.append("")

        report = "\n".join(lines) + "\n"
        REPORT_PATH.write_text(report)
        print(f"\n  Report saved: {REPORT_PATH}")
        return metrics


def main():
    parser = argparse.ArgumentParser(description="Scalp Backtester")
    parser.add_argument("--start", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=int, default=None, help="Starting capital")
    args = parser.parse_args()

    # Load config
    if not CONFIG_PATH.exists():
        print(f"Error: Config not found: {CONFIG_PATH}")
        print("Run generate_scalp_config.py first.")
        return

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    capital = args.capital or config.get("global", {}).get("capital", 1000000)

    print(f"\n  Scalp Backtester")
    print(f"  Capital: {capital:,.0f}")

    # Parse dates
    start_date = date.fromisoformat(args.start) if args.start else None
    end_date = date.fromisoformat(args.end) if args.end else None

    if start_date and end_date:
        print(f"  Period: {start_date} to {end_date}")

    # Initialize engine
    engine = BacktestEngine(capital=capital, config=config)

    print("  Loading data...")
    if not engine.load_data():
        print("  No data found. Run 'python -m scalp.config' first.")
        return

    # Run backtest
    print("  Running backtest...")
    engine.run(start_date=start_date, end_date=end_date)

    # Generate report
    metrics = engine.generate_report()

    # Print summary
    print(f"\n  ── Backtest Results ──")
    print(f"  Total Trades: {metrics['total_trades']}")
    print(f"  Win Rate: {metrics['win_rate']}%")
    print(f"  Avg P&L: {metrics['avg_pnl_pct']:+.3f}%")
    print(f"  Total P&L: {metrics['total_pnl_pct']:+.2f}%")
    sharpe = f"{metrics['sharpe']:.2f}" if metrics['sharpe'] is not None else "N/A"
    print(f"  Sharpe: {sharpe}")
    print(f"  Max DD: {metrics['max_drawdown_pct']}%")
    print(f"  Profit Factor: {metrics['profit_factor']}")
    print()


if __name__ == "__main__":
    main()
