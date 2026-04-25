-- Migration 003: Convert ohlcv_cache to TimescaleDB hypertable
-- Run against Supabase: psql $SUPABASE_DB_URL -f migrations/003_timescaledb.sql
--
-- This drops the existing ohlcv_cache table and recreates it as a
-- TimescaleDB hypertable with compression and retention policies.
-- Data will be backfilled from yfinance after this migration.

-- Step 1: Enable TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Step 2: Drop old table and recreate
DROP TABLE IF EXISTS ohlcv_cache;

CREATE TABLE ohlcv_cache (
    symbol     TEXT NOT NULL,
    interval   TEXT NOT NULL,
    bar_time   TIMESTAMPTZ NOT NULL,
    open       DOUBLE PRECISION,
    high       DOUBLE PRECISION,
    low        DOUBLE PRECISION,
    close      DOUBLE PRECISION,
    volume     BIGINT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, interval, bar_time)
);

SELECT create_hypertable(
    'ohlcv_cache',
    by_range('bar_time'),
    chunk_time_interval => INTERVAL '7 days'
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_cache_symbol_interval
    ON ohlcv_cache (symbol, interval);
CREATE INDEX IF NOT EXISTS idx_ohlcv_cache_freshness
    ON ohlcv_cache (symbol, interval, bar_time DESC);

-- Step 3: Enable compression
ALTER TABLE ohlcv_cache SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol, interval',
    timescaledb.compress_orderby = 'bar_time DESC'
);

-- Step 4: Auto-compress chunks older than 7 days
SELECT add_compression_policy(
    'ohlcv_cache',
    compress_after => INTERVAL '7 days'
);

-- Step 5: Auto-drop chunks older than 365 days
SELECT add_retention_policy(
    'ohlcv_cache',
    drop_after => INTERVAL '365 days'
);
