# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Trading system for Indian equity markets (NSE) with intraday scalp scanner and overnight BTST analyzer. Organized into packages under `common/`, `scalp/`, and `btst/`.

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
- `main.py` — FastAPI app (unrelated to trading)

## Running

- **Intraday scanner**: `python -m intraday.scanner` (auto-detects phase: pre_market/pre_live/live/post_market)
- **Intraday scanner (force live)**: `python -m intraday.scanner --force`
- **Scalp reports**: `python -m scalp.report`
- **Scalp config**: `python -m scalp.config`
- **Scalp scanner**: `python -m scalp.scanner`
- **Scalp backtest**: `python -m scalp.backtest`
- **BTST scanner**: `python -m btst.scanner [--force]`
- **FastAPI app**: `uvicorn main:app --reload`

## Key Dependencies

- **yfinance** + **pandas** + **numpy**: Stock data fetching and analysis
- **pyyaml**: Config management (`scalp_config.yaml`)
- **peewee**: SQLite trade journal (`scalp_journal.db`)
- **scipy** + **scikit-learn**: Statistical analysis in config generator
- **openai** / **requests**: LLM advisory (Ollama local or OpenAI cloud)
- **fastapi** + **uvicorn**: Web API (`main.py`)
- **upstox-python-sdk**: Upstox broker API integration
