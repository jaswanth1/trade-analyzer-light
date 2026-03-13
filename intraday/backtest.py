#!/usr/bin/env python3
"""
Intraday Scanner Backtest

Replays historical days through the intraday scanner's phase-aware pipeline:
  1. Post-market T-1 — tomorrow's watchlist (gap scenarios for T)
  2. Pre-market T — gap scenarios with daily data only
  3. Live scans — continuous (default, every N min) or 4-point (--fast)
  4. Validates all signals against actual T data (entry hit, target/stop, MFE/MAE)

Pre-live (9:00-9:15) is skipped — yfinance doesn't store pre-market auction data.

Usage:
    python -m intraday.backtest --date 2026-02-20
    python -m intraday.backtest --date 2026-02-20 --fast          # old 4-point mode
    python -m intraday.backtest --date 2026-02-20 --scan-interval 15
    python -m intraday.backtest --date 2026-02-20 --interval 1m   # 1-min candles
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
from intraday.scanner import INTRADAY_REPORT_DIR, LONG_ONLY
from intraday.scoring import evaluate_symbol, rank_signals
from intraday.phases import (
    _build_gap_scenarios, run_pre_market_scan,
    run_post_market_scan, _run_live_scan,
)
from intraday.backtest_report import generate_report

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

    # Prediction context (for LLM reasoning)
    conditions: dict = field(default_factory=dict)     # strategy conditions (met/unmet)
    gates: dict = field(default_factory=dict)           # vwap_gate, nifty_ok, etc.
    day_type: str = ""                                  # market day type at scan time
    score_raw: float = 0.0                              # raw confidence before adjustments
    historical_hit_rate: float = 0.0                    # historical WR for this setup
    historical_sample_size: int = 0                     # sample size for hit rate

    # Price journey tracking
    mfe_time: str = ""             # when MFE was reached
    mae_time: str = ""             # when MAE was reached


# ── Backtest Engine ───────────────────────────────────────────────────────

class IntradayBacktestEngine:
    """Backtest engine for the intraday scanner."""

    def __init__(self, target_date, capital=1000000, config=None, use_llm=False,
                 fast=False, scan_interval=30, interval="5m"):
        self.target_date = target_date  # date object — the day we're testing
        self.capital = capital
        self.config = config or {}
        self.use_llm = use_llm
        self.fast = fast                # True = old 4-point mode
        self.scan_interval = scan_interval  # minutes between continuous scans
        self.interval = interval        # "5m" or "1m" candle width
        self.all_signals: list[SignalResult] = []
        self._seen_signals: set = set()  # dedup key: (symbol, strategy, direction)
        self.market_context: dict = {}
        self.data_cache: dict = {}  # {symbol: {"daily": df, "intra": df}}
        self.symbols = list(TICKERS.keys())

    # ── Data Fetching ──────────────────────────────────────────────────

    def fetch_all_data(self):
        """Pre-fetch daily (6mo) + intraday data for all tickers + Nifty."""
        # 1m candles: yfinance limits to 7d; 5m: 5d
        intra_period = "7d" if self.interval == "1m" else "5d"
        intra_label = self.interval
        if self.interval == "1m":
            print(f"  [NOTE] Using 1-min candles — slower evaluation, finer resolution")
        print(f"  Fetching data for {len(self.symbols)} tickers + Nifty "
              f"(intraday: {intra_label}, period: {intra_period})...")

        # Nifty
        nifty_daily = fetch_yf(BENCHMARK, period="6mo", interval="1d")
        nifty_intra = fetch_yf(BENCHMARK, period=intra_period, interval=self.interval)
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
            intra = fetch_yf(sym, period=intra_period, interval=self.interval)
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

        # Collect STRONG/ACTIVE/WATCH — skip only AVOID
        # WATCH signals provide learning value (borderline calls)
        n_total = len(candidates or [])
        n_actionable = 0
        n_watch = 0
        for c in (candidates or []):
            tier = c.get("signal", "AVOID")
            if tier == "AVOID":
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
                signal_tier=tier,
                rr_ratio=c.get("rr_ratio", 0),
                reason=c.get("signal_reason", ""),
                convergence=c.get("convergence_detail", ""),
                regime=c.get("symbol_regime", {}).get("trend", "") if isinstance(c.get("symbol_regime"), dict) else str(c.get("symbol_regime", "")),
                # Prediction context for LLM reasoning
                conditions=c.get("conditions", {}),
                gates=c.get("gates", {}),
                day_type=c.get("day_type", ""),
                score_raw=c.get("confidence", 0),
                historical_hit_rate=c.get("historical_hit_rate", 0),
                historical_sample_size=c.get("historical_sample_size", 0),
            )
            self.all_signals.append(sig)
            if tier in ("STRONG", "ACTIVE"):
                n_actionable += 1
            else:
                n_watch += 1

        print(f"    Candidates: {n_total} | "
              f"Actionable (STRONG/ACTIVE): {n_actionable} | "
              f"Watch: {n_watch}")

    def run_continuous_live_scan(self):
        """Continuous live scanning from 09:30 to 15:15 at scan_interval-min steps.

        Deduplicates signals via self._seen_signals keyed on (symbol, strategy, direction).
        Seed the seen set from pre/post-market signals before calling this.
        """
        # Seed seen set from signals already collected (post-market, pre-market)
        for sig in self.all_signals:
            self._seen_signals.add((sig.symbol, sig.strategy, sig.direction))

        start_minutes = 9 * 60 + 30   # 09:30
        end_minutes = 15 * 60 + 15     # 15:15
        scan_times = []
        t = start_minutes
        while t <= end_minutes:
            h, m = divmod(t, 60)
            scan_times.append(dtime(h, m))
            t += self.scan_interval

        print(f"\n  Continuous scanning: {len(scan_times)} scan points "
              f"(every {self.scan_interval} min, 09:30-15:15)")

        total_new = 0
        for scan_time in scan_times:
            mock_dt = datetime.combine(self.target_date, scan_time, tzinfo=IST)
            label = scan_time.strftime("%H:%M")

            t_minus_1 = self._get_prev_trading_day()
            data_override = self._build_data_override(t_minus_1, mock_dt)

            candidates = _run_live_scan(
                self.config, self.symbols,
                now_ist=mock_dt, data_override=data_override, skip_llm=True,
            )

            n_new = 0
            for c in (candidates or []):
                tier = c.get("signal", "AVOID")
                if tier == "AVOID":
                    continue
                key = (c["symbol"], c.get("strategy", ""), c.get("direction", "long"))
                if key in self._seen_signals:
                    continue  # already seen this signal
                self._seen_signals.add(key)

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
                    signal_tier=tier,
                    rr_ratio=c.get("rr_ratio", 0),
                    reason=c.get("signal_reason", ""),
                    convergence=c.get("convergence_detail", ""),
                    regime=c.get("symbol_regime", {}).get("trend", "") if isinstance(c.get("symbol_regime"), dict) else str(c.get("symbol_regime", "")),
                    conditions=c.get("conditions", {}),
                    gates=c.get("gates", {}),
                    day_type=c.get("day_type", ""),
                    score_raw=c.get("confidence", 0),
                    historical_hit_rate=c.get("historical_hit_rate", 0),
                    historical_sample_size=c.get("historical_sample_size", 0),
                )
                self.all_signals.append(sig)
                n_new += 1

            if n_new > 0:
                print(f"    {label}: +{n_new} new signals")
            total_new += n_new

        print(f"  Continuous scan complete: {total_new} new signals across "
              f"{len(scan_times)} scan points")

    # ── Market Context Capture ─────────────────────────────────────────

    def _capture_market_context(self):
        """Capture actual market conditions for target_date.

        Returns dict with Nifty stats, VIX, and per-signal price summaries
        so the LLM can compare prediction context with actual behavior.
        """
        ctx = {"date": self.target_date.isoformat()}

        # Nifty day stats
        nifty_bars = self._get_actual_day_data("_nifty")
        if not nifty_bars.empty:
            nifty_open = float(nifty_bars["Open"].iloc[0])
            nifty_close = float(nifty_bars["Close"].iloc[-1])
            nifty_high = float(nifty_bars["High"].max())
            nifty_low = float(nifty_bars["Low"].min())
            nifty_range = nifty_high - nifty_low
            nifty_chg = (nifty_close - nifty_open) / nifty_open * 100 if nifty_open > 0 else 0
            direction = "up" if nifty_chg > 0.1 else ("down" if nifty_chg < -0.1 else "flat")

            # ATR context
            nifty_daily = self.data_cache.get("_nifty", {}).get("daily", pd.DataFrame())
            atr = 0
            if not nifty_daily.empty and len(nifty_daily) >= 14:
                try:
                    atr = float(compute_atr(nifty_daily, period=14))
                    if np.isnan(atr):
                        atr = 0
                except Exception:
                    atr = 0

            ctx["nifty"] = {
                "open": nifty_open, "close": nifty_close,
                "high": nifty_high, "low": nifty_low,
                "range": nifty_range, "change_pct": round(nifty_chg, 2),
                "direction": direction,
                "atr_14": round(atr, 2),
                "range_vs_atr": round(nifty_range / atr, 2) if atr > 0 else 0,
            }

        # VIX
        vix_val, vix_regime = self.data_cache.get("_vix", (None, "normal"))
        ctx["vix"] = {"value": vix_val, "regime": vix_regime}

        # Day type (from full-day nifty data)
        if not nifty_bars.empty:
            nifty_daily = self.data_cache.get("_nifty", {}).get("daily", pd.DataFrame())
            try:
                day_info = classify_day_type(nifty_bars, nifty_daily)
                ctx["day_type"] = day_info.get("type", "unknown")
            except Exception:
                ctx["day_type"] = "unknown"

        # Per-signal actual price summary
        signal_summaries = {}
        for sig in self.all_signals:
            if sig.symbol in signal_summaries:
                continue
            bars = self._get_actual_day_data(sig.symbol)
            if bars.empty:
                continue
            sym_open = float(bars["Open"].iloc[0])
            sym_close = float(bars["Close"].iloc[-1])
            sym_high = float(bars["High"].max())
            sym_low = float(bars["Low"].min())
            sym_chg = (sym_close - sym_open) / sym_open * 100 if sym_open > 0 else 0
            signal_summaries[sig.symbol] = {
                "open": sym_open, "close": sym_close,
                "high": sym_high, "low": sym_low,
                "change_pct": round(sym_chg, 2),
                "direction": "up" if sym_chg > 0.3 else ("down" if sym_chg < -0.3 else "flat"),
            }
        ctx["symbols"] = signal_summaries

        return ctx

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

        # Phase 3: Live scans
        if self.fast:
            # Old 4-point mode
            live_times = [dtime(9, 30), dtime(11, 0), dtime(13, 0), dtime(14, 30)]
            for t in live_times:
                self.run_live_scan_at(t)
        else:
            # Continuous scanning (default)
            self.run_continuous_live_scan()

        # Phase 4: Validate
        self.validate_signals()

        # Phase 5: Capture market context (for LLM reasoning)
        self.market_context = self._capture_market_context()

        # Phase 6: Report
        report = generate_report(
            self.target_date, self.all_signals,
            use_llm=self.use_llm, market_context=self.market_context,
        )
        return report


# ── Multi-Day Runner ──────────────────────────────────────────────────────

def run_multi_day(start_date, end_date, capital=1000000, config=None, use_llm=False,
                  fast=False, scan_interval=30, interval="5m"):
    """Run backtest across a date range and aggregate results."""
    all_signals = []
    day_summaries = []

    current = start_date
    while current <= end_date:
        # Skip weekends
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        engine = IntradayBacktestEngine(current, capital=capital, config=config,
                                        use_llm=use_llm, fast=fast,
                                        scan_interval=scan_interval,
                                        interval=interval)
        report = engine.run()
        if report is None:
            current += timedelta(days=1)
            continue

        # Collect signals
        all_signals.extend(engine.all_signals)

        # Day summary (actionable signals only for headline stats)
        day_actionable = [s for s in engine.all_signals
                          if s.signal_tier in ("STRONG", "ACTIVE")]
        entered = sum(1 for s in day_actionable if s.entry_hit)
        correct = sum(1 for s in day_actionable if s.outcome == "CORRECT")
        wrong = sum(1 for s in day_actionable if s.outcome == "WRONG")
        close_calls = sum(1 for s in day_actionable if s.outcome == "CLOSE_CALL")
        wr = correct / entered * 100 if entered > 0 else 0
        day_summaries.append({
            "date": current,
            "signals": len(day_actionable),
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

    # Overall stats (actionable only)
    agg_actionable = [s for s in all_signals if s.signal_tier in ("STRONG", "ACTIVE")]
    total = len(agg_actionable)
    entered = sum(1 for s in agg_actionable if s.entry_hit)
    correct = sum(1 for s in agg_actionable if s.outcome == "CORRECT")
    wrong = sum(1 for s in agg_actionable if s.outcome == "WRONG")
    close_calls = sum(1 for s in agg_actionable if s.outcome == "CLOSE_CALL")
    no_entry = sum(1 for s in agg_actionable if s.outcome == "NO_ENTRY")
    wr = correct / entered * 100 if entered > 0 else 0

    lines.append("## Overall Summary\n")
    lines.append(f"- Trading days: {len(day_summaries)}")
    lines.append(f"- Total signals: {total} | Entered: {entered} | "
                  f"Correct: {correct} | Wrong: {wrong} | No-entry: {no_entry}")
    lines.append(f"- Win rate (entered): {wr:.1f}%")
    lines.append(f"- Close calls: {close_calls}\n")

    # Per-strategy across all days (actionable only)
    strategies = sorted(set(s.strategy for s in agg_actionable if s.strategy))
    if strategies:
        lines.append("## Per-Strategy (All Days)\n")
        lines.append("| Strategy | Signals | Entered | Win Rate | "
                      "Avg MFE | Avg MAE |")
        lines.append("|----------|---------|---------|----------|"
                      "---------|---------|")
        for strat in strategies:
            ss = [s for s in agg_actionable if s.strategy == strat]
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
  python -m intraday.backtest --date 2026-02-20 --fast
  python -m intraday.backtest --date 2026-02-20 --scan-interval 15
  python -m intraday.backtest --date 2026-02-20 --interval 1m
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
    parser.add_argument("--fast", action="store_true",
                        help="Use old 4-point scan mode instead of continuous")
    parser.add_argument("--scan-interval", type=int, default=30,
                        help="Minutes between continuous scans (default: 30)")
    parser.add_argument("--interval", type=str, default="5m",
                        choices=["1m", "5m"],
                        help="Candle interval (default: 5m)")
    args = parser.parse_args()

    # Load config
    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}
    config.setdefault("global", {})["capital"] = args.capital

    if args.date:
        target = date.fromisoformat(args.date)
        engine = IntradayBacktestEngine(target, capital=args.capital,
                                        config=config, use_llm=args.llm,
                                        fast=args.fast,
                                        scan_interval=args.scan_interval,
                                        interval=args.interval)
        report = engine.run()
        if report:
            INTRADAY_REPORT_DIR.mkdir(parents=True, exist_ok=True)
            path = INTRADAY_REPORT_DIR / f"backtest_{target.isoformat()}.md"
            with open(path, "w") as f:
                f.write(report + "\n")
            print(f"\n  Report saved: {path}")

    elif args.start and args.end:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        run_multi_day(start, end, capital=args.capital, config=config,
                      use_llm=args.llm, fast=args.fast,
                      scan_interval=args.scan_interval,
                      interval=args.interval)
    else:
        parser.error("Provide --date or both --start and --end")


if __name__ == "__main__":
    main()
