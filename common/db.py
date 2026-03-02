"""
Supabase (PostgreSQL) persistence layer — via psycopg2 session pooler.

Uses SUPABASE_DB_URL (session pooler) for direct Postgres access.
"""

import json
import os
from datetime import datetime, timedelta
from functools import lru_cache

from dotenv import load_dotenv


class _NumpySafeEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types."""
    def default(self, obj):
        try:
            import numpy as np
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                return float(obj)
            if isinstance(obj, np.bool_):
                return bool(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass
        return super().default(obj)

load_dotenv()


# ── Connection ────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_conn():
    """Return a cached psycopg2 connection to Supabase via session pooler."""
    import psycopg2

    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        raise RuntimeError("Missing SUPABASE_DB_URL in env")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    return conn


def _get_cursor():
    """Get a cursor, reconnecting if the connection was lost."""
    import psycopg2

    conn = _get_conn()
    try:
        conn.isolation_level  # triggers check
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        _get_conn.cache_clear()
        conn = _get_conn()
    return conn.cursor()


def _supabase_available():
    """Quick check — can we connect?"""
    try:
        _get_cursor().close()
        return True
    except Exception:
        return False


# ── Helpers ───────────────────────────────────────────────────────────────

def _sanitize_val(v):
    """Convert numpy/pandas types to native Python for psycopg2."""
    if v is None:
        return None
    try:
        import numpy as np
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
        if isinstance(v, np.bool_):
            return bool(v)
        if isinstance(v, np.ndarray):
            return v.tolist()
    except ImportError:
        pass
    return v


def _insert(table, row):
    """Insert a dict into a table and return the row with id."""
    cur = _get_cursor()
    cols = list(row.keys())
    vals = [_sanitize_val(v) for v in row.values()]
    placeholders = ", ".join(["%s"] * len(cols))
    col_names = ", ".join(cols)

    cur.execute(
        f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) RETURNING *",
        vals,
    )
    result = cur.fetchone()
    if result:
        desc = [d[0] for d in cur.description]
        return dict(zip(desc, result))
    return None


def _select(table, columns="*", where=None, params=None, order=None, limit=None):
    """Simple SELECT helper. Returns list of dicts."""
    sql = f"SELECT {columns} FROM {table}"
    if where:
        sql += f" WHERE {where}"
    if order:
        sql += f" ORDER BY {order}"
    if limit:
        sql += f" LIMIT {limit}"

    cur = _get_cursor()
    cur.execute(sql, params or [])
    if not cur.description:
        return []
    desc = [d[0] for d in cur.description]
    return [dict(zip(desc, r)) for r in cur.fetchall()]


def _update(table, updates, where, params):
    """Simple UPDATE helper."""
    set_clause = ", ".join(f"{k} = %s" for k in updates.keys())
    vals = [_sanitize_val(v) for v in updates.values()] + [_sanitize_val(v) for v in params]
    cur = _get_cursor()
    cur.execute(f"UPDATE {table} SET {set_clause} WHERE {where}", vals)


# ── Signal Logging ────────────────────────────────────────────────────────

def log_signal_supa(*, candidate, vix_val=None, nifty_regime="unknown",
                    scanner_type="intraday"):
    """Insert enriched signal into `trades` table."""
    c = candidate

    row = {
        "symbol": c["symbol"],
        "direction": c["direction"],
        "phase": f"{scanner_type.upper()}_{c['strategy'].upper()}",
        "strategy": c["strategy"],
        "edge_strength": 5 if c.get("signal") == "STRONG" else 4,
        "vix_at_signal": vix_val,
        "nifty_regime": nifty_regime,
        "conditions_met": sum(
            1 for v in c.get("conditions", {}).values()
            if (isinstance(v, dict) and v.get("met")) or (isinstance(v, bool) and v)
        ),
        "conditions_total": len(c.get("conditions", {})),
        "weighted_score": c.get("score", 0),
        "entry_price": c["entry_price"],
        "target_price": c["target_price"],
        "stop_price": c["stop_price"],
        "recommended_qty": c.get("recommended_qty", 0),
        "capital_at_risk": c.get("capital_at_risk", 0),
        "status": "signal",
        "rr_ratio": c.get("rr_ratio", 0),
        "target_pct": c.get("target_pct", 0),
        "stop_pct": c.get("stop_pct", 0),
        "conditions": json.dumps(c.get("conditions", {}), cls=_NumpySafeEncoder),
        "day_type": c.get("day_type", ""),
        "dow_name": c.get("dow_name", ""),
        "dow_wr": c.get("dow_wr"),
        "month_period": c.get("month_period", ""),
        "month_period_wr": c.get("month_period_wr"),
        "symbol_regime": json.dumps(c.get("symbol_regime", {}), cls=_NumpySafeEncoder),
        "signal_tier": c.get("signal", ""),
        "signal_reason": c.get("signal_reason", ""),
        "ltp": c.get("ltp"),
        "change_pct": c.get("change_pct"),
        "sector": c.get("sector", ""),
        "scanner_type": scanner_type,
        "confidence": c.get("confidence", 0),
        "news_sentiment": c.get("news_sentiment"),
        "convergence_score": c.get("convergence_score"),
        "historical_hit_rate": c.get("historical_hit_rate"),
    }

    return _insert("trades", row)


# ── Scan Run Logging ──────────────────────────────────────────────────────

def log_scan_run(*, scanner_type="intraday", vix_val=None, vix_regime=None,
                 nifty_regime=None, day_type=None, dow=None, month_period=None,
                 total_candidates=0, strong_count=0, active_count=0,
                 report_markdown=None, ai_advisory=None):
    """Insert a row into `scan_runs` for this scanner execution."""
    row = {
        "scanner_type": scanner_type,
        "vix_val": vix_val,
        "vix_regime": vix_regime,
        "nifty_regime": nifty_regime,
        "day_type": day_type,
        "dow": dow,
        "month_period": month_period,
        "total_candidates": total_candidates,
        "strong_count": strong_count,
        "active_count": active_count,
        "report_markdown": report_markdown,
        "ai_advisory": ai_advisory,
    }

    return _insert("scan_runs", row)


# ── Portfolio Metrics ─────────────────────────────────────────────────────

def get_portfolio_metrics_supa(days=30, scanner_type="intraday"):
    """Aggregate metrics from `trades` table."""
    import numpy as np

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    rows = _select(
        "trades", "*",
        where="status = %s AND scanner_type = %s AND exit_time >= %s",
        params=["closed", scanner_type, cutoff],
        order="exit_time",
    )

    if not rows:
        return {
            "n_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "gross_pnl": 0, "avg_pnl_pct": 0, "days": days,
        }

    pnls = [r["pnl"] for r in rows if r.get("pnl") is not None]
    pnl_pcts = [r["pnl_pct"] for r in rows if r.get("pnl_pct") is not None]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p <= 0)

    return {
        "n_trades": len(rows),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(rows) * 100, 1) if rows else 0,
        "gross_pnl": round(sum(pnls), 2),
        "avg_pnl_pct": round(float(np.mean(pnl_pcts)), 2) if pnl_pcts else 0,
        "days": days,
    }


# ── Close Trade ───────────────────────────────────────────────────────────

def close_trade_supa(trade_id, actual_exit, exit_reason="manual"):
    """Update a trade's status to closed."""
    rows = _select("trades", "*", where="id = %s", params=[trade_id])
    if not rows:
        return None

    trade = rows[0]
    entry = trade.get("actual_entry") or trade.get("entry_price", 0)
    qty = trade.get("actual_qty") or 1
    direction = trade.get("direction", "long")

    if direction == "long":
        pnl = (actual_exit - entry) * qty
        pnl_pct = (actual_exit / entry - 1) * 100 if entry > 0 else 0
    else:
        pnl = (entry - actual_exit) * qty
        pnl_pct = (entry / actual_exit - 1) * 100 if actual_exit > 0 else 0

    updates = {
        "status": "closed",
        "actual_exit": actual_exit,
        "exit_time": datetime.now().isoformat(),
        "exit_reason": exit_reason,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
    }

    _update("trades", updates, "id = %s", [trade_id])
    return {**trade, **updates}


# ── Today's Realized P&L ─────────────────────────────────────────────────

def get_today_realized_pnl(scanner_type="intraday"):
    """Sum of P&L for trades closed today. Used for drawdown enforcement."""
    today = datetime.now().strftime("%Y-%m-%d")

    rows = _select(
        "trades", "pnl",
        where="status = %s AND scanner_type = %s AND exit_time >= %s",
        params=["closed", scanner_type, f"{today}T00:00:00"],
    )

    return sum(r["pnl"] for r in rows if r.get("pnl") is not None)


# ── Today's Trades (for risk controls) ────────────────────────────────────

def get_today_trades(scanner_type="intraday"):
    """Fetch all closed trades from today. Used for P&L velocity, strategy budgets,
    and repeat-entry guard."""
    today = datetime.now().strftime("%Y-%m-%d")

    return _select(
        "trades", "symbol, strategy, pnl, exit_reason, exit_time",
        where="status = %s AND scanner_type = %s AND exit_time >= %s",
        params=["closed", scanner_type, f"{today}T00:00:00"],
    )
