# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Trading system for Indian equity markets (NSE) with intraday scalp scanner, overnight BTST analyzer, and intra-week swing scanner. Organized into packages under `common/`, `scalp/`, `btst/`, and `intra_week/`.

## Environment

- Python 3.14 via Homebrew
- Package manager: `uv` (dependencies declared in `pyproject.toml`)
- Virtual environment: `.venv/`
- Setup: `uv sync`
- Add a package: `uv add <package>`

## Project Structure

- `common/` — shared utilities (data fetching, indicators, market, risk, journal, display)
- `scalp/` — intraday scalp scanner, report generator, config builder, backtester
- `intraday/` — time-aware intraday scanner (5 strategies, 4 market phases)
- `btst/` — BTST (Buy Today Sell Tomorrow) scanner
- `intra_week/` — intra-week swing scanner (3 strategies: oversold recovery, vol compression, weekly context)
- `main.py` — FastAPI app (unrelated to trading)

## Running

- **Intraday scanner**: `python -m intraday.scanner` (auto-detects phase: pre_market/pre_live/live/post_market)
- **Intraday scanner (force live)**: `python -m intraday.scanner --force`
- **Market data report**: `python -m intraday.market_data` (fetches global/India/sector/commodity data, universe movers, conditional triggers)
- **Config staleness check**: `python -m intraday.config_check` (checks MLR + scalp config freshness and ticker mismatches)
- **Intraday backtest (last week)**: `python -m intraday.backtest --last-week` (auto-computes last 5 trading days)
- **Scalp reports**: `python -m scalp.report`
- **Scalp config**: `python -m scalp.config`
- **Scalp scanner**: `python -m scalp.scanner`
- **Scalp backtest**: `python -m scalp.backtest`
- **BTST scanner**: `python -m btst.scanner [--force]`
- **IntraWeek scanner**: `python -m intra_week.scanner` (best on Mon/Tue)
- **IntraWeek scanner (force)**: `python -m intra_week.scanner --force` (run any day)
- **IntraWeek backtest**: `python -m intra_week.backtest --start 2025-01-01 --end 2026-04-01`
- **IntraWeek backtest (last quarter)**: `python -m intra_week.backtest --last-quarter`
- **Trade universe builder**: `python -m common.universe` (NOT in active use — overwrites manual curation with 1400+ auto-screened stocks)
- **Trade universe (force refresh)**: `python -m common.universe --force` (NOT in active use — re-downloads MTF + re-fetches all data)
- **Trade universe (decision)**: Universe is manually curated in `common/universe.yaml` via broker watchlist exports
- **DB maintenance (retention cleanup)**: `python -m common.db_maintenance` (deletes stale rows from all tables)
- **DB maintenance (dry run)**: `python -m common.db_maintenance --dry-run` (shows what would be deleted)
- **Backfill OHLCV cache**: `python -m scripts.backfill_ohlcv` (one-time: fetches 1y of 1d+5m bars for 122 symbols)
- **Backfill specific symbols**: `python -m scripts.backfill_ohlcv --symbols RELIANCE.NS SBIN.NS`
- **FastAPI app**: `uvicorn main:app --reload`

## Key Dependencies

- **yfinance** + **pandas** + **numpy**: Stock data fetching and analysis
- **pyyaml**: Config management (`scalp_config.yaml`)
- **peewee**: SQLite trade journal (`scalp_journal.db`)
- **scipy** + **scikit-learn**: Statistical analysis in config generator
- **openai** / **requests**: LLM advisory (Ollama local or OpenAI cloud)
- **fastapi** + **uvicorn**: Web API (`main.py`)
- **upstox-python-sdk**: Upstox broker API integration
