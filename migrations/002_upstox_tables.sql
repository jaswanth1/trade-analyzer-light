-- Upstox Integration — Supabase Tables
-- Run this once in the Supabase SQL Editor.

-- 1. Upstox tokens — OAuth access token persistence
CREATE TABLE IF NOT EXISTS upstox_tokens (
    id              BIGSERIAL PRIMARY KEY,
    access_token    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_upstox_tokens_created ON upstox_tokens (created_at DESC);

-- 2. OHLCV cache — avoids repeated API calls across scanners
CREATE TABLE IF NOT EXISTS ohlcv_cache (
    symbol          TEXT NOT NULL,
    interval        TEXT NOT NULL,
    bar_time        TIMESTAMPTZ NOT NULL,
    open            DOUBLE PRECISION,
    high            DOUBLE PRECISION,
    low             DOUBLE PRECISION,
    close           DOUBLE PRECISION,
    volume          BIGINT,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (symbol, interval, bar_time)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_cache_symbol_interval ON ohlcv_cache (symbol, interval);
CREATE INDEX IF NOT EXISTS idx_ohlcv_cache_freshness ON ohlcv_cache (symbol, interval, bar_time DESC);

-- 3. Analysis cache — persists expensive computed metrics across scanner runs
CREATE TABLE IF NOT EXISTS analysis_cache (
    metric      TEXT NOT NULL,              -- e.g. "vix", "symbol_regime", "correlation_clusters"
    symbol      TEXT NOT NULL DEFAULT '',   -- empty for market-level, "RELIANCE.NS" for per-symbol
    params      TEXT NOT NULL DEFAULT '',   -- extra key: "orb|long|trend_up" for hit_rate, etc.
    payload     JSONB NOT NULL,             -- the cached result (any shape)
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (metric, symbol, params)
);

CREATE INDEX IF NOT EXISTS idx_analysis_cache_metric ON analysis_cache (metric);
CREATE INDEX IF NOT EXISTS idx_analysis_cache_freshness ON analysis_cache (metric, symbol, computed_at DESC);

-- 4. Upstox BOD instruments — NSE equity symbol ↔ instrument key mapping
CREATE TABLE IF NOT EXISTS upstox_instruments (
    trading_symbol  TEXT NOT NULL,           -- e.g. "RELIANCE"
    instrument_key  TEXT NOT NULL,           -- e.g. "NSE_EQ|INE002A01018"
    isin            TEXT NOT NULL DEFAULT '',
    exchange_token  TEXT NOT NULL DEFAULT '',
    lot_size        INTEGER NOT NULL DEFAULT 1,
    tick_size       DOUBLE PRECISION NOT NULL DEFAULT 0.05,
    fetched_date    DATE NOT NULL DEFAULT CURRENT_DATE,

    PRIMARY KEY (trading_symbol)
);

CREATE INDEX IF NOT EXISTS idx_upstox_instruments_date ON upstox_instruments (fetched_date);
