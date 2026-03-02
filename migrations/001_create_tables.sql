-- Intraday Scanner — Supabase Tables
-- Run this once in the Supabase SQL Editor.

-- 1. Trades — enriched signal log
CREATE TABLE IF NOT EXISTS trades (
    id              BIGSERIAL PRIMARY KEY,
    signal_time     TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL DEFAULT 'long',
    phase           TEXT DEFAULT '',
    gap_type        TEXT DEFAULT '',
    gap_pct         REAL DEFAULT 0.0,
    edge_strength   INTEGER DEFAULT 0,
    kelly_fraction  REAL DEFAULT 0.0,
    vix_at_signal   REAL,
    nifty_regime    TEXT DEFAULT 'unknown',
    conditions_met  INTEGER DEFAULT 0,
    conditions_total INTEGER DEFAULT 0,
    weighted_score  REAL DEFAULT 0.0,
    entry_price     REAL DEFAULT 0.0,
    target_price    REAL DEFAULT 0.0,
    stop_price      REAL DEFAULT 0.0,
    recommended_qty INTEGER DEFAULT 0,
    capital_at_risk REAL DEFAULT 0.0,
    status          TEXT DEFAULT 'signal',
    actual_entry    REAL,
    actual_exit     REAL,
    actual_qty      INTEGER,
    exit_time       TIMESTAMPTZ,
    exit_reason     TEXT,
    slippage_entry  REAL,
    slippage_exit   REAL,
    pnl             REAL,
    pnl_pct         REAL,
    mae_pct         REAL,
    -- New enriched columns
    strategy        TEXT,
    rr_ratio        REAL,
    target_pct      REAL,
    stop_pct        REAL,
    conditions      JSONB,
    day_type        TEXT,
    dow_name        TEXT,
    dow_wr          REAL,
    month_period    TEXT,
    month_period_wr REAL,
    symbol_regime   JSONB,
    signal_tier     TEXT,
    signal_reason   TEXT,
    ltp             REAL,
    change_pct      REAL,
    sector          TEXT,
    scanner_type    TEXT DEFAULT 'intraday'
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);
CREATE INDEX IF NOT EXISTS idx_trades_signal_time ON trades (signal_time);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades (status);
CREATE INDEX IF NOT EXISTS idx_trades_scanner_type ON trades (scanner_type);
CREATE INDEX IF NOT EXISTS idx_trades_signal_tier ON trades (signal_tier);

-- 2. Daily performance
CREATE TABLE IF NOT EXISTS daily_performance (
    id                  BIGSERIAL PRIMARY KEY,
    date                DATE UNIQUE NOT NULL,
    total_trades        INTEGER DEFAULT 0,
    wins                INTEGER DEFAULT 0,
    losses              INTEGER DEFAULT 0,
    gross_pnl           REAL DEFAULT 0.0,
    net_pnl             REAL DEFAULT 0.0,
    max_drawdown_pct    REAL DEFAULT 0.0,
    nifty_regime        TEXT DEFAULT 'unknown',
    vix_close           REAL,
    notes               TEXT DEFAULT '',
    scanner_type        TEXT DEFAULT 'intraday'
);

CREATE INDEX IF NOT EXISTS idx_daily_perf_date ON daily_performance (date);

-- 3. Scan runs — one row per scanner execution
CREATE TABLE IF NOT EXISTS scan_runs (
    id              BIGSERIAL PRIMARY KEY,
    run_time        TIMESTAMPTZ NOT NULL DEFAULT now(),
    scanner_type    TEXT NOT NULL DEFAULT 'intraday',
    vix_val         REAL,
    vix_regime      TEXT,
    nifty_regime    TEXT,
    day_type        TEXT,
    dow             TEXT,
    month_period    TEXT,
    total_candidates INTEGER DEFAULT 0,
    strong_count    INTEGER DEFAULT 0,
    active_count    INTEGER DEFAULT 0,
    report_markdown TEXT,
    ai_advisory     TEXT
);

CREATE INDEX IF NOT EXISTS idx_scan_runs_time ON scan_runs (run_time);
CREATE INDEX IF NOT EXISTS idx_scan_runs_type ON scan_runs (scanner_type);

-- 4. Config snapshots
CREATE TABLE IF NOT EXISTS config_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    created         TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol          TEXT NOT NULL,
    config_yaml     TEXT,
    edge_strength   INTEGER DEFAULT 0,
    best_ev         REAL DEFAULT 0.0,
    best_combo      TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_config_snap_symbol ON config_snapshots (symbol);
