#!/usr/bin/env python3
"""
Intraday Scanner Backtest

Replays historical days through the intraday scanner's phase-aware pipeline:
  1. Post-market T-1 — tomorrow's watchlist (gap scenarios for T)
  2. Pre-market T — gap scenarios with daily data only
  3. Live scans at 09:30, 11:00, 13:00, 14:30 on T
  4. Validates all signals against actual T data (entry hit, target/stop, MFE/MAE)

Pre-live (9:00-9:15) is skipped — yfinance doesn't store pre-market auction data.

Usage:
    python -m intraday.backtest --date 2026-02-20
    python -m intraday.backtest --start 2026-02-10 --end 2026-02-20
    python -m intraday.backtest --date 2026-02-20 --capital 500000
    python -m intraday.backtest --date 2026-02-20 --llm
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

from common.data import fetch_yf, TICKERS, BENCHMARK, PROJECT_ROOT, CONFIG_PATH
from common.indicators import compute_atr, compute_vwap, _to_ist, classify_gaps
from common.market import fetch_india_vix, detect_nifty_regime
from intraday.regime import (
    classify_day_type, reclassify_day_type, classify_symbol_regime,
    classify_month_period, compute_dow_month_stats, DOW_NAMES,
)
from intraday.scanner import (
    _build_gap_scenarios, evaluate_symbol, run_pre_market_scan,
    run_post_market_scan, _run_live_scan, rank_signals,
    INTRADAY_REPORT_DIR, LONG_ONLY,
)
from intraday.explanations import _action_label

warnings.filterwarnings("ignore")

IST = ZoneInfo("Asia/Kolkata")


# ── Signal result dataclass ───────────────────────────────────────────────

@dataclass
class SignalResult:
    """Validated backtest signal."""
    symbol: str
    name: str
    phase: str           # "post_market_t-1", "pre_market", "live_09:30", etc.
    strategy: str
    direction: str       # "long" or "short"
    entry_price: float
    target_price: float
    stop_price: float
    score: float
    signal_tier: str     # "STRONG", "ACTIVE", "WATCH", "AVOID"
    rr_ratio: float

    # Gap scenario fields (pre/post-market signals)
    predicted_scenario: str = ""   # "gap_up", "gap_down", "flat"
    actual_scenario: str = ""
    scenario_correct: bool = False

    # Validation results
    outcome: str = "pending"       # "CORRECT", "WRONG", "NO_ENTRY", "CLOSE_CALL"
    entry_hit: bool = False
    entry_hit_time: str = ""
    target_hit: bool = False
    target_hit_time: str = ""
    stop_hit: bool = False
    stop_hit_time: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""          # "target", "stop", "eod", "no_entry"

    # MFE / MAE
    mfe: float = 0.0              # max favorable excursion (price)
    mae: float = 0.0              # max adverse excursion (price)
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    mfe_of_target: float = 0.0    # MFE as % of target distance

    # Timing
    bars_to_resolution: int = 0

    # Scanner reasoning context
    reason: str = ""               # why scanner generated this signal
    convergence: str = ""          # convergence summary (e.g. "4/6 MACD,RSI,VWAP,imbalance")
    regime: str = ""               # market regime at time of signal

    # Price journey tracking
    mfe_time: str = ""             # when MFE was reached
    mae_time: str = ""             # when MAE was reached


# ── Backtest Engine ───────────────────────────────────────────────────────

class IntradayBacktestEngine:
    """Backtest engine for the intraday scanner."""

    def __init__(self, target_date, capital=1000000, config=None):
        self.target_date = target_date  # date object — the day we're testing
        self.capital = capital
        self.config = config or {}
        self.all_signals: list[SignalResult] = []
        self.data_cache: dict = {}  # {symbol: {"daily": df, "intra": df}}
        self.symbols = list(TICKERS.keys())

    # ── Data Fetching ──────────────────────────────────────────────────

    def fetch_all_data(self):
        """Pre-fetch daily (6mo) + intraday (5min, 5d) data for all tickers + Nifty."""
        print(f"  Fetching data for {len(self.symbols)} tickers + Nifty...")

        # Nifty
        nifty_daily = fetch_yf(BENCHMARK, period="6mo", interval="1d")
        nifty_intra = fetch_yf(BENCHMARK, period="5d", interval="5m")
        self.data_cache["_nifty"] = {"daily": nifty_daily, "intra": nifty_intra}

        # VIX (single fetch, reused across phases)
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
        # Daily index is date-like (no time component)
        mask = raw.index.date <= up_to_date
        return raw[mask].copy()

    def _slice_intraday(self, sym, up_to_time):
        """Intraday bars up to a specific datetime (IST).

        Args:
            up_to_time: datetime with IST timezone, or naive datetime to be
                        treated as IST.
        """
        raw = self.data_cache.get(sym, {}).get("intra", pd.DataFrame())
        if raw.empty:
            return raw
        # Convert to IST for slicing
        ist_df = _to_ist(raw)
        if up_to_time.tzinfo is None:
            up_to_time = up_to_time.replace(tzinfo=IST)
        mask = ist_df.index <= up_to_time
        # Return the original (UTC) rows that correspond to the IST mask
        # but since strategies expect UTC input (they call _to_ist themselves),
        # we need to return the raw data sliced equivalently
        sliced = raw.iloc[:mask.sum()].copy()
        return sliced

    def _build_data_override(self, daily_cutoff, intra_cutoff=None):
        """Build a data_override dict for scanner functions.

        Args:
            daily_cutoff: date object — daily data up to this date (inclusive)
            intra_cutoff: datetime — intraday data up to this time, or None
        """
        override = {
            "_vix": self.data_cache.get("_vix", (None, "normal")),
            "_inst_flow": "neutral",
            "_news": {},
        }

        # Nifty
        nifty_daily = self._slice_daily("_nifty", daily_cutoff)
        if intra_cutoff:
            nifty_intra = self._slice_intraday("_nifty", intra_cutoff)
        else:
            nifty_intra = pd.DataFrame()
        override["_nifty"] = {"daily": nifty_daily, "intra": nifty_intra}

        # Tickers
        for sym in self.symbols:
            sym_daily = self._slice_daily(sym, daily_cutoff)
            if intra_cutoff:
                sym_intra = self._slice_intraday(sym, intra_cutoff)
            else:
                sym_intra = pd.DataFrame()
            override[sym] = {"daily": sym_daily, "intra": sym_intra}

        # Sector indices
        sectors = set(cfg["sector"] for cfg in TICKERS.values() if cfg.get("sector"))
        for sec in sectors:
            override[sec] = {"daily": self._slice_daily(sec, daily_cutoff)}

        return override

    # ── Phase Simulations ─────────────────────────────────────────────

    def _get_prev_trading_day(self):
        """Get the trading day before target_date."""
        nifty_daily = self.data_cache.get("_nifty", {}).get("daily", pd.DataFrame())
        if nifty_daily.empty:
            # Approximate: go back 1 day, skip weekends
            d = self.target_date - timedelta(days=1)
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            return d
        # Find the last trading day before target_date
        dates = sorted(set(nifty_daily.index.date))
        prev_dates = [d for d in dates if d < self.target_date]
        return prev_dates[-1] if prev_dates else self.target_date - timedelta(days=1)

    def run_post_market_t_minus_1(self):
        """Simulate post-market on T-1: tomorrow's watchlist = predictions for T."""
        t_minus_1 = self._get_prev_trading_day()
        print(f"\n  Phase 1: Post-market T-1 ({t_minus_1})...")

        mock_time = datetime.combine(t_minus_1, dtime(15, 30), tzinfo=IST)
        # Daily data up to T-1, full T-1 intraday
        intra_cutoff = datetime.combine(t_minus_1, dtime(15, 30), tzinfo=IST)
        data_override = self._build_data_override(t_minus_1, intra_cutoff)

        result = run_post_market_scan(
            self.config, self.symbols,
            now_ist=mock_time, data_override=data_override, skip_llm=True,
        )
        if result is None:
            return
        session_summaries, tomorrow_setups = result

        # Record gap scenario signals — only actionable (STRONG/ACTIVE)
        actionable = [s for s in tomorrow_setups
                      if s.get("signal") in ("STRONG", "ACTIVE")]
        for setup in actionable:
            best = setup.get("best_scenario", {})
            if not best:
                continue
            sig = SignalResult(
                symbol=setup["symbol"],
                name=setup.get("name", setup["symbol"]),
                phase="post_market_t-1",
                strategy=best.get("strategy", ""),
                direction=best.get("direction", "long"),
                entry_price=best.get("entry", 0),
                target_price=best.get("target", 0),
                stop_price=best.get("stop", 0),
                score=best.get("probability", 0) / 100,
                signal_tier=setup.get("signal", "WATCH"),
                rr_ratio=best.get("rr", 0),
                predicted_scenario=best.get("type", ""),
            )
            self.all_signals.append(sig)

        print(f"    Tomorrow's watchlist: {len(tomorrow_setups)} total | "
              f"Actionable (STRONG/ACTIVE): {len(actionable)}")

    def run_pre_market_t(self):
        """Simulate pre-market on T morning (before 9:00)."""
        print(f"\n  Phase 2: Pre-market T ({self.target_date})...")

        mock_time = datetime.combine(self.target_date, dtime(8, 0), tzinfo=IST)
        # Daily data up to T-1 (same as post-market), no intraday
        t_minus_1 = self._get_prev_trading_day()
        data_override = self._build_data_override(t_minus_1, intra_cutoff=None)

        setups = run_pre_market_scan(
            self.config, self.symbols,
            now_ist=mock_time, data_override=data_override, skip_llm=True,
        )

        # Only actionable signals — and skip duplicates already in post-market
        existing_keys = {(s.symbol, s.strategy, s.direction)
                         for s in self.all_signals}
        actionable = [s for s in (setups or [])
                      if s.get("signal") in ("STRONG", "ACTIVE")]
        added = 0
        for setup in actionable:
            best = setup.get("best_scenario", {})
            if not best:
                continue
            key = (setup["symbol"], best.get("strategy", ""),
                   best.get("direction", "long"))
            if key in existing_keys:
                continue  # already have this from post-market T-1
            sig = SignalResult(
                symbol=setup["symbol"],
                name=setup.get("name", setup["symbol"]),
                phase="pre_market",
                strategy=best.get("strategy", ""),
                direction=best.get("direction", "long"),
                entry_price=best.get("entry", 0),
                target_price=best.get("target", 0),
                stop_price=best.get("stop", 0),
                score=best.get("probability", 0) / 100,
                signal_tier=setup.get("signal", "WATCH"),
                rr_ratio=best.get("rr", 0),
                predicted_scenario=best.get("type", ""),
            )
            self.all_signals.append(sig)
            existing_keys.add(key)
            added += 1

        print(f"    Pre-market setups: {len(setups or [])} total | "
              f"Actionable: {len(actionable)} | New (not in T-1): {added}")

    def run_live_scan_at(self, scan_time):
        """Simulate a live scan at a specific time on T.

        Args:
            scan_time: time object (e.g., dtime(9, 30))
        """
        mock_dt = datetime.combine(self.target_date, scan_time, tzinfo=IST)
        label = scan_time.strftime("%H:%M")
        print(f"\n  Phase: Live scan at {label}...")

        t_minus_1 = self._get_prev_trading_day()
        # Daily data up to T-1 (strategies derive "today" from intraday index)
        # Intraday data up to mock_dt
        data_override = self._build_data_override(t_minus_1, mock_dt)

        candidates = _run_live_scan(
            self.config, self.symbols,
            now_ist=mock_dt, data_override=data_override, skip_llm=True,
        )

        # Only collect STRONG/ACTIVE — these are the signals the scanner
        # would actually recommend for trading
        n_total = len(candidates or [])
        n_actionable = 0
        for c in (candidates or []):
            if c.get("signal") not in ("STRONG", "ACTIVE"):
                continue
            sig = SignalResult(
                symbol=c["symbol"],
                name=c.get("name", c["symbol"]),
                phase=f"live_{label}",
                strategy=c.get("strategy", ""),
                direction=c.get("direction", "long"),
                entry_price=c.get("entry_price", 0),
                target_price=c.get("target_price", 0),
                stop_price=c.get("stop_price", 0),
                score=c.get("score", 0),
                signal_tier=c.get("signal", "WATCH"),
                rr_ratio=c.get("rr_ratio", 0),
                reason=c.get("signal_reason", ""),
                convergence=c.get("convergence_detail", ""),
                regime=c.get("symbol_regime", {}).get("trend", "") if isinstance(c.get("symbol_regime"), dict) else str(c.get("symbol_regime", "")),
            )
            self.all_signals.append(sig)
            n_actionable += 1

        print(f"    Candidates: {n_total} | "
              f"Actionable (STRONG/ACTIVE): {n_actionable}")

    # ── Signal Validation ─────────────────────────────────────────────

    def _get_actual_day_data(self, sym):
        """Get full intraday data for target_date in IST."""
        raw = self.data_cache.get(sym, {}).get("intra", pd.DataFrame())
        if raw.empty:
            return pd.DataFrame()
        ist_df = _to_ist(raw)
        day_bars = ist_df[ist_df.index.date == self.target_date]
        return day_bars

    def _determine_actual_gap(self, sym):
        """Determine the actual gap scenario for T.

        Returns: "gap_up", "gap_down", or "flat"
        """
        daily = self.data_cache.get(sym, {}).get("daily", pd.DataFrame())
        if daily.empty or len(daily) < 2:
            return "flat"

        day_bars = self._get_actual_day_data(sym)
        if day_bars.empty:
            return "flat"

        # Previous close from daily data up to T-1
        t_minus_1 = self._get_prev_trading_day()
        daily_before = daily[daily.index.date <= t_minus_1]
        if daily_before.empty:
            return "flat"

        prev_close = float(daily_before["Close"].iloc[-1])
        actual_open = float(day_bars["Open"].iloc[0])

        if prev_close <= 0:
            return "flat"

        gap_pct = (actual_open - prev_close) / prev_close * 100
        if gap_pct > 0.3:
            return "gap_up"
        elif gap_pct < -0.3:
            return "gap_down"
        return "flat"

    def validate_signals(self):
        """Validate all collected signals against actual T data."""
        print(f"\n  Validating {len(self.all_signals)} signals against actual data...")

        for sig in self.all_signals:
            day_bars = self._get_actual_day_data(sig.symbol)
            if day_bars.empty:
                sig.outcome = "NO_ENTRY"
                sig.exit_reason = "no_data"
                continue

            # Gap scenario validation (pre/post-market signals)
            if sig.predicted_scenario:
                sig.actual_scenario = self._determine_actual_gap(sig.symbol)
                sig.scenario_correct = (sig.predicted_scenario == sig.actual_scenario)

            # Determine which bars to use for walk-forward
            if sig.phase.startswith("live_"):
                # Only use bars after the scan time
                time_str = sig.phase.split("_")[1]
                h, m = int(time_str.split(":")[0]), int(time_str.split(":")[1])
                scan_cutoff = datetime.combine(
                    self.target_date, dtime(h, m), tzinfo=IST,
                )
                forward_bars = day_bars[day_bars.index > scan_cutoff]
            else:
                # Pre/post-market signals: evaluate from market open
                forward_bars = day_bars

            if forward_bars.empty:
                sig.outcome = "NO_ENTRY"
                sig.exit_reason = "no_bars_after_signal"
                continue

            self._walk_forward(sig, forward_bars)

        # Summary
        outcomes = {}
        for s in self.all_signals:
            outcomes[s.outcome] = outcomes.get(s.outcome, 0) + 1
        print(f"    Results: {outcomes}")

    def _walk_forward(self, sig, bars):
        """Walk forward through bars to validate a signal.

        Checks: entry hit → target/stop → EOD exit.
        """
        entry = sig.entry_price
        target = sig.target_price
        stop = sig.stop_price
        direction = sig.direction

        if entry <= 0 or target <= 0 or stop <= 0:
            sig.outcome = "NO_ENTRY"
            sig.exit_reason = "invalid_levels"
            return

        entry_found = False
        best_price = entry  # for MFE
        worst_price = entry  # for MAE
        best_time = ""       # when MFE reached
        worst_time = ""      # when MAE reached
        bars_count = 0

        for idx, row in bars.iterrows():
            high = float(row["High"])
            low = float(row["Low"])
            close = float(row["Close"])
            bars_count += 1

            # Check entry hit
            if not entry_found:
                if direction == "long" and low <= entry:
                    entry_found = True
                    sig.entry_hit = True
                    sig.entry_hit_time = str(idx)
                elif direction == "short" and high >= entry:
                    entry_found = True
                    sig.entry_hit = True
                    sig.entry_hit_time = str(idx)
                continue

            # After entry: track MFE/MAE and check target/stop
            if direction == "long":
                if high > best_price:
                    best_price = high
                    best_time = str(idx)
                if low < worst_price:
                    worst_price = low
                    worst_time = str(idx)

                # Check stop first (conservative: assumes worst case within bar)
                if low <= stop:
                    sig.stop_hit = True
                    sig.stop_hit_time = str(idx)
                    sig.exit_price = stop
                    sig.exit_reason = "stop"
                    sig.bars_to_resolution = bars_count
                    break
                if high >= target:
                    sig.target_hit = True
                    sig.target_hit_time = str(idx)
                    sig.exit_price = target
                    sig.exit_reason = "target"
                    sig.bars_to_resolution = bars_count
                    break
            else:  # short
                if low < best_price:
                    best_price = low
                    best_time = str(idx)
                if high > worst_price:
                    worst_price = high
                    worst_time = str(idx)

                if high >= stop:
                    sig.stop_hit = True
                    sig.stop_hit_time = str(idx)
                    sig.exit_price = stop
                    sig.exit_reason = "stop"
                    sig.bars_to_resolution = bars_count
                    break
                if low <= target:
                    sig.target_hit = True
                    sig.target_hit_time = str(idx)
                    sig.exit_price = target
                    sig.exit_reason = "target"
                    sig.bars_to_resolution = bars_count
                    break

        # If neither hit: EOD exit
        if not sig.target_hit and not sig.stop_hit and entry_found:
            last_close = float(bars["Close"].iloc[-1])
            sig.exit_price = last_close
            sig.exit_reason = "eod"
            sig.bars_to_resolution = bars_count

        # MFE / MAE calculation
        sig.mfe_time = best_time
        sig.mae_time = worst_time
        if entry_found and entry > 0:
            if direction == "long":
                sig.mfe = best_price
                sig.mae = worst_price
                sig.mfe_pct = (best_price - entry) / entry * 100
                sig.mae_pct = (entry - worst_price) / entry * 100
            else:
                sig.mfe = best_price
                sig.mae = worst_price
                sig.mfe_pct = (entry - best_price) / entry * 100
                sig.mae_pct = (worst_price - entry) / entry * 100

            # MFE as % of target distance
            target_dist = abs(target - entry)
            if target_dist > 0:
                if direction == "long":
                    sig.mfe_of_target = (best_price - entry) / target_dist * 100
                else:
                    sig.mfe_of_target = (entry - best_price) / target_dist * 100

        # Classify outcome
        if not sig.entry_hit:
            sig.outcome = "NO_ENTRY"
        elif sig.target_hit:
            sig.outcome = "CORRECT"
        elif sig.stop_hit:
            if sig.mfe_of_target >= 50:
                sig.outcome = "CLOSE_CALL"
            else:
                sig.outcome = "WRONG"
        else:
            # EOD exit
            if direction == "long":
                pnl = sig.exit_price - entry
            else:
                pnl = entry - sig.exit_price
            if pnl > 0:
                sig.outcome = "CORRECT"
            elif sig.mfe_of_target >= 50:
                sig.outcome = "CLOSE_CALL"
            else:
                sig.outcome = "WRONG"

    # ── Report Generation ─────────────────────────────────────────────

    @staticmethod
    def _fmt_time(time_str):
        """Format a raw timestamp string to readable IST time (HH:MM)."""
        if not time_str:
            return "—"
        try:
            dt = pd.Timestamp(time_str)
            if dt.tzinfo is None:
                dt = dt.tz_localize("UTC").tz_convert(IST)
            else:
                dt = dt.tz_convert(IST)
            return dt.strftime("%H:%M")
        except Exception:
            # Fallback: extract time-like portion
            for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(time_str[:25].strip(), fmt)
                    return dt.strftime("%H:%M")
                except Exception:
                    continue
            return time_str[-8:-3] if len(time_str) > 8 else time_str

    @staticmethod
    def _phase_label(phase):
        """Human-readable phase label."""
        labels = {
            "post_market_t-1": "Post-Market (T-1)",
            "pre_market": "Pre-Market",
        }
        if phase in labels:
            return labels[phase]
        if phase.startswith("live_"):
            return f"Live {phase.replace('live_', '')} IST"
        return phase

    def _write_signal_narrative(self, lines, sig, idx):
        """Write a detailed narrative for a single signal."""
        clean_sym = sig.symbol.replace(".NS", "")
        action = _action_label(sig.direction)
        phase_label = self._phase_label(sig.phase)

        # Header with outcome icon
        icon = {"CORRECT": "✅", "WRONG": "❌", "CLOSE_CALL": "⚠️",
                "NO_ENTRY": "⏭️"}.get(sig.outcome, "❓")

        lines.append(f"### Signal {idx}: {clean_sym} — {sig.strategy.upper()} {action} {icon}\n")

        # What the scanner said
        lines.append(f"**Scanner said** ({phase_label}, {sig.signal_tier}):")
        target_pct = abs(sig.target_price - sig.entry_price) / sig.entry_price * 100 if sig.entry_price > 0 else 0
        stop_pct = abs(sig.entry_price - sig.stop_price) / sig.entry_price * 100 if sig.entry_price > 0 else 0
        lines.append(f"- {action} {clean_sym} at ₹{sig.entry_price:,.2f}")
        lines.append(f"- Target: ₹{sig.target_price:,.2f} ({target_pct:+.1f}%) | "
                      f"Stop: ₹{sig.stop_price:,.2f} ({stop_pct:-.1f}%) | "
                      f"RR: {sig.rr_ratio:.1f}")
        lines.append(f"- Score: {sig.score:.0%}")
        if sig.reason:
            lines.append(f"- Reason: {sig.reason}")
        if sig.convergence:
            lines.append(f"- Convergence: {sig.convergence}")
        if sig.regime:
            lines.append(f"- Regime: {sig.regime}")
        if sig.predicted_scenario:
            lines.append(f"- Predicted gap: {sig.predicted_scenario}")
        lines.append("")

        # What actually happened
        lines.append("**What happened:**")

        if not sig.entry_hit:
            lines.append(f"- ⏭️ **NO ENTRY** — Price never reached ₹{sig.entry_price:,.2f}. "
                          f"Signal was never triggered.")
            lines.append("")
            return

        # Entry
        entry_time = self._fmt_time(sig.entry_hit_time)
        lines.append(f"- Entry hit at **{entry_time}** IST")

        # Gap scenario check
        if sig.predicted_scenario:
            sc_icon = "✅" if sig.scenario_correct else "❌"
            lines.append(f"- Gap prediction: {sig.predicted_scenario} → "
                          f"actual {sig.actual_scenario} {sc_icon}")

        # Price journey
        mfe_time = self._fmt_time(sig.mfe_time)
        mae_time = self._fmt_time(sig.mae_time)
        if sig.direction == "long":
            lines.append(f"- Best price (MFE): ₹{sig.mfe:,.2f} "
                          f"({sig.mfe_pct:+.1f}%) at {mfe_time} — "
                          f"reached {sig.mfe_of_target:.0f}% of target distance")
            lines.append(f"- Worst drawdown (MAE): ₹{sig.mae:,.2f} "
                          f"({sig.mae_pct:-.1f}%) at {mae_time}")
        else:
            lines.append(f"- Best price (MFE): ₹{sig.mfe:,.2f} "
                          f"({sig.mfe_pct:+.1f}%) at {mfe_time} — "
                          f"reached {sig.mfe_of_target:.0f}% of target distance")
            lines.append(f"- Worst drawdown (MAE): ₹{sig.mae:,.2f} "
                          f"({sig.mae_pct:-.1f}%) at {mae_time}")

        # Exit
        if sig.exit_reason == "target":
            exit_time = self._fmt_time(sig.target_hit_time)
            lines.append(f"- ✅ **TARGET HIT** at {exit_time} IST "
                          f"(₹{sig.exit_price:,.2f}) in {sig.bars_to_resolution} bars")
        elif sig.exit_reason == "stop":
            exit_time = self._fmt_time(sig.stop_hit_time)
            if sig.mfe_of_target >= 50:
                lines.append(f"- ⚠️ **STOPPED OUT (close call)** at {exit_time} IST "
                              f"(₹{sig.exit_price:,.2f}) — price reached "
                              f"{sig.mfe_of_target:.0f}% of target before reversing")
            else:
                lines.append(f"- ❌ **STOPPED OUT** at {exit_time} IST "
                              f"(₹{sig.exit_price:,.2f}) in {sig.bars_to_resolution} bars")
        elif sig.exit_reason == "eod":
            pnl = sig.exit_price - sig.entry_price if sig.direction == "long" else sig.entry_price - sig.exit_price
            pnl_pct = pnl / sig.entry_price * 100 if sig.entry_price > 0 else 0
            pnl_icon = "📈" if pnl > 0 else "📉"
            lines.append(f"- {pnl_icon} **EOD EXIT** at ₹{sig.exit_price:,.2f} "
                          f"({pnl_pct:+.1f}%) — neither target nor stop hit by close")

        # Verdict
        lines.append("")
        if sig.outcome == "CORRECT":
            lines.append(f"> **VERDICT: SUCCESS** — {sig.strategy} {action} worked as expected.")
        elif sig.outcome == "CLOSE_CALL":
            lines.append(f"> **VERDICT: CLOSE CALL** — Price moved {sig.mfe_of_target:.0f}% "
                          f"toward target before reversing. The direction was right but "
                          f"target was too ambitious or stop too tight.")
        elif sig.outcome == "WRONG":
            lines.append(f"> **VERDICT: FAILED** — Price only reached {sig.mfe_of_target:.0f}% "
                          f"of target. The setup didn't play out.")

        # Flag absurd targets
        if target_pct > 5.0 and sig.outcome != "CORRECT":
            lines.append(f">\n> ⚠️ **FLAG**: Target was {target_pct:.1f}% from entry — "
                          f"may be too aggressive for intraday.")
        lines.append("")

    def generate_report(self):
        """Generate markdown backtest report with signal-by-signal narratives."""
        lines = []
        td = self.target_date.isoformat()

        lines.append(f"# Intraday Backtest — {td}\n")

        # ── Summary ──
        total = len(self.all_signals)
        entered = sum(1 for s in self.all_signals if s.entry_hit)
        correct = sum(1 for s in self.all_signals if s.outcome == "CORRECT")
        wrong = sum(1 for s in self.all_signals if s.outcome == "WRONG")
        no_entry = sum(1 for s in self.all_signals if s.outcome == "NO_ENTRY")
        close_calls = sum(1 for s in self.all_signals if s.outcome == "CLOSE_CALL")
        win_rate = correct / entered * 100 if entered > 0 else 0

        # Avg RR achieved
        rr_achieved_list = []
        for s in self.all_signals:
            if s.entry_hit and s.entry_price > 0:
                stop_dist = abs(s.entry_price - s.stop_price)
                if stop_dist > 0 and s.exit_price > 0:
                    if s.direction == "long":
                        actual_rr = (s.exit_price - s.entry_price) / stop_dist
                    else:
                        actual_rr = (s.entry_price - s.exit_price) / stop_dist
                    rr_achieved_list.append(actual_rr)
        avg_rr = np.mean(rr_achieved_list) if rr_achieved_list else 0

        lines.append("## Summary\n")
        lines.append(f"- Phases simulated: post_market(T-1), pre_market(T), live x4")
        lines.append(f"- Total signals: {total} | Entered: {entered} | "
                      f"Correct: {correct} | Wrong: {wrong} | No-entry: {no_entry}")
        lines.append(f"- Win rate (entered): {win_rate:.1f}% | "
                      f"Avg RR achieved: {avg_rr:.1f}")
        lines.append(f"- Close calls (wrong but MFE>50% of target): {close_calls}\n")

        # ── Signal-by-Signal Replay ──
        lines.append("---\n")
        lines.append("## Signal-by-Signal Replay\n")
        lines.append("> For each signal: what the scanner suggested, and what actually "
                      "happened in the market. This is the core of the backtest — "
                      "read each one to build intuition.\n")

        # Group by phase, ordered chronologically
        phase_order = ["post_market_t-1", "pre_market"]
        live_phases = sorted(set(s.phase for s in self.all_signals
                                if s.phase.startswith("live_")))
        phase_order.extend(live_phases)

        signal_num = 0
        for phase in phase_order:
            phase_sigs = [s for s in self.all_signals if s.phase == phase]
            if not phase_sigs:
                continue
            phase_label = self._phase_label(phase)
            phase_entered = sum(1 for s in phase_sigs if s.entry_hit)
            phase_won = sum(1 for s in phase_sigs if s.outcome == "CORRECT")
            lines.append(f"---\n")
            lines.append(f"#### Scan: {phase_label} — {len(phase_sigs)} signal(s), "
                          f"{phase_entered} entered, {phase_won} won\n")

            for s in phase_sigs:
                signal_num += 1
                self._write_signal_narrative(lines, s, signal_num)

        # ── Per-Strategy Breakdown ──
        strategies = sorted(set(s.strategy for s in self.all_signals if s.strategy))
        if strategies:
            lines.append("---\n")
            lines.append("## Per-Strategy Breakdown\n")
            lines.append("| Strategy | Signals | Entered | Win Rate | "
                          "Avg MFE | Avg MAE | Avg RR Achieved |")
            lines.append("|----------|---------|---------|----------|"
                          "---------|---------|-----------------|")
            for strat in strategies:
                ss = [s for s in self.all_signals if s.strategy == strat]
                n = len(ss)
                n_entered = sum(1 for s in ss if s.entry_hit)
                n_won = sum(1 for s in ss if s.outcome == "CORRECT")
                wr = f"{n_won/n_entered*100:.0f}%" if n_entered > 0 else "N/A"
                avg_mfe = np.mean([s.mfe_pct for s in ss if s.entry_hit]) if n_entered else 0
                avg_mae = np.mean([s.mae_pct for s in ss if s.entry_hit]) if n_entered else 0
                rrs = []
                for s in ss:
                    if s.entry_hit and abs(s.entry_price - s.stop_price) > 0:
                        sd = abs(s.entry_price - s.stop_price)
                        if s.direction == "long":
                            rrs.append((s.exit_price - s.entry_price) / sd)
                        else:
                            rrs.append((s.entry_price - s.exit_price) / sd)
                avg_rr_s = np.mean(rrs) if rrs else 0
                lines.append(
                    f"| {strat} | {n} | {n_entered} | {wr} | "
                    f"{avg_mfe:.1f}% | {avg_mae:.1f}% | {avg_rr_s:.1f} |"
                )
            lines.append("")

        # ── Absurd Target Flags ──
        flagged = []
        for s in self.all_signals:
            if s.entry_price > 0:
                tgt_pct = abs(s.target_price - s.entry_price) / s.entry_price * 100
                if tgt_pct > 5.0:
                    flagged.append((s, tgt_pct))
        if flagged:
            lines.append("## ⚠️ Absurd Target Flags\n")
            lines.append("> Signals where target was >5% from entry — likely too "
                          "aggressive for intraday. Review strategy parameters.\n")
            lines.append("| Stock | Strategy | Dir | Entry | Target | Target % | "
                          "Outcome | MFE % of Target |")
            lines.append("|-------|----------|-----|-------|--------|----------|"
                          "---------|-----------------|")
            for s, tgt_pct in flagged:
                clean = s.symbol.replace(".NS", "")
                mfe_t = f"{s.mfe_of_target:.0f}%" if s.entry_hit else "—"
                lines.append(
                    f"| {clean} | {s.strategy} | {_action_label(s.direction)} | "
                    f"₹{s.entry_price:,.2f} | ₹{s.target_price:,.2f} | "
                    f"{tgt_pct:.1f}% | {s.outcome} | {mfe_t} |"
                )
            lines.append("")

        # ── Wrong Calls Analysis ──
        wrong_sigs = [s for s in self.all_signals
                      if s.outcome in ("WRONG", "CLOSE_CALL")]
        if wrong_sigs:
            lines.append("## Wrong Calls Analysis\n")
            lines.append("| Stock | Phase | Strategy | Action | Entry | Target | Stop | "
                          "Exit | MFE% | How Close |")
            lines.append("|-------|-------|----------|--------|-------|--------|------|"
                          "-----|------|-----------|")
            for s in wrong_sigs:
                clean = s.symbol.replace(".NS", "")
                how_close = f"{s.mfe_of_target:.0f}% of target"
                lines.append(
                    f"| {clean} | {s.phase} | {s.strategy} | {_action_label(s.direction)} | "
                    f"{s.entry_price:.2f} | {s.target_price:.2f} | {s.stop_price:.2f} | "
                    f"{s.exit_price:.2f} | {s.mfe_pct:+.1f}% | {how_close} |"
                )
            lines.append("")

        return "\n".join(lines)

    # ── Main Runner ───────────────────────────────────────────────────

    def run(self):
        """Full backtest pipeline for a single day."""
        print(f"\n{'='*60}")
        print(f"  INTRADAY BACKTEST — {self.target_date}")
        print(f"{'='*60}")

        self.fetch_all_data()

        # Verify target_date has data
        nifty_bars = self._get_actual_day_data("_nifty")
        if nifty_bars.empty:
            print(f"\n  [WARN] No intraday data for {self.target_date} — "
                  f"may be a holiday or weekend. Skipping.")
            return None

        # Phase 1: Post-market T-1
        self.run_post_market_t_minus_1()

        # Phase 2: Pre-market T
        self.run_pre_market_t()

        # Phase 3: Live scans at 4 points
        live_times = [dtime(9, 30), dtime(11, 0), dtime(13, 0), dtime(14, 30)]
        for t in live_times:
            self.run_live_scan_at(t)

        # Phase 4: Validate
        self.validate_signals()

        # Phase 5: Report
        report = self.generate_report()
        return report


# ── Multi-Day Runner ──────────────────────────────────────────────────────

def run_multi_day(start_date, end_date, capital=1000000, config=None):
    """Run backtest across a date range and aggregate results."""
    all_signals = []
    day_summaries = []

    current = start_date
    while current <= end_date:
        # Skip weekends
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        engine = IntradayBacktestEngine(current, capital=capital, config=config)
        report = engine.run()
        if report is None:
            current += timedelta(days=1)
            continue

        # Collect signals
        all_signals.extend(engine.all_signals)

        # Day summary
        entered = sum(1 for s in engine.all_signals if s.entry_hit)
        correct = sum(1 for s in engine.all_signals if s.outcome == "CORRECT")
        wrong = sum(1 for s in engine.all_signals if s.outcome == "WRONG")
        close_calls = sum(1 for s in engine.all_signals if s.outcome == "CLOSE_CALL")
        wr = correct / entered * 100 if entered > 0 else 0
        day_summaries.append({
            "date": current,
            "signals": len(engine.all_signals),
            "entered": entered,
            "correct": correct,
            "wrong": wrong,
            "close_calls": close_calls,
            "win_rate": wr,
        })

        # Save individual day report
        INTRADAY_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = INTRADAY_REPORT_DIR / f"backtest_{current.isoformat()}.md"
        with open(path, "w") as f:
            f.write(report + "\n")
        print(f"  Day report saved: {path}")

        current += timedelta(days=1)

    if not day_summaries:
        print("\n  No trading days found in range.")
        return

    # ── Aggregate Report ──
    lines = []
    lines.append(f"# Intraday Backtest — {start_date} to {end_date}\n")
    lines.append("## Per-Day Summary\n")
    lines.append("| Date | Signals | Entered | Correct | Wrong | Close Call | Win Rate |")
    lines.append("|------|---------|---------|---------|-------|------------|----------|")
    for d in day_summaries:
        lines.append(
            f"| {d['date']} | {d['signals']} | {d['entered']} | "
            f"{d['correct']} | {d['wrong']} | {d['close_calls']} | "
            f"{d['win_rate']:.0f}% |"
        )
    lines.append("")

    # Overall stats
    total = len(all_signals)
    entered = sum(1 for s in all_signals if s.entry_hit)
    correct = sum(1 for s in all_signals if s.outcome == "CORRECT")
    wrong = sum(1 for s in all_signals if s.outcome == "WRONG")
    close_calls = sum(1 for s in all_signals if s.outcome == "CLOSE_CALL")
    no_entry = sum(1 for s in all_signals if s.outcome == "NO_ENTRY")
    wr = correct / entered * 100 if entered > 0 else 0

    lines.append("## Overall Summary\n")
    lines.append(f"- Trading days: {len(day_summaries)}")
    lines.append(f"- Total signals: {total} | Entered: {entered} | "
                  f"Correct: {correct} | Wrong: {wrong} | No-entry: {no_entry}")
    lines.append(f"- Win rate (entered): {wr:.1f}%")
    lines.append(f"- Close calls: {close_calls}\n")

    # Per-strategy across all days
    strategies = sorted(set(s.strategy for s in all_signals if s.strategy))
    if strategies:
        lines.append("## Per-Strategy (All Days)\n")
        lines.append("| Strategy | Signals | Entered | Win Rate | "
                      "Avg MFE | Avg MAE |")
        lines.append("|----------|---------|---------|----------|"
                      "---------|---------|")
        for strat in strategies:
            ss = [s for s in all_signals if s.strategy == strat]
            n = len(ss)
            n_entered = sum(1 for s in ss if s.entry_hit)
            n_won = sum(1 for s in ss if s.outcome == "CORRECT")
            swr = f"{n_won/n_entered*100:.0f}%" if n_entered else "N/A"
            avg_mfe = np.mean([s.mfe_pct for s in ss if s.entry_hit]) if n_entered else 0
            avg_mae = np.mean([s.mae_pct for s in ss if s.entry_hit]) if n_entered else 0
            lines.append(
                f"| {strat} | {n} | {n_entered} | {swr} | "
                f"{avg_mfe:.1f}% | {avg_mae:.1f}% |"
            )
        lines.append("")

    aggregate_report = "\n".join(lines)

    # Save aggregate report
    INTRADAY_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    agg_path = (INTRADAY_REPORT_DIR /
                f"backtest_{start_date.isoformat()}_to_{end_date.isoformat()}.md")
    with open(agg_path, "w") as f:
        f.write(aggregate_report + "\n")
    print(f"\n  Aggregate report saved: {agg_path}")

    # Print summary to terminal
    print(f"\n{'='*60}")
    print(f"  AGGREGATE RESULTS — {start_date} to {end_date}")
    print(f"{'='*60}")
    print(f"  Days: {len(day_summaries)} | Signals: {total} | "
          f"Entered: {entered}")
    print(f"  Correct: {correct} | Wrong: {wrong} | "
          f"Close Calls: {close_calls} | No-entry: {no_entry}")
    print(f"  Win Rate (entered): {wr:.1f}%")


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Intraday Scanner Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python -m intraday.backtest --date 2026-02-20
  python -m intraday.backtest --start 2026-02-10 --end 2026-02-20
  python -m intraday.backtest --date 2026-02-20 --capital 500000
  python -m intraday.backtest --date 2026-02-20 --llm""",
    )
    parser.add_argument("--date", type=str, help="Single date (YYYY-MM-DD)")
    parser.add_argument("--start", type=str, help="Start date for range")
    parser.add_argument("--end", type=str, help="End date for range")
    parser.add_argument("--capital", type=int, default=1000000,
                        help="Starting capital (default: 1000000)")
    parser.add_argument("--llm", action="store_true",
                        help="Add LLM summary to report (off by default)")
    args = parser.parse_args()

    # Load config
    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}
    config.setdefault("global", {})["capital"] = args.capital

    if args.date:
        target = date.fromisoformat(args.date)
        engine = IntradayBacktestEngine(target, capital=args.capital, config=config)
        report = engine.run()
        if report:
            INTRADAY_REPORT_DIR.mkdir(parents=True, exist_ok=True)
            path = INTRADAY_REPORT_DIR / f"backtest_{target.isoformat()}.md"
            with open(path, "w") as f:
                f.write(report + "\n")
            print(f"\n  Report saved: {path}")

            # Optional LLM summary
            if args.llm:
                try:
                    from common.llm import call_llm
                    summary_prompt = (
                        f"Summarize this intraday backtest report in 200 words. "
                        f"Focus on what worked, what didn't, and key takeaways:\n\n"
                        f"{report[:3000]}"
                    )
                    llm_summary = call_llm(
                        [{"role": "user", "content": summary_prompt}],
                        max_tokens=500,
                    )
                    if llm_summary:
                        with open(path, "a") as f:
                            f.write(f"\n---\n\n## AI Summary\n\n{llm_summary}\n")
                        print("  LLM summary appended to report")
                except Exception as e:
                    print(f"  [WARN] LLM summary failed: {e}")

    elif args.start and args.end:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        run_multi_day(start, end, capital=args.capital, config=config)
    else:
        parser.error("Provide --date or both --start and --end")


if __name__ == "__main__":
    main()
