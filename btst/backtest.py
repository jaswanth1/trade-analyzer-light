#!/usr/bin/env python3
"""
BTST (Buy Today Sell Tomorrow) Backtest

Replays historical days through the BTST scanner pipeline:
  1. Run BTST evaluation at 14:30 (closing strength, convergence, overnight stats)
  2. Validate signals against actual next-day price action
  3. Track: entry at close, next-day open gap, target/stop hit, EOD exit

Key BTST-specific validations:
  - Did next-day open gap up or down? (overnight gap risk)
  - Was target hit by next-day close?
  - Was stop triggered at next-day open (gap-down)?
  - Max hold: 2 trading days

Usage:
    python -m btst.backtest --date 2026-02-20
    python -m btst.backtest --start 2026-02-10 --end 2026-02-20
"""

import argparse
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from common.data import fetch_yf, TICKERS, BENCHMARK, CONFIG_PATH, PROJECT_ROOT
from common.indicators import compute_atr, compute_vwap, _to_ist, classify_gaps
from common.market import fetch_india_vix, detect_nifty_regime, estimate_institutional_flow
from intraday.regime import classify_symbol_regime, classify_month_period, DOW_NAMES
from intraday.explanations import _action_label
from btst.scanner import (
    evaluate_btst, rank_btst_signals, compute_overnight_stats,
    BTST_REPORT_DIR, MAX_HOLD_DAYS,
)

warnings.filterwarnings("ignore")

IST = ZoneInfo("Asia/Kolkata")


# ── Signal result dataclass ───────────────────────────────────────────────

@dataclass
class BTSTSignalResult:
    """Validated BTST backtest signal."""
    symbol: str
    name: str
    signal_tier: str          # "STRONG_BUY", "BUY", "WATCH"
    entry_price: float        # close price on signal day
    target_price: float
    stop_price: float
    target_pct: float
    stop_pct: float
    composite_score: float
    overnight_wr: float
    convergence_score: float
    convergence_detail: str
    action_text: str

    # Regime context
    regime_trend: str = ""
    regime_weekly: str = ""

    # Validation results
    outcome: str = "pending"      # "WIN", "LOSS", "PARTIAL", "NO_TRADE", "GAP_STOP"
    next_open: float = 0.0
    next_high: float = 0.0
    next_low: float = 0.0
    next_close: float = 0.0
    gap_pct: float = 0.0         # (next_open - entry) / entry * 100
    exit_price: float = 0.0
    exit_reason: str = ""        # "target", "stop", "gap_stop", "eod_d1", "eod_d2"
    exit_day: int = 0            # 1 = next day, 2 = day after

    # MFE / MAE over holding period
    mfe: float = 0.0             # max favorable price
    mae: float = 0.0             # max adverse price
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    mfe_of_target: float = 0.0   # MFE as % of target distance
    pnl_pct: float = 0.0         # actual P&L %

    # Holding period tracking
    day1_high: float = 0.0
    day1_low: float = 0.0
    day1_close: float = 0.0
    day2_high: float = 0.0
    day2_low: float = 0.0
    day2_close: float = 0.0


# ── Backtest Engine ───────────────────────────────────────────────────────

class BTSTBacktestEngine:
    """Backtest engine for the BTST scanner."""

    def __init__(self, signal_date, capital=1000000, config=None):
        self.signal_date = signal_date  # day we generate signals (buy day)
        self.capital = capital
        self.config = config or {}
        self.all_signals: list[BTSTSignalResult] = []
        self.data_cache: dict = {}
        self.symbols = list(TICKERS.keys())

    # ── Data Fetching ──────────────────────────────────────────────────

    def fetch_all_data(self):
        """Pre-fetch daily (6mo) + intraday (5d, 5min) for all tickers + Nifty."""
        print(f"  Fetching data for {len(self.symbols)} tickers + Nifty...")

        # Nifty
        nifty_daily = fetch_yf(BENCHMARK, period="6mo", interval="1d")
        nifty_intra = fetch_yf(BENCHMARK, period="5d", interval="5m")
        self.data_cache["_nifty"] = {"daily": nifty_daily, "intra": nifty_intra}

        # VIX
        try:
            vix_val, vix_regime = fetch_india_vix()
        except Exception:
            vix_val, vix_regime = None, "normal"
        self.data_cache["_vix"] = (vix_val, vix_regime)

        # Tickers
        for sym in self.symbols:
            daily = fetch_yf(sym, period="6mo", interval="1d")
            intra = fetch_yf(sym, period="5d", interval="5m")
            self.data_cache[sym] = {"daily": daily, "intra": intra}

        # Sector indices
        sectors = set(cfg["sector"] for cfg in TICKERS.values() if cfg.get("sector"))
        for sec in sectors:
            self.data_cache[sec] = {"daily": fetch_yf(sec, period="5d", interval="1d")}

        print(f"  Data fetched: {len(self.data_cache)} items cached")

    # ── Data Slicing ──────────────────────────────────────────────────

    def _slice_daily(self, sym, up_to_date):
        """Daily data up to (inclusive) a date."""
        raw = self.data_cache.get(sym, {}).get("daily", pd.DataFrame())
        if raw.empty:
            return raw
        mask = raw.index.date <= up_to_date
        return raw[mask].copy()

    def _get_trading_days(self):
        """Get sorted list of trading days from Nifty daily data."""
        nifty_daily = self.data_cache.get("_nifty", {}).get("daily", pd.DataFrame())
        if nifty_daily.empty:
            return []
        return sorted(set(nifty_daily.index.date))

    def _get_next_trading_days(self, after_date, n=2):
        """Get next N trading days after a given date."""
        all_days = self._get_trading_days()
        future = [d for d in all_days if d > after_date]
        return future[:n]

    def _get_daily_bar(self, sym, target_date):
        """Get daily OHLCV for a specific date."""
        daily = self.data_cache.get(sym, {}).get("daily", pd.DataFrame())
        if daily.empty:
            return None
        day_data = daily[daily.index.date == target_date]
        if day_data.empty:
            return None
        return day_data.iloc[0]

    # ── Signal Generation ─────────────────────────────────────────────

    def generate_signals(self):
        """Run BTST scanner for signal_date and collect signals."""
        print(f"\n  Generating BTST signals for {self.signal_date}...")

        vix_val, vix_regime = self.data_cache.get("_vix", (None, "normal"))
        vix_info = (vix_val, vix_regime)

        nifty_daily = self._slice_daily("_nifty", self.signal_date)
        nifty_intra_raw = self.data_cache.get("_nifty", {}).get("intra", pd.DataFrame())
        if not nifty_intra_raw.empty:
            nifty_ist = compute_vwap(_to_ist(nifty_intra_raw))
            nifty_today = nifty_ist[nifty_ist.index.date == self.signal_date]
            if nifty_today.empty:
                nifty_today = nifty_ist.tail(20)
        else:
            nifty_today = pd.DataFrame()

        # Nifty state
        from common.market import nifty_making_new_lows
        nifty_new_lows = nifty_making_new_lows(nifty_today) if not nifty_today.empty else False
        nifty_regime, beta_scale = detect_nifty_regime(nifty_daily)
        nifty_state = {
            "regime": nifty_regime,
            "new_lows": nifty_new_lows,
            "beta_scale": beta_scale,
            "nifty_ist": nifty_today,
        }

        # Sector data
        sectors = set(cfg["sector"] for cfg in TICKERS.values() if cfg.get("sector"))
        sector_data = {}
        for sec in sectors:
            sec_daily = self._slice_daily(sec, self.signal_date)
            if not sec_daily.empty:
                sector_data[sec] = sec_daily

        # Evaluate each ticker
        ticker_states = []
        for sym in self.symbols:
            daily_df = self._slice_daily(sym, self.signal_date)
            intra_df = self.data_cache.get(sym, {}).get("intra", pd.DataFrame())

            state = evaluate_btst(
                sym, intra_df, daily_df, nifty_state, vix_info, sector_data,
                nifty_daily=nifty_daily, news_sentiment=None,
            )
            ticker_states.append(state)

        # Rank
        ticker_states = rank_btst_signals(ticker_states)

        # Collect actionable signals
        for s in ticker_states:
            if s["signal"] not in ("STRONG_BUY", "BUY"):
                continue

            regime = s.get("symbol_regime", {})
            sig = BTSTSignalResult(
                symbol=s["symbol"],
                name=s.get("name", s["symbol"]),
                signal_tier=s["signal"],
                entry_price=s["entry_price"],
                target_price=s["target_price"],
                stop_price=s["stop_price"],
                target_pct=s["target_pct"],
                stop_pct=s["stop_pct"],
                composite_score=s["composite_score"],
                overnight_wr=s["overnight_wr"],
                convergence_score=s.get("convergence_score", 0),
                convergence_detail=s.get("convergence_detail", ""),
                action_text=s.get("action_text", ""),
                regime_trend=regime.get("trend", ""),
                regime_weekly=regime.get("weekly_trend", ""),
            )
            self.all_signals.append(sig)

        print(f"    Signals generated: {len(self.all_signals)} actionable "
              f"(STRONG_BUY + BUY)")

    # ── Signal Validation ─────────────────────────────────────────────

    def validate_signals(self):
        """Validate all signals against actual next-day(s) price action."""
        next_days = self._get_next_trading_days(self.signal_date, n=MAX_HOLD_DAYS)
        if not next_days:
            print(f"    No trading days after {self.signal_date} — cannot validate")
            for sig in self.all_signals:
                sig.outcome = "NO_TRADE"
                sig.exit_reason = "no_data"
            return

        print(f"\n  Validating {len(self.all_signals)} signals against "
              f"{', '.join(str(d) for d in next_days)}...")

        for sig in self.all_signals:
            self._validate_one(sig, next_days)

        # Summary
        outcomes = {}
        for s in self.all_signals:
            outcomes[s.outcome] = outcomes.get(s.outcome, 0) + 1
        print(f"    Results: {outcomes}")

    def _validate_one(self, sig, next_days):
        """Validate a single BTST signal."""
        entry = sig.entry_price
        target = sig.target_price
        stop = sig.stop_price

        if entry <= 0 or target <= 0 or stop <= 0:
            sig.outcome = "NO_TRADE"
            sig.exit_reason = "invalid_levels"
            return

        best_price = entry
        worst_price = entry

        # Day 1: next trading day
        d1 = next_days[0] if len(next_days) >= 1 else None
        if d1:
            bar = self._get_daily_bar(sig.symbol, d1)
            if bar is not None:
                sig.day1_high = float(bar["High"])
                sig.day1_low = float(bar["Low"])
                sig.day1_close = float(bar["Close"])
                sig.next_open = float(bar["Open"])
                sig.next_high = sig.day1_high
                sig.next_low = sig.day1_low
                sig.next_close = sig.day1_close

                # Gap calculation
                sig.gap_pct = round((sig.next_open - entry) / entry * 100, 2)

                # Check if gap-down breaches stop at open
                if sig.next_open <= stop:
                    sig.outcome = "GAP_STOP"
                    sig.exit_price = sig.next_open
                    sig.exit_reason = "gap_stop"
                    sig.exit_day = 1
                    sig.pnl_pct = round((sig.exit_price - entry) / entry * 100, 2)
                    sig.mfe = sig.next_open  # never got better
                    sig.mae = sig.next_open
                    sig.mfe_pct = sig.gap_pct
                    sig.mae_pct = abs(sig.gap_pct)
                    return

                best_price = max(best_price, sig.day1_high)
                worst_price = min(worst_price, sig.day1_low)

                # Check stop hit (intraday)
                if sig.day1_low <= stop:
                    sig.outcome = "LOSS"
                    sig.exit_price = stop
                    sig.exit_reason = "stop"
                    sig.exit_day = 1
                    self._compute_mfe_mae(sig, entry, best_price, worst_price)
                    return

                # Check target hit (intraday)
                if sig.day1_high >= target:
                    sig.outcome = "WIN"
                    sig.exit_price = target
                    sig.exit_reason = "target"
                    sig.exit_day = 1
                    self._compute_mfe_mae(sig, entry, best_price, worst_price)
                    return

        # Day 2: second trading day (if max hold allows)
        d2 = next_days[1] if len(next_days) >= 2 else None
        if d2 and MAX_HOLD_DAYS >= 2:
            bar = self._get_daily_bar(sig.symbol, d2)
            if bar is not None:
                sig.day2_high = float(bar["High"])
                sig.day2_low = float(bar["Low"])
                sig.day2_close = float(bar["Close"])

                best_price = max(best_price, sig.day2_high)
                worst_price = min(worst_price, sig.day2_low)

                # Check stop hit
                if sig.day2_low <= stop:
                    sig.outcome = "LOSS"
                    sig.exit_price = stop
                    sig.exit_reason = "stop"
                    sig.exit_day = 2
                    self._compute_mfe_mae(sig, entry, best_price, worst_price)
                    return

                # Check target hit
                if sig.day2_high >= target:
                    sig.outcome = "WIN"
                    sig.exit_price = target
                    sig.exit_reason = "target"
                    sig.exit_day = 2
                    self._compute_mfe_mae(sig, entry, best_price, worst_price)
                    return

                # EOD Day 2 exit
                sig.exit_price = sig.day2_close
                sig.exit_reason = "eod_d2"
                sig.exit_day = 2
                sig.pnl_pct = round((sig.exit_price - entry) / entry * 100, 2)
                self._compute_mfe_mae(sig, entry, best_price, worst_price)
                sig.outcome = "WIN" if sig.pnl_pct > 0 else "LOSS"
                if sig.pnl_pct <= 0 and sig.mfe_of_target >= 50:
                    sig.outcome = "PARTIAL"
                return

        # Only Day 1 available, EOD exit
        if d1 and sig.day1_close > 0:
            sig.exit_price = sig.day1_close
            sig.exit_reason = "eod_d1"
            sig.exit_day = 1
            sig.pnl_pct = round((sig.exit_price - entry) / entry * 100, 2)
            self._compute_mfe_mae(sig, entry, best_price, worst_price)
            sig.outcome = "WIN" if sig.pnl_pct > 0 else "LOSS"
            if sig.pnl_pct <= 0 and sig.mfe_of_target >= 50:
                sig.outcome = "PARTIAL"
            return

        sig.outcome = "NO_TRADE"
        sig.exit_reason = "no_data"

    @staticmethod
    def _compute_mfe_mae(sig, entry, best_price, worst_price):
        """Compute MFE/MAE metrics."""
        if entry <= 0:
            return
        sig.mfe = best_price
        sig.mae = worst_price
        sig.mfe_pct = round((best_price - entry) / entry * 100, 2)
        sig.mae_pct = round((entry - worst_price) / entry * 100, 2)
        sig.pnl_pct = round((sig.exit_price - entry) / entry * 100, 2)

        target_dist = sig.target_price - entry
        if target_dist > 0:
            sig.mfe_of_target = round((best_price - entry) / target_dist * 100, 1)

    # ── Report Generation ─────────────────────────────────────────────

    def generate_report(self):
        """Generate markdown backtest report with signal-by-signal narratives."""
        lines = []
        sd = self.signal_date.isoformat()
        next_days = self._get_next_trading_days(self.signal_date, n=MAX_HOLD_DAYS)

        lines.append(f"# BTST Backtest — Signal Date: {sd}\n")
        if next_days:
            lines.append(f"Hold period: {', '.join(str(d) for d in next_days)} "
                         f"(max {MAX_HOLD_DAYS} trading days)\n")

        # ── Summary ──
        total = len(self.all_signals)
        wins = sum(1 for s in self.all_signals if s.outcome == "WIN")
        losses = sum(1 for s in self.all_signals if s.outcome == "LOSS")
        gap_stops = sum(1 for s in self.all_signals if s.outcome == "GAP_STOP")
        partials = sum(1 for s in self.all_signals if s.outcome == "PARTIAL")
        no_trade = sum(1 for s in self.all_signals if s.outcome == "NO_TRADE")
        traded = total - no_trade
        win_rate = wins / traded * 100 if traded > 0 else 0

        pnl_list = [s.pnl_pct for s in self.all_signals if s.outcome != "NO_TRADE"]
        avg_pnl = np.mean(pnl_list) if pnl_list else 0
        total_pnl = sum(pnl_list)

        lines.append("## Summary\n")
        lines.append(f"- Total signals: {total} | Traded: {traded}")
        lines.append(f"- Wins: {wins} | Losses: {losses} | Gap-stops: {gap_stops} | "
                     f"Partial: {partials} | No-trade: {no_trade}")
        lines.append(f"- Win rate: {win_rate:.0f}% | Avg P&L: {avg_pnl:+.2f}% | "
                     f"Total P&L: {total_pnl:+.2f}%")

        # Gap analysis
        gap_signals = [s for s in self.all_signals if s.gap_pct != 0]
        if gap_signals:
            avg_gap = np.mean([s.gap_pct for s in gap_signals])
            gap_ups = sum(1 for s in gap_signals if s.gap_pct > 0.3)
            gap_downs = sum(1 for s in gap_signals if s.gap_pct < -0.3)
            lines.append(f"- Avg overnight gap: {avg_gap:+.2f}% | "
                         f"Gap-ups: {gap_ups} | Gap-downs: {gap_downs}")
        lines.append("")

        # ── Signal-by-Signal Replay ──
        lines.append("---\n")
        lines.append("## Signal-by-Signal Replay\n")
        lines.append("> For each BTST signal: what the scanner suggested at close, "
                     "and what happened the next day(s).\n")

        for i, sig in enumerate(self.all_signals, 1):
            self._write_signal_narrative(lines, sig, i)

        # ── Overnight Gap Risk Analysis ──
        gap_stops_list = [s for s in self.all_signals if s.outcome == "GAP_STOP"]
        if gap_stops_list:
            lines.append("---\n")
            lines.append("## Overnight Gap Risk\n")
            lines.append("> These signals were stopped out at the open due to a gap-down. "
                         "This is the primary risk of BTST trading.\n")
            lines.append("| Stock | Entry | Next Open | Gap % | Loss |")
            lines.append("|-------|-------|-----------|-------|------|")
            for s in gap_stops_list:
                clean = s.symbol.replace(".NS", "")
                lines.append(f"| {clean} | {s.entry_price:,.2f} | "
                             f"{s.next_open:,.2f} | {s.gap_pct:+.2f}% | "
                             f"{s.pnl_pct:+.2f}% |")
            lines.append("")

        # ── Absurd Target Flags ──
        flagged = [(s, s.target_pct) for s in self.all_signals if s.target_pct > 5.0]
        if flagged:
            lines.append("## Absurd Target Flags\n")
            lines.append("> Targets >5% may be too aggressive for overnight holds.\n")
            lines.append("| Stock | Entry | Target | Target % | Outcome | MFE % of Target |")
            lines.append("|-------|-------|--------|----------|---------|-----------------|")
            for s, tgt_pct in flagged:
                clean = s.symbol.replace(".NS", "")
                mfe_t = f"{s.mfe_of_target:.0f}%" if s.outcome != "NO_TRADE" else "—"
                lines.append(f"| {clean} | {s.entry_price:,.2f} | "
                             f"{s.target_price:,.2f} | {tgt_pct:.1f}% | "
                             f"{s.outcome} | {mfe_t} |")
            lines.append("")

        return "\n".join(lines)

    def _write_signal_narrative(self, lines, sig, idx):
        """Write a detailed narrative for a single BTST signal."""
        clean = sig.symbol.replace(".NS", "")

        icon = {
            "WIN": "✅", "LOSS": "❌", "GAP_STOP": "💥",
            "PARTIAL": "⚠️", "NO_TRADE": "⏭️",
        }.get(sig.outcome, "❓")

        lines.append(f"### Signal {idx}: {clean} — {sig.signal_tier} {icon}\n")

        # What the scanner said
        lines.append(f"**Scanner said** ({sig.signal_tier}, composite {sig.composite_score:.0%}):")
        lines.append(f"- BUY {clean} at close ₹{sig.entry_price:,.2f}")
        lines.append(f"- Target: ₹{sig.target_price:,.2f} (+{sig.target_pct:.1f}%) | "
                     f"Stop: ₹{sig.stop_price:,.2f} (-{sig.stop_pct:.1f}%)")
        lines.append(f"- Overnight WR: {sig.overnight_wr:.0f}% | "
                     f"Convergence: {sig.convergence_score}%")
        if sig.convergence_detail:
            lines.append(f"- Convergence: {sig.convergence_detail}")
        if sig.regime_trend:
            lines.append(f"- Regime: {sig.regime_trend} trend, weekly {sig.regime_weekly}")
        lines.append("")

        # What actually happened
        lines.append("**What happened:**")

        if sig.outcome == "NO_TRADE":
            lines.append("- ⏭️ **NO DATA** — Could not validate (no next-day data)")
            lines.append("")
            return

        # Overnight gap
        gap_icon = "📈" if sig.gap_pct > 0 else "📉" if sig.gap_pct < 0 else "➡️"
        lines.append(f"- {gap_icon} Next-day open: ₹{sig.next_open:,.2f} "
                     f"(gap {sig.gap_pct:+.2f}%)")

        # Day 1 price action
        if sig.day1_high > 0:
            lines.append(f"- Day 1: High ₹{sig.day1_high:,.2f} | "
                         f"Low ₹{sig.day1_low:,.2f} | "
                         f"Close ₹{sig.day1_close:,.2f}")

        # Day 2 price action (if used)
        if sig.day2_high > 0:
            lines.append(f"- Day 2: High ₹{sig.day2_high:,.2f} | "
                         f"Low ₹{sig.day2_low:,.2f} | "
                         f"Close ₹{sig.day2_close:,.2f}")

        # MFE/MAE
        lines.append(f"- Best price (MFE): ₹{sig.mfe:,.2f} ({sig.mfe_pct:+.2f}%) — "
                     f"reached {sig.mfe_of_target:.0f}% of target distance")
        lines.append(f"- Worst drawdown (MAE): ₹{sig.mae:,.2f} ({sig.mae_pct:-.2f}%)")

        # Exit
        if sig.exit_reason == "target":
            lines.append(f"- ✅ **TARGET HIT** on Day {sig.exit_day} at "
                         f"₹{sig.exit_price:,.2f} ({sig.pnl_pct:+.2f}%)")
        elif sig.exit_reason == "stop":
            lines.append(f"- ❌ **STOPPED OUT** on Day {sig.exit_day} at "
                         f"₹{sig.exit_price:,.2f} ({sig.pnl_pct:+.2f}%)")
        elif sig.exit_reason == "gap_stop":
            lines.append(f"- 💥 **GAP-DOWN STOP** — opened below stop at "
                         f"₹{sig.exit_price:,.2f} ({sig.pnl_pct:+.2f}%)")
        elif sig.exit_reason.startswith("eod"):
            pnl_icon = "📈" if sig.pnl_pct > 0 else "📉"
            lines.append(f"- {pnl_icon} **EOD EXIT** Day {sig.exit_day} at "
                         f"₹{sig.exit_price:,.2f} ({sig.pnl_pct:+.2f}%)")

        # Verdict
        lines.append("")
        if sig.outcome == "WIN":
            lines.append(f"> **VERDICT: WIN** — BTST trade worked. "
                         f"P&L: {sig.pnl_pct:+.2f}%")
        elif sig.outcome == "LOSS":
            lines.append(f"> **VERDICT: LOSS** — Setup failed. "
                         f"P&L: {sig.pnl_pct:+.2f}%. "
                         f"MFE reached {sig.mfe_of_target:.0f}% of target.")
        elif sig.outcome == "GAP_STOP":
            lines.append(f"> **VERDICT: GAP-DOWN STOP** — Overnight gap wiped the trade. "
                         f"This is the core BTST risk. P&L: {sig.pnl_pct:+.2f}%")
        elif sig.outcome == "PARTIAL":
            lines.append(f"> **VERDICT: PARTIAL** — Price moved {sig.mfe_of_target:.0f}% "
                         f"toward target but reversed. Direction was right, "
                         f"target may have been too ambitious.")

        # Flag absurd targets
        if sig.target_pct > 5.0 and sig.outcome != "WIN":
            lines.append(f">\n> ⚠️ **FLAG**: Target was {sig.target_pct:.1f}% — "
                         f"may be too aggressive for overnight hold.")
        lines.append("")

    # ── Main Runner ───────────────────────────────────────────────────

    def run(self):
        """Full backtest pipeline for a single signal day."""
        print(f"\n{'='*60}")
        print(f"  BTST BACKTEST — Signal Date: {self.signal_date}")
        print(f"{'='*60}")

        self.fetch_all_data()

        # Verify signal date has data
        nifty_bar = self._get_daily_bar("_nifty", self.signal_date)
        if nifty_bar is None:
            print(f"\n  [WARN] No data for {self.signal_date} — "
                  f"may be a holiday or weekend. Skipping.")
            return None

        # Generate signals
        self.generate_signals()

        if not self.all_signals:
            print("  No actionable BTST signals generated.")
            return self.generate_report()

        # Validate
        self.validate_signals()

        # Report
        report = self.generate_report()
        return report


# ── Multi-Day Runner ──────────────────────────────────────────────────────

def run_multi_day(start_date, end_date, capital=1000000, config=None):
    """Run BTST backtest across a date range."""
    all_signals = []
    day_summaries = []

    current = start_date
    while current <= end_date:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        engine = BTSTBacktestEngine(current, capital=capital, config=config)
        report = engine.run()
        if report is None:
            current += timedelta(days=1)
            continue

        all_signals.extend(engine.all_signals)

        traded = [s for s in engine.all_signals if s.outcome != "NO_TRADE"]
        wins = sum(1 for s in traded if s.outcome == "WIN")
        losses = sum(1 for s in traded if s.outcome in ("LOSS", "GAP_STOP"))
        wr = wins / len(traded) * 100 if traded else 0
        avg_pnl = np.mean([s.pnl_pct for s in traded]) if traded else 0

        day_summaries.append({
            "date": current,
            "signals": len(engine.all_signals),
            "traded": len(traded),
            "wins": wins,
            "losses": losses,
            "win_rate": wr,
            "avg_pnl": avg_pnl,
        })

        # Save individual report
        BTST_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = BTST_REPORT_DIR / f"backtest_{current.isoformat()}.md"
        with open(path, "w") as f:
            f.write(report + "\n")
        print(f"  Day report saved: {path}")

        current += timedelta(days=1)

    if not day_summaries:
        print("\n  No trading days found in range.")
        return

    # ── Aggregate Report ──
    lines = []
    lines.append(f"# BTST Backtest — {start_date} to {end_date}\n")
    lines.append("## Per-Day Summary\n")
    lines.append("| Date | Signals | Traded | Wins | Losses | Win Rate | Avg P&L |")
    lines.append("|------|---------|--------|------|--------|----------|---------|")
    for d in day_summaries:
        lines.append(
            f"| {d['date']} | {d['signals']} | {d['traded']} | "
            f"{d['wins']} | {d['losses']} | {d['win_rate']:.0f}% | "
            f"{d['avg_pnl']:+.2f}% |"
        )
    lines.append("")

    # Overall
    traded_all = [s for s in all_signals if s.outcome != "NO_TRADE"]
    wins_all = sum(1 for s in traded_all if s.outcome == "WIN")
    losses_all = sum(1 for s in traded_all if s.outcome in ("LOSS", "GAP_STOP"))
    gap_stops = sum(1 for s in traded_all if s.outcome == "GAP_STOP")
    wr_all = wins_all / len(traded_all) * 100 if traded_all else 0
    pnl_all = [s.pnl_pct for s in traded_all]
    avg_pnl_all = np.mean(pnl_all) if pnl_all else 0
    total_pnl = sum(pnl_all)

    lines.append("## Overall Summary\n")
    lines.append(f"- Trading days: {len(day_summaries)}")
    lines.append(f"- Total signals: {len(all_signals)} | Traded: {len(traded_all)}")
    lines.append(f"- Wins: {wins_all} | Losses: {losses_all} | Gap-stops: {gap_stops}")
    lines.append(f"- Win rate: {wr_all:.1f}% | Avg P&L: {avg_pnl_all:+.2f}% | "
                 f"Total P&L: {total_pnl:+.2f}%")

    # Gap risk
    gap_signals = [s for s in traded_all if s.gap_pct != 0]
    if gap_signals:
        avg_gap = np.mean([s.gap_pct for s in gap_signals])
        worst_gap = min(s.gap_pct for s in gap_signals)
        lines.append(f"- Avg overnight gap: {avg_gap:+.2f}% | "
                     f"Worst gap: {worst_gap:+.2f}%")
    lines.append("")

    aggregate_report = "\n".join(lines)

    BTST_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    agg_path = BTST_REPORT_DIR / f"backtest_{start_date.isoformat()}_to_{end_date.isoformat()}.md"
    with open(agg_path, "w") as f:
        f.write(aggregate_report + "\n")
    print(f"\n  Aggregate report saved: {agg_path}")

    print(f"\n{'='*60}")
    print(f"  BTST AGGREGATE — {start_date} to {end_date}")
    print(f"{'='*60}")
    print(f"  Days: {len(day_summaries)} | Signals: {len(all_signals)} | "
          f"Traded: {len(traded_all)}")
    print(f"  Wins: {wins_all} | Losses: {losses_all} | Gap-stops: {gap_stops}")
    print(f"  Win Rate: {wr_all:.1f}% | Total P&L: {total_pnl:+.2f}%")


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BTST Scanner Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python -m btst.backtest --date 2026-02-25
  python -m btst.backtest --start 2026-02-17 --end 2026-02-25""",
    )
    parser.add_argument("--date", type=str, help="Single signal date (YYYY-MM-DD)")
    parser.add_argument("--start", type=str, help="Start date for range")
    parser.add_argument("--end", type=str, help="End date for range")
    parser.add_argument("--capital", type=int, default=1000000,
                        help="Starting capital (default: 1000000)")
    args = parser.parse_args()

    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}
    config.setdefault("global", {})["capital"] = args.capital

    if args.date:
        target = date.fromisoformat(args.date)
        engine = BTSTBacktestEngine(target, capital=args.capital, config=config)
        report = engine.run()
        if report:
            BTST_REPORT_DIR.mkdir(parents=True, exist_ok=True)
            path = BTST_REPORT_DIR / f"backtest_{target.isoformat()}.md"
            with open(path, "w") as f:
                f.write(report + "\n")
            print(f"\n  Report saved: {path}")
    elif args.start and args.end:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        run_multi_day(start, end, capital=args.capital, config=config)
    else:
        parser.error("Provide --date or both --start and --end")


if __name__ == "__main__":
    main()
