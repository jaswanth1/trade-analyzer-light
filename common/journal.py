"""
Trade Journal — Peewee + SQLite persistence for scalp signals and trades.

Auto-logs all ACTIVE signals. User marks fills separately.
Provides portfolio metrics (Sharpe, Sortino, max DD, streaks) and
beta-adjusted exposure calculations.
"""

import math
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import yaml
from peewee import (
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    FloatField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

from common.data import DB_PATH

db = SqliteDatabase(None)


# ── Models ─────────────────────────────────────────────────────────────────

class BaseModel(Model):
    class Meta:
        database = db


class Trade(BaseModel):
    signal_time = DateTimeField(default=datetime.now)
    symbol = CharField()
    direction = CharField(default="long")
    phase = CharField(default="")
    gap_type = CharField(default="")
    gap_pct = FloatField(default=0.0)
    edge_strength = IntegerField(default=0)
    kelly_fraction = FloatField(default=0.0)
    vix_at_signal = FloatField(null=True)
    nifty_regime = CharField(default="unknown")
    conditions_met = IntegerField(default=0)
    conditions_total = IntegerField(default=0)
    weighted_score = FloatField(default=0.0)
    entry_price = FloatField(default=0.0)
    target_price = FloatField(default=0.0)
    stop_price = FloatField(default=0.0)
    recommended_qty = IntegerField(default=0)
    capital_at_risk = FloatField(default=0.0)
    status = CharField(default="signal")
    actual_entry = FloatField(null=True)
    actual_exit = FloatField(null=True)
    actual_qty = IntegerField(null=True)
    exit_time = DateTimeField(null=True)
    exit_reason = CharField(null=True)
    slippage_entry = FloatField(null=True)
    slippage_exit = FloatField(null=True)
    pnl = FloatField(null=True)
    pnl_pct = FloatField(null=True)
    mae_pct = FloatField(null=True)

    class Meta:
        table_name = "trades"


class DailyPerformance(BaseModel):
    date = DateField(unique=True)
    total_trades = IntegerField(default=0)
    wins = IntegerField(default=0)
    losses = IntegerField(default=0)
    gross_pnl = FloatField(default=0.0)
    net_pnl = FloatField(default=0.0)
    max_drawdown_pct = FloatField(default=0.0)
    nifty_regime = CharField(default="unknown")
    vix_close = FloatField(null=True)
    notes = TextField(default="")

    class Meta:
        table_name = "daily_performance"


class ConfigSnapshot(BaseModel):
    created = DateTimeField(default=datetime.now)
    symbol = CharField()
    config_yaml = TextField()
    edge_strength = IntegerField(default=0)
    best_ev = FloatField(default=0.0)
    best_combo = CharField(default="")

    class Meta:
        table_name = "config_snapshots"


# ── Init ───────────────────────────────────────────────────────────────────

def init_db(db_path=None):
    """Initialize the journal database, creating tables if needed."""
    path = db_path or DB_PATH
    db.init(str(path))
    db.connect(reuse_if_open=True)
    db.create_tables([Trade, DailyPerformance, ConfigSnapshot], safe=True)
    return db


# ── Signal Logging ─────────────────────────────────────────────────────────

def log_signal(*, symbol, direction="long", phase="", gap_type="", gap_pct=0.0,
               edge_strength=0, kelly_fraction=0.0, vix_at_signal=None,
               nifty_regime="unknown", conditions_met=0, conditions_total=0,
               weighted_score=0.0, entry_price=0.0, target_price=0.0,
               stop_price=0.0, recommended_qty=0, capital_at_risk=0.0):
    """Log an ACTIVE signal to the journal. Returns the Trade record."""
    trade = Trade.create(
        symbol=symbol,
        direction=direction,
        phase=phase,
        gap_type=gap_type,
        gap_pct=gap_pct,
        edge_strength=edge_strength,
        kelly_fraction=kelly_fraction,
        vix_at_signal=vix_at_signal,
        nifty_regime=nifty_regime,
        conditions_met=conditions_met,
        conditions_total=conditions_total,
        weighted_score=weighted_score,
        entry_price=entry_price,
        target_price=target_price,
        stop_price=stop_price,
        recommended_qty=recommended_qty,
        capital_at_risk=capital_at_risk,
        status="signal",
    )
    return trade


def fill_trade(trade_id, actual_entry, actual_qty):
    """Mark a signal as filled with actual entry details."""
    trade = Trade.get_by_id(trade_id)
    trade.status = "filled"
    trade.actual_entry = actual_entry
    trade.actual_qty = actual_qty
    trade.slippage_entry = actual_entry - trade.entry_price
    trade.save()
    return trade


def close_trade(trade_id, actual_exit, exit_reason="manual", mae_pct=None):
    """Close a filled trade with exit details. Computes P&L."""
    trade = Trade.get_by_id(trade_id)
    if trade.status != "filled":
        raise ValueError(f"Trade {trade_id} is not filled (status={trade.status})")

    trade.status = "closed"
    trade.actual_exit = actual_exit
    trade.exit_time = datetime.now()
    trade.exit_reason = exit_reason
    trade.slippage_exit = actual_exit - trade.target_price if trade.target_price else None

    entry = trade.actual_entry or trade.entry_price
    qty = trade.actual_qty or 1
    if trade.direction == "long":
        trade.pnl = (actual_exit - entry) * qty
        trade.pnl_pct = (actual_exit / entry - 1) * 100 if entry > 0 else 0
    else:
        trade.pnl = (entry - actual_exit) * qty
        trade.pnl_pct = (entry / actual_exit - 1) * 100 if actual_exit > 0 else 0

    if mae_pct is not None:
        trade.mae_pct = mae_pct

    trade.save()
    return trade


# ── Edge Decay ─────────────────────────────────────────────────────────────

def get_edge_decay(symbol, window=20):
    """Compare recent win rate to historical for a symbol."""
    closed = (Trade
              .select()
              .where(Trade.symbol == symbol, Trade.status == "closed")
              .order_by(Trade.exit_time.desc()))

    all_trades = list(closed)
    if len(all_trades) < window:
        return {"recent_wr": None, "historical_wr": None, "decay_pct": None,
                "n_total": len(all_trades)}

    recent = all_trades[:window]
    historical = all_trades[window:]

    recent_wins = sum(1 for t in recent if t.pnl and t.pnl > 0)
    recent_wr = recent_wins / len(recent) * 100

    hist_wins = sum(1 for t in historical if t.pnl and t.pnl > 0)
    hist_wr = hist_wins / len(historical) * 100 if historical else 0

    decay = hist_wr - recent_wr

    return {
        "recent_wr": round(recent_wr, 1),
        "historical_wr": round(hist_wr, 1),
        "decay_pct": round(decay, 1),
        "n_total": len(all_trades),
        "n_recent": len(recent),
    }


# ── Portfolio Metrics ──────────────────────────────────────────────────────

def get_portfolio_metrics(days=30):
    """Compute portfolio-level metrics from closed trades over N days."""
    cutoff = datetime.now() - timedelta(days=days)
    closed = list(
        Trade
        .select()
        .where(Trade.status == "closed", Trade.exit_time >= cutoff)
        .order_by(Trade.exit_time.asc())
    )

    if not closed:
        return {
            "n_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "gross_pnl": 0, "avg_pnl_pct": 0,
            "sharpe": None, "sortino": None,
            "max_drawdown_pct": 0, "max_win_streak": 0, "max_loss_streak": 0,
            "current_streak": 0, "cumulative_pnl": 0, "days": days,
        }

    pnl_pcts = [t.pnl_pct for t in closed if t.pnl_pct is not None]
    pnls = [t.pnl for t in closed if t.pnl is not None]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p <= 0)

    sharpe = None
    if pnl_pcts and len(pnl_pcts) > 1:
        mean_r = np.mean(pnl_pcts)
        std_r = np.std(pnl_pcts, ddof=1)
        if std_r > 0:
            sharpe = round((mean_r / std_r) * math.sqrt(250), 2)

    sortino = None
    if pnl_pcts and len(pnl_pcts) > 1:
        neg_returns = [r for r in pnl_pcts if r < 0]
        if neg_returns:
            downside_std = np.std(neg_returns, ddof=1)
            if downside_std > 0:
                sortino = round((np.mean(pnl_pcts) / downside_std) * math.sqrt(250), 2)

    cum_pnl = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum_pnl)
    drawdowns = cum_pnl - peak
    max_dd = float(abs(drawdowns.min())) if len(drawdowns) > 0 else 0
    total_capital = cum_pnl[-1] + max_dd if max_dd > 0 else abs(cum_pnl[-1]) if cum_pnl[-1] != 0 else 1
    max_dd_pct = round(max_dd / total_capital * 100, 2) if total_capital > 0 else 0

    max_win_streak = max_loss_streak = current_streak = 0
    win_streak = loss_streak = 0
    for p in pnls:
        if p > 0:
            win_streak += 1
            loss_streak = 0
            max_win_streak = max(max_win_streak, win_streak)
        else:
            loss_streak += 1
            win_streak = 0
            max_loss_streak = max(max_loss_streak, loss_streak)
    current_streak = win_streak if win_streak > 0 else -loss_streak

    return {
        "n_trades": len(closed),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(closed) * 100, 1) if closed else 0,
        "gross_pnl": round(sum(pnls), 2),
        "avg_pnl_pct": round(np.mean(pnl_pcts), 2) if pnl_pcts else 0,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_pct": max_dd_pct,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "current_streak": current_streak,
        "cumulative_pnl": round(float(cum_pnl[-1]), 2) if len(cum_pnl) > 0 else 0,
        "days": days,
    }


# ── Beta-Adjusted Exposure ────────────────────────────────────────────────

def compute_beta_adjusted_exposure(positions, betas):
    """Compute portfolio-level exposure metrics."""
    if not positions:
        return {
            "gross_exposure": 0, "beta_weighted_exposure": 0,
            "net_beta": 0, "hhi": 0, "n_positions": 0,
        }

    gross = 0
    beta_weighted = 0
    exposures = []

    for pos in positions:
        sym = pos["symbol"]
        cap = pos.get("capital_allocated", 0)
        direction_mult = 1 if pos.get("direction", "long") == "long" else -1
        beta = betas.get(sym, 1.0)

        gross += abs(cap)
        beta_weighted += cap * beta * direction_mult
        exposures.append(abs(cap))

    hhi = 0
    if gross > 0:
        shares = [e / gross for e in exposures]
        hhi = round(sum(s ** 2 for s in shares) * 10000)

    return {
        "gross_exposure": round(gross, 2),
        "beta_weighted_exposure": round(beta_weighted, 2),
        "net_beta": round(beta_weighted / gross, 3) if gross > 0 else 0,
        "hhi": hhi,
        "n_positions": len(positions),
    }


# ── Weekly Summary ─────────────────────────────────────────────────────────

def generate_weekly_summary():
    """Generate a markdown summary of the past week's trading."""
    week_ago = datetime.now() - timedelta(days=7)
    closed = list(
        Trade
        .select()
        .where(Trade.status == "closed", Trade.exit_time >= week_ago)
        .order_by(Trade.exit_time.asc())
    )

    signals = Trade.select().where(
        Trade.signal_time >= week_ago
    ).count()

    metrics = get_portfolio_metrics(days=7)

    lines = []
    lines.append("# Weekly Trading Summary")
    lines.append(f"*Period: {week_ago.strftime('%Y-%m-%d')} to {datetime.now().strftime('%Y-%m-%d')}*\n")

    lines.append("## Overview\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Signals Generated | {signals} |")
    lines.append(f"| Trades Closed | {metrics['n_trades']} |")
    lines.append(f"| Win Rate | {metrics['win_rate']}% |")
    lines.append(f"| Gross P&L | {metrics['gross_pnl']:+.2f} |")
    lines.append(f"| Avg P&L% | {metrics['avg_pnl_pct']:+.2f}% |")
    lines.append(f"| Sharpe | {metrics['sharpe'] or 'N/A'} |")
    lines.append(f"| Max Drawdown | {metrics['max_drawdown_pct']}% |")
    lines.append(f"| Best Streak | {metrics['max_win_streak']}W |")
    lines.append(f"| Worst Streak | {metrics['max_loss_streak']}L |")

    if closed:
        lines.append("\n## Per-Symbol Breakdown\n")
        lines.append("| Symbol | Trades | Wins | Losses | Avg P&L% | Total P&L |")
        lines.append("|--------|--------|------|--------|----------|-----------|")

        symbols = {}
        for t in closed:
            if t.symbol not in symbols:
                symbols[t.symbol] = []
            symbols[t.symbol].append(t)

        for sym, trades in sorted(symbols.items()):
            n = len(trades)
            w = sum(1 for t in trades if t.pnl and t.pnl > 0)
            l = n - w
            avg_pnl = np.mean([t.pnl_pct for t in trades if t.pnl_pct is not None])
            total_pnl = sum(t.pnl for t in trades if t.pnl is not None)
            lines.append(f"| {sym} | {n} | {w} | {l} | {avg_pnl:+.2f}% | {total_pnl:+.2f} |")

    if closed:
        lines.append("\n## Edge Decay Alerts\n")
        syms_checked = set()
        for t in closed:
            if t.symbol not in syms_checked:
                syms_checked.add(t.symbol)
                decay = get_edge_decay(t.symbol)
                if decay["decay_pct"] is not None and decay["decay_pct"] > 10:
                    lines.append(
                        f"- **{t.symbol}**: Edge decaying — recent WR {decay['recent_wr']}% "
                        f"vs historical {decay['historical_wr']}% (decay: {decay['decay_pct']}pp)"
                    )
        if not any("Edge decaying" in l for l in lines):
            lines.append("No significant edge decay detected.")

    return "\n".join(lines)


# ── Config Snapshot ────────────────────────────────────────────────────────

def snapshot_config(symbol, config_dict, edge_strength=0, best_ev=0.0, best_combo=""):
    """Save a snapshot of a ticker's config for historical tracking."""
    ConfigSnapshot.create(
        symbol=symbol,
        config_yaml=yaml.dump(config_dict, default_flow_style=False),
        edge_strength=edge_strength,
        best_ev=best_ev,
        best_combo=best_combo,
    )
