# Trading System

Intraday scalp scanner and overnight BTST analyzer for Indian equity markets (NSE).

## Project Structure

```
common/              Shared utilities
  data.py            OHLCV fetching (yfinance + Upstox gap-fill + Supabase cache)
  universe.py        Trade universe builder (MTF screening → universe.yaml)
  data_cache.py      Supabase-backed OHLCV bar cache
  analysis_cache.py  Supabase-backed analysis metric cache (VIX, regimes, hit rates)
  indicators.py      Technical indicators (ATR, VWAP, gaps)
  market.py          Market-level utilities (VIX, Nifty regime, institutional flow, earnings)
  risk.py            Position sizing, correlation clusters, portfolio heat
  upstox.py          Upstox API integration (auth, token management, REST data)
  upstox_symbols.py  yfinance ↔ Upstox instrument key mapping
  db.py              Supabase (PostgreSQL) persistence layer
  llm.py             LLM advisory (Ollama / OpenAI / any OpenAI-compatible endpoint)
  display.py         Terminal box-drawing utilities
  journal.py         SQLite trade journal (fallback)
  news.py            Stock news fetching + LLM sentiment scoring
scalp/               Intraday scalp trading
  config.py          Fetch OHLCV → compute indicators → cache → generate scalp_config.yaml
  scanner.py         Live scalp scanner with LLM advisory
  backtest.py        Replay historical bars through scanner logic
btst/                Buy Today Sell Tomorrow
  scanner.py         BTST signal scanner (last 90 min of market)
  regime.py          Overnight DOW/month-period statistics
  convergence.py     Daily convergence scoring + overnight hit rate
  explanations.py    Educational + LLM explanations for BTST
intra_week/          IntraWeek swing scanner (oversold recovery, vol compression, weekly context)
intraday/            Time-aware multi-strategy intraday & swing scanner
  scanner.py         Main orchestrator: phase detection → evaluate → rank → dashboard → report
  backtest.py        Replay historical days through scanner pipeline, validate signals
  market_data.py     Market data report (global indices, sectors, commodities, universe movers)
  config_check.py    Config staleness checker (MLR + scalp config freshness)
  explanations.py    Template + LLM educational explanations (per-phase)
  features.py        Technical indicators (EMA, RSI, MACD, Bollinger, Keltner, squeeze)
  regime.py          Day-type + symbol-level regime classification
  strategies/        Strategy modules (ORB, pullback, compression, mean-revert, swing, MLR)
  convergence.py     Convergence scoring + historical pattern replay
  mlr_config.py      MLR config generator (precomputed per-ticker recovery stats)
migrations/          Supabase SQL migrations (run once in SQL Editor)
main.py              FastAPI app (unrelated)
```

## Setup

```bash
uv sync
```

## Trade Universe

The system uses a **systematic trade universe** built from Upstox's MTF (Margin Trading Facility) instrument list. MTF eligibility means NSE Group 1 classification — stocks traded on ≥80% of trading days with impact cost ≤1%. This provides a pre-screened pool of liquid, actively traded equities.

### Universe Builder

> **Decision (2026-04-23):** The automated universe builder is **not in active use**. It pulls 1,400+ MTF instruments and produces an overly large universe (~918 stocks). The universe is now **manually curated** in `common/universe.yaml` — stocks are added/removed by editing the YAML directly, typically from broker watchlist exports. Do NOT run these commands unless re-enabling automated universe building.

Screens 1,400+ MTF instruments through a multi-filter pipeline and assigns stocks to three strategy tiers:

```bash
python -m common.universe              # build universe (uses Supabase cache for speed)
python -m common.universe --force      # force full re-download + re-computation
python -m common.universe --dry-run    # preview results without writing files
```

**Previously run weekly** to refresh. First run takes ~5-8 minutes (fetches OHLCV + sector info for all stocks). Subsequent runs take ~30 seconds (Supabase-cached metrics + sectors).

### Filter Pipeline

```
Upstox MTF list (1,400+ stocks)
  → NSE_EQ equities only
  → Batch OHLCV fetch (yfinance, 1-month daily)
  → Compute: last price, 20-day ADTV (₹ crore), 14-day ATR%
  → Apply tier filters
  → Fetch sector classification (yfinance, threaded)
  → Sector cap (max 25% from any single sector)
  → Write common/universe.yaml + common/universe_guide.md
```

### Strategy Tiers

| Tier | ADTV Min | Price Range | ATR% Range | Target Size |
|------|----------|-------------|------------|-------------|
| **Scalp** | ₹15 Cr | ₹100-5,000 | 1.5-5.0% | ~80 stocks |
| **Intraday** | ₹8 Cr | ₹50-10,000 | 1.5-7.0% | ~120 stocks |
| **IntraWeek** | ₹5 Cr | ₹50-10,000 | 2.0-8.0% | ~150 stocks |
| **BTST** | ₹4 Cr | ₹50-10,000 | 1.0-6.0% | ~150 stocks |

### How Scanners Use It

- `common/data.py` loads `TICKERS` from `universe.yaml` automatically (intraday tier by default)
- Scanners can load tier-specific universes: `load_universe_for_tier("scalp")`
- If `universe.yaml` doesn't exist, falls back to the hardcoded 34-stock universe
- Each stock in the universe includes: sector, instrument_key (for Upstox), ISIN, ADTV, ATR%, price, and tier eligibility flags

### Caching

| Layer | What | TTL |
|-------|------|-----|
| MTF instrument list | Local file (`~/.upstox_mtf.json`) | 1 day |
| Stock metrics (price, ADTV, ATR%) | Supabase `universe_metrics` table | 1 day |
| Sector classification | Supabase `universe_sectors` table + local file | 30 days |

## Trade Plan Utilities

Helper scripts that automate data collection for the daily trade plan (see `intraday/TRADE_PLAN_PROMPT.md`):

```bash
python -m intraday.market_data         # fetch global/India/sector/commodity data + universe movers
python -m intraday.market_data --json  # JSON output for programmatic use
python -m intraday.config_check        # check MLR + scalp config staleness
python -m intraday.backtest --last-week # backtest last 5 trading days (auto-computed dates)
```

- **Market data report** fetches global indices, India markets, sector indices, commodities/FX, FII flow proxy, and universe movers. Computes conditional search triggers (VIX elevated, Brent move, etc.) and backtest date range. Output: markdown to stdout + saved to `intraday/reports/market_data_*.md`.
- **Config check** verifies `mlr_config.yaml` (stale if ≥3 days) and `scalp_config.yaml` (stale if ≥7 days), checks ticker count mismatches, and recommends regeneration commands.

## Scalp Trading Workflow

### 1. Build scalp config

Fetches OHLCV data, computes indicators (gap analysis, probability matrix, time window stats), caches results in Supabase `analysis_cache`, and generates optimal `scalp_config.yaml`. Subsequent runs skip fresh cache entries.

```bash
python -m scalp.config                      # smart cache — only recomputes stale tickers
python -m scalp.config --force              # force full recomputation
python -m scalp.config --skip-explanation   # skip the guide doc
```

### 2. Run the live scalp scanner

Evaluates all tickers against config rules, checks VIX/Nifty regime, computes position sizes, and calls LLM for advisory.

```bash
python -m scalp.scanner
```

Runs in two modes automatically:
- **Live market** (09:15-15:15) — signals, positions, conditions dashboard
- **Prep mode** (pre/post market) — next-session scenario analysis

### 3. Backtest

Replays historical 5-min bars through scanner logic. Loads data from `fetch_yf()` and `analysis_cache`. Generates `backtest_report.md`.

```bash
python -m scalp.backtest
python -m scalp.backtest --start 2026-01-01 --end 2026-02-25 --capital 500000
```

## BTST (Buy Today Sell Tomorrow)

Scans for stocks closing near day high with volume surge. Designed to run in the last 90 minutes of trading (after 2:30 PM).

```bash
python -m btst.scanner           # runs after 2:30 PM only
python -m btst.scanner --force   # manual override, runs anytime
```

### BTST Signal Types

| Signal | Criteria |
|--------|----------|
| **STRONG_BUY** | All gates pass + score >= 85% + overnight WR > 55% |
| **BUY** | All gates pass + score >= 70% |
| **WATCH** | Some conditions met, score < 70% |
| **AVOID** | Gate failed, earnings near, or VIX stress |

### BTST Gates (must all pass)

- Close in top 20% of day range
- Trading above VWAP
- Nifty not bearish / not making new lows

### BTST Risk Limits

- Max 3 concurrent BTST positions
- Max 30% of capital in BTST
- Correlation cluster limit (max 2 from same cluster)
- Auto-skip on VIX > 22 or earnings within 3 days

## Intraday Scanner

Time-aware multi-strategy scanner. Auto-detects market phase and adapts output — no CLI flags needed for mode selection. Evaluates all tickers across 6 strategies, ranks by conviction, applies portfolio risk overlays, and generates educational explanations with AI advisory.

```bash
python -m intraday.scanner            # auto-detects phase and runs
python -m intraday.scanner --force    # force LIVE mode anytime (testing)
python -m intraday.scanner --manage   # position management only
```

### Time Phases (Auto-Detected)

| Window | Phase | Output |
|--------|-------|--------|
| Before 9:00 | **PRE_MARKET** | Conditional IF-THEN gap-scenario setups from daily data |
| 9:00 - 9:15 | **PRE_LIVE** | Refined setups using pre-market auction data + institutional volume signals |
| 9:15 - 15:15 | **LIVE** | Full scanner with time-relevance per strategy |
| After 15:15 | **POST_MARKET** | Session review + tomorrow's watchlist |

### Strategies

| Strategy | Window | Day Types | Description |
|----------|--------|-----------|-------------|
| **ORB** | 9:15-12:00 | trend_up, trend_down, gap_and_go | Opening range breakout with volume confirmation |
| **Pullback** | 9:30-14:30 | trend_up, trend_down | Pullback to EMA20/VWAP in trending market |
| **Compression** | 10:00-14:00 | range_bound | Bollinger squeeze breakout (BB inside Keltner) |
| **Mean-Revert** | 10:00-14:30 | range_bound, volatile_two_sided | Reversion to VWAP from extended levels |
| **Swing** | 9:15-15:00 | trend_up, trend_down | Multi-day (1-5d) entry on daily breakout pullback |
| **MLR** | 9:30-11:30 | all day types | Morning Low Recovery — buys confirmed reversal off session low |

Strategy windows enforce time-relevance: EXPIRED = skip, FADING (>75% elapsed) = -0.05 penalty, PRIME = no penalty.

**MLR** is exempt from the `nifty_ok` gate — data shows 57% of daily lows form before 11:00 AM with avg +2.2% recovery to close, and the pattern holds in bearish markets.

### Signal Tiers

| Signal | Criteria |
|--------|----------|
| **STRONG** | Score >= 80%, RR >= 2.0, regime aligned, DOW+month favorable |
| **ACTIVE** | Score >= 65%, RR >= 1.5, regime compatible |
| **WATCH** | Score 50-65% or one gate failed |
| **AVOID** | VIX stress, earnings, illiquid, regime mismatch |

### Educational Output

Every setup includes a template-based explanation covering: stock profile (ATR in ₹, beta), strategy logic, ₹ terms per ₹1L capital, convergence breakdown, historical context, timing, risks, and verdict. Top 3 setups also get LLM-powered explanations with cricket analogies and rupee terms, adapted per phase.

If nothing qualifies, the scanner says "none" explicitly.

### Risk Limits

- Max 5 concurrent intraday positions, max 50% of capital
- Max 2 positions per sector, max 2 per correlation cluster
- Net direction cap: max 4 longs or 4 shorts
- Daily drawdown circuit breaker at -2%, P&L velocity breaker (3 losses in 30 min)
- Per-strategy daily loss budgets, repeat-entry guard
- Auto-skip on VIX > 22 or earnings within 3 days
- Hard exit at 15:00, lunch window exit for low-progress trades

Reports saved to `intraday/reports/` named by phase: `pre_market_*.md`, `pre_live_*.md`, `intraday_*.md`, `post_market_*.md`.

### Intraday Backtest

Replays historical days through the intraday scanner's phase-aware pipeline. Simulates post-market T-1, pre-market T, and live scans at 4 time points. Validates all signals against actual data (entry hit, target/stop, MFE/MAE).

```bash
python -m intraday.backtest --date 2026-02-20                        # Single day
python -m intraday.backtest --start 2026-02-10 --end 2026-02-20     # Date range
python -m intraday.backtest --last-week                              # Auto last 5 trading days
python -m intraday.backtest --date 2026-02-20 --capital 500000       # Custom capital
python -m intraday.backtest --date 2026-02-20 --llm                  # Add LLM summary
```

Reports saved to `intraday/reports/backtest_*.md`.

## IntraWeek Scanner

Systematic swing scanner targeting 10-20% upside within a single trading week. Best run on Monday or Tuesday when there are enough trading days remaining for the thesis to play out.

```bash
python -m intra_week.scanner           # run on Mon-Wed (auto day check)
python -m intra_week.scanner --force   # override day/VIX checks
```

### Strategies

| Strategy | Trigger | Target |
|----------|---------|--------|
| **Oversold Recovery** | 5%+ drawdown in 1-3 days, RSI < 35, sector divergence | Mean reversion to 2.5x ATR |
| **Vol Compression** | Bollinger bandwidth at 20d low, Keltner squeeze | Breakout to 3x ATR |
| **Weekly Context** | Mon/Tue weakness + holiday/expiry week | Calendar-driven recovery |

### Signal Tiers

| Signal | Criteria |
|--------|----------|
| **STRONG** | Score >= 80%, expected upside >= 12% |
| **ACTIVE** | Score >= 65%, expected upside >= 10% |
| **WATCH** | Score >= 50%, expected upside >= 8% |
| **AVOID** | Gate failed, earnings near, or VIX stress |

### Scoring

Composite blend: 40% weighted conditions + 25% convergence (7-indicator alignment) + 20% historical hit rate + 15% regime alignment. VIX stress and bearish market apply multiplicative penalties.

### Risk Limits

- Max 5 concurrent positions, max 40% of capital
- Max 2 positions per sector
- Auto-skip on VIX stress or earnings within 5 days
- Day-of-week gate: best Mon-Wed, skips Thu/Fri unless `--force`

### Backtest

```bash
python -m intra_week.backtest --last-quarter
python -m intra_week.backtest --start 2025-01-01 --end 2026-04-01
python -m intra_week.backtest --start 2025-06-01 --end 2026-01-01 --capital 500000
```

Reports saved to `intra_week/reports/`.

See [`intra_week/HOW_IT_WORKS.md`](intra_week/HOW_IT_WORKS.md) for full implementation details.

### MLR Config Generator

Precomputes per-ticker Morning Low Recovery statistics: recovery probabilities by time bucket, EV-optimal entry/stop/target via grid search, walk-forward OOS validation, Monte Carlo 95% CIs, and DOW/month seasonality. Output consumed by the live scanner for ticker-specific calibration.

```bash
python -m intraday.mlr_config              # process all tickers
python -m intraday.mlr_config -v           # verbose progress per ticker
python -m intraday.mlr_config -t RELIANCE.NS  # single ticker
```

Generates `intraday/mlr_config.yaml` + `intraday/mlr_config_guide.md`.

See [`intraday/HOW_IT_WORKS.md`](intraday/HOW_IT_WORKS.md) for full implementation details.

## Upstox Integration (Optional)

Upstox provides **real-time gap-fill** for intraday data. When yfinance's last bar is stale (>1 min old during market hours), the system fills the gap with Upstox live candles. It also serves as a full fallback if yfinance returns nothing.

**Without Upstox**: All scanners work fine using yfinance only. Upstox is purely additive — if no valid token exists, the system silently falls back to yfinance-only mode.

### Setup

1. Add credentials to `.env.local` (or `.env`):

```
UPSTOX_API_KEY=your-api-key
UPSTOX_API_SECRET=your-api-secret
UPSTOX_CALL_BACK_URL=http://localhost:9000/api/v1/upstox/callback
```

2. Generate an access token (valid ~22 hours, must be refreshed daily):

```bash
python -m common.upstox
```

This opens your browser to the Upstox login page. After logging in, Upstox redirects to the callback URL with an auth code. Paste the code back into the terminal. The token is saved to both Supabase (`upstox_tokens` table) and a local fallback file (`~/.upstox_token.json`).

3. Verify it's working:

```bash
python -c "from common.upstox import is_upstox_available; print(is_upstox_available())"
```

### Token Lifecycle

| Step | What Happens |
|------|-------------|
| `python -m common.upstox` | Opens browser → Upstox login → redirects with auth code |
| Paste auth code | Exchanges code for access token via Upstox API |
| Token saved | Stored in Supabase `upstox_tokens` + `~/.upstox_token.json` |
| Token used | All `fetch_yf()` calls check Upstox for real-time gap-fill |
| Token expires | After ~22 hours — run `python -m common.upstox` again next morning |

### What Uses Upstox

| Feature | Without Upstox | With Upstox |
|---------|---------------|-------------|
| Intraday OHLCV | yfinance (may lag 1-2 min) | yfinance + Upstox real-time gap-fill |
| Daily OHLCV | yfinance | yfinance (Upstox not needed) |
| Batch LTP | yfinance last close | Upstox real-time LTP |
| Full fallback | yfinance only | Upstox if yfinance returns empty |

## Trade Journal

```bash
python -c "from common.journal import init_db, get_portfolio_metrics; init_db(); print(get_portfolio_metrics())"
```

## LLM Configuration

Set in `.env` (or `scalp_config.yaml` under `global`):

```
LLM_BASE_URL=http://localhost:11434/v1   # Ollama (default)
LLM_API_KEY=not-needed
LLM_MODEL=qwen3:8b
```

Switch to any OpenAI-compatible provider by changing the URL and key:

```
# Lightning AI
LLM_BASE_URL=https://lightning.ai/api/v1
LLM_API_KEY=your-key
LLM_MODEL=lightning-ai/gpt-oss-20b

# OpenAI
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-key
LLM_MODEL=gpt-4.1
```

All scanners share the same `common/llm.py` module.
