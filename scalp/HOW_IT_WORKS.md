# Scalp Scanner — How It Works

## What It Does

The scalp scanner is a **config-driven, probabilistic intraday scalping system** for NSE equities. It identifies short-duration trade setups (target hold: 15-45 minutes) based on statistically validated gap behavior, time-of-day edges, and market microstructure conditions.

The system operates in a **two-stage pipeline**: config (fetch → compute → cache → YAML) → scanner.

---

## Strategy: Gap-Based Probabilistic Scalping

The core insight is that different **opening gap types** (flat, small gap-up, small gap-down, large gap-up, large gap-down) produce statistically different intraday outcomes. By analyzing 6 months of historical data, the system identifies which gap + time-window + target/stop combinations have a genuine edge.

### How the Edge is Discovered

1. **Gap Classification** — Each trading day is classified by how the stock opens relative to the previous close: flat (±0.3%), small gap (0.3-1%), or large gap (>1%), in either direction.

2. **First-Touch Probability Matrix** — For every combination of target % and stop %, the system simulates: starting from the day's open, which price level gets hit first? This produces a probability matrix across all gap types.

3. **Time-of-Day Windows** — The trading day is divided into 6 windows (09:15-10:00, 10:00-11:30, etc.). Each window has different volatility, volume, and directional characteristics. The system identifies which windows have statistically significant win rates.

4. **Bayesian + FDR-Corrected Selection** — Raw hit rates are adjusted with Bayesian priors (Beta distribution) to avoid overfitting on small samples. All combos are then tested with Benjamini-Hochberg false discovery rate correction to ensure the edge isn't just noise.

5. **Walk-Forward Validation** — The best combos are validated out-of-sample: train on the first 70% of data, test on the remaining 30%. If performance degrades, the edge strength is penalized.

6. **Expected Value with Costs** — Each combo's EV includes round-trip transaction costs (brokerage + STT + slippage ≈ 0.10%). Only positive-EV combos survive.

### The Strategy in Practice

On any given day, the scanner evaluates each ticker against its optimal configuration:

- **Is the gap type favorable?** — The current day's gap must match one of the statistically validated gap types for this ticker and time phase.
- **Is price above VWAP?** — VWAP acts as the "fair price" for the day. Trading above it confirms bullish intraday bias.
- **Has VWAP been reclaimed?** — If price dipped below VWAP and crossed back above (holding for 2 bars), it signals buyers stepping in.
- **Are higher lows forming?** — The current intraday swing low must be above the opening 15-minute low — a structural bullish signal.
- **Is Nifty supportive?** — The benchmark must not be in bearish mode or making fresh lows.
- **Is volume adequate?** — Adjusted for time-of-day seasonality (morning volume is naturally 2x afternoon).
- **Is the move not overextended?** — Price must not have already moved beyond a threshold fraction of ATR from open.
- **Is the stock outperforming Nifty?** — Relative strength vs benchmark computed intraday.

### Conditions Evaluated

Each ticker is checked against **must-have gates** and **weighted signals**:

**Must-Have Gates** (all must pass):

| Gate | What It Checks |
|------|---------------|
| Gap preferred | Today's gap type matches phase's statistically validated gap types |
| Above VWAP | Current price above the day's volume-weighted average price |
| Nifty OK | Benchmark not making fresh intraday lows |

**Weighted Conditions** (scored by importance):

| Condition | Weight | What It Checks |
|-----------|--------|---------------|
| Gap preferred | 3.0 | (also a gate — double-weighted for scoring) |
| Above VWAP | 2.5 | (also a gate) |
| Nifty OK | 2.5 | (also a gate) |
| VWAP reclaimed | 2.0 | Price crossed above VWAP from below and held 2 bars |
| Higher low | 1.5 | Current swing low > opening 15-min low |
| Move not extended | 1.5 | Day's move from open ≤ max_move_from_open_pct |
| Volume OK | 1.0 | Volume ≥ min_volume_ratio × seasonality-adjusted median |
| Range OK | 1.0 | Day range ≥ min_range × ATR% |
| RS positive | 0.5 | Stock outperforming Nifty intraday |
| Volume expansion | 0.5 | Volume regime = "Expansion" (≥1.5× median) |

### Signal Classification

Based on gate passage and weighted score:

| Signal | Criteria |
|--------|----------|
| **ACTIVE** | All gates pass + all conditions met |
| **WATCH** | Gates pass but weighted score ≥ 75%, or some conditions missing |
| **NO_TRADE** | A gate is blocked, or conditions below threshold |
| **STAND_ASIDE** | Range compression or volume contraction — no edge |
| **AVOID** | Phase is historically negative for this ticker |

### Scoring and Ranking

Active signals are ranked by a composite score:
```
score = edge_strength × 10 + weighted_score × 5
```
Edge strength (1-5) comes from the config generator's statistical analysis. Weighted score (0-1) reflects current market conditions.

### Transaction Cost Model

Round-trip NSE costs (0.08% for MIS intraday) are **deducted from target prices** before computing the reward-risk ratio. This ensures marginal setups with thin RR get naturally filtered. The RR ratio displayed in the dashboard and reports is always net of costs.

### Position Management

Once in a trade:

- **Breakeven stop** at 50% of target distance
- **Trailing stop** at 75% of target distance (entry + 25% of target distance)
- **Time exit** after 45 minutes if target not reached and P&L < 0.1%
- **Hard flatten** at 15:15 (market close)

---

## How the Config is Built

The config generator (`scalp/config.py`) is the entry point and most statistically rigorous part of the system. It fetches OHLCV data via `fetch_yf()` (which uses Supabase OHLCV cache + Upstox gap-fill), computes all indicators (gap classification, probability matrix, time window stats), caches intermediate results in Supabase `analysis_cache`, and generates `scalp_config.yaml`. On subsequent runs, fresh cache entries are skipped — only stale symbols are recomputed.

### Statistical Methods

| Method | Library | Purpose |
|--------|---------|---------|
| Bayesian hit rate adjustment | numpy | Beta(hits+1, misses+1) posterior mean — smooths noisy win rates |
| Binomial test | scipy.stats.binomtest | Tests whether a time window's win rate is significantly above 50% |
| Benjamini-Hochberg FDR | custom implementation | Controls false discovery rate when testing many target/stop combos |
| Walk-forward split | pandas | 70/30 temporal train-test split for out-of-sample validation |
| Monte Carlo bootstrap | numpy.random | 10,000 resampled P&L series to compute Kelly fraction confidence intervals |
| PCA | scikit-learn | Groups tickers by behavioral similarity across time-window stats |

### Edge Strength Score (1-5)

A composite score assigned to each ticker:

- EV magnitude (higher EV = stronger edge)
- Win rate above baseline
- Sample size sufficiency
- Out-of-sample degradation penalty
- Trap safety (how often gap-type setups reverse against you)

Tickers with edge strength < 3 or overall tradability score < 55 are disabled.

### Per-Ticker Config Output

Each ticker in `scalp_config.yaml` gets:

- Optimal target/stop in both % and ATR-multiples
- Active phases (time windows with statistically significant win rates)
- Avoid phases (time windows with negative edge)
- Gap rules per phase (which gap types are favorable)
- Kelly fraction for position sizing
- DOW-specific avoid rules (e.g., skip Mondays if win rate < 40%)

---

## Risk Management

| Layer | Mechanism |
|-------|-----------|
| Position sizing | Half-Kelly criterion × VIX scale × beta scale |
| Max risk per trade | 0.5% of capital |
| Sector concentration | Max 2 trades from the same sector/regime tag |
| Correlation clusters | Max 2 trades from the same correlation cluster (20-day returns, threshold 0.6) |
| VIX regime | Scale down at elevated VIX, stop trading at VIX > 22 (stress) |
| **VIX failure default** | If VIX unavailable, use conservative 0.7× (not 1.0×) |
| Daily drawdown | Hard stop if cumulative daily loss (open P&L + realized P&L) exceeds 1.5% |
| Nifty regime filter | High-beta tickers disabled when Nifty is bearish |
| Earnings proximity | Skip tickers with earnings within 3 days |
| DOW filter | Skip if historical win rate on this weekday < 40% |
| **Transaction cost model** | 0.08% NSE round-trip cost deducted from targets for realistic RR |

### Volume Seasonality Adjustment

Volume is compared to the 20-day median, but adjusted for time-of-day patterns:

| Window | Multiplier | Rationale |
|--------|-----------|-----------|
| 09:15-10:00 | 2.0× | Morning naturally has 2x daily average volume |
| 10:00-11:30 | 1.3× | Still elevated |
| 11:30-12:30 | 0.8× | Pre-lunch fade |
| 12:30-13:30 | 0.6× | Lunch break — lowest liquidity |
| 13:30-14:30 | 0.8× | Pre-close buildup |
| 14:30-15:15 | 1.2× | Closing momentum |

This prevents false "volume contraction" signals during naturally low-volume periods.

---

## Two Operating Modes

### Live Market Mode (09:15-15:30)

During market hours, the scanner evaluates all tickers against current conditions and produces:
- Signal classification (ACTIVE / WATCH / NO_TRADE / STAND_ASIDE / AVOID)
- Position sizing with Kelly criterion
- Open position P&L tracking with dynamic stop management
- AI advisory with specific trade recommendations

### Next-Session Prep Mode (before 09:15 / after 15:30)

Outside market hours, the scanner switches to preparation mode:
- Computes gap scenario probabilities for each ticker
- Maps each scenario to tradeable phases and historical hit rates
- Identifies key levels (today's high/low/VWAP)
- Finds the best time window and win rate from cached analysis
- AI generates conditional plans: "If X opens with Y gap, do Z"

---

## Data Pipeline

| Data | Source | Cache | Granularity | Lookback |
|------|--------|-------|-------------|----------|
| Intraday OHLCV | yfinance + Upstox gap-fill | Supabase OHLCV cache | 5-minute bars | 60 days |
| Daily OHLCV | yfinance + Upstox fallback | Supabase OHLCV cache | Daily bars | 6 months |
| Gap analysis | Computed by `config.py` | Supabase `analysis_cache` | Per-day | 6 months |
| Probability matrix | Computed by `config.py` | Supabase `analysis_cache` | Per-day×combo | 60 days |
| Time window stats | Computed by `config.py` | Supabase `analysis_cache` | Per-window | 60 days |
| Metadata/scores | Computed by `config.py` | Supabase `analysis_cache` | Per-ticker | Latest |
| India VIX | yfinance (`^INDIAVIX`) | — | Daily | 5 days |
| Nifty 50 | yfinance (`^NSEI`) | — | 5-min + daily | 5 days / 2 months |
| Sector indices | yfinance (e.g., `^CNXFIN`) | — | Daily | 5 days |
| Ticker info | yfinance `.info` | — | Snapshot | Latest |

All timestamps are converted to IST (Asia/Kolkata). VWAP resets daily. Analysis cache entries have a 24-hour TTL — `config.py --force` bypasses this for full recomputation.

---

## Output

All scalp-related output files are created inside the `scalp/` directory, keeping the project root clean. Intermediate analysis data (gap analysis, probability matrices, time window stats, metadata) is cached in Supabase `analysis_cache` — no local CSV files needed.

| Output | Format | Location |
|--------|--------|----------|
| Analysis cache | JSONB | Supabase `analysis_cache` table |
| Config file | YAML | `scalp/scalp_config.yaml` |
| Config guide | Markdown | `scalp/scalp_config_guide.md` |
| Backtest report | Markdown | `scalp/backtest_report.md` |
| Scanner reports | Markdown | `scalp/reports/scalp_report_YYYY-MM-DD_HHMM.md` |
| Dashboard | Box-drawing terminal UI | stdout |
| AI advisory | LLM-generated text | Embedded in report + dashboard |
| Supabase tables | PostgreSQL | `trades`, `scan_runs` |
| SQLite journal | Peewee/SQLite | `scalp_journal.db` (fallback) |

### Directory Layout

```
scalp/
├── reports/             # Scanner run reports (from scalp.scanner)
│   ├── scalp_report_2026-02-26_1030.md
│   └── ...
├── scalp_config.yaml    # Generated config (from scalp.config)
├── scalp_config_guide.md # Config documentation (from scalp.config)
├── backtest_report.md   # Backtest results (from scalp.backtest)
├── scanner.py
├── config.py
├── report.py           # DEPRECATED — kept for reference
├── backtest.py
└── HOW_IT_WORKS.md
```

---

## Persistence Layer

### Supabase (Primary)

All signals, scan runs, and metrics are persisted to Supabase (PostgreSQL) via the `common/db.py` module:

| Table | Purpose |
|-------|---------|
| `trades` | Enriched signal log with strategy, conditions JSONB, RR ratio, gap type, regime |
| `scan_runs` | One row per scanner execution with VIX, regime, report markdown, AI advisory |
| `analysis_cache` | Computed metrics (gap analysis, probability matrix, time window stats, metadata) with TTL |
| `daily_performance` | Daily aggregate P&L metrics |
| `config_snapshots` | Config version tracking |

Each scan run logs: scanner_type ("scalp"), VIX value/regime, Nifty regime, day of week, total candidates, active signal count, full report markdown, and AI advisory text.

SQL migration: `migrations/001_create_tables.sql` — run once in Supabase SQL Editor.

### SQLite (Fallback)

If Supabase is unreachable, signals fall back to `scalp_journal.db` (project root) via the Peewee ORM in `common/journal.py`. The system logs a warning and continues operating normally.

The SQLite journal also provides:
- **Edge decay detection** — compares recent vs historical win rate per symbol
- **Portfolio metrics** — 30-day Sharpe, Sortino, max drawdown, win/loss streaks
- **Beta-adjusted exposure** — HHI concentration and gross/beta-weighted exposure
- **Weekly summaries** — markdown summary with per-symbol breakdown and edge decay alerts
- **Config snapshots** — historical tracking of config changes per symbol

---

## AI Advisory

Works with any OpenAI-compatible LLM endpoint. Configured via 3 env vars in `.env`:

```
LLM_BASE_URL=http://localhost:11434/v1   # Ollama (default)
LLM_API_KEY=not-needed
LLM_MODEL=qwen3:8b
```

Switch to any provider by changing the URL and key:

| Provider | `LLM_BASE_URL` | `LLM_MODEL` example |
|----------|----------------|---------------------|
| Ollama (local) | `http://localhost:11434/v1` | `qwen3:8b` |
| Lightning AI | `https://lightning.ai/api/v1` | `lightning-ai/gpt-oss-20b` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4.1` |

All scanners share the same `common/llm.py` module — one OpenAI client, zero code duplication.

**Live market mode**: receives all ticker states, open positions, Nifty regime, VIX, and historical hit rates. Ranks opportunities, recommends position actions, flags risks.

**Prep mode**: receives gap scenario tables with probabilities and hit rates, key levels, volume trends. Produces conditional plans ("If X opens near Y, buy with target Z and stop at W") in two sections: Quick Summary (plain language) and Detailed Analysis (6-section trading desk format).

---

## Libraries & Platforms

| Library | Role |
|---------|------|
| **yfinance** | Market data fetching (OHLCV, ticker info, VIX) |
| **pandas** | Data manipulation, time-series operations, rolling statistics |
| **numpy** | Numerical computation, Bayesian posteriors, Monte Carlo |
| **scipy** | Binomial hypothesis testing for time-window significance |
| **scikit-learn** | PCA for ticker behavioral clustering |
| **PyYAML** | Config serialization/deserialization |
| **supabase-py** | PostgreSQL persistence via Supabase (primary) |
| **python-dotenv** | Environment variable loading for Supabase credentials |
| **Peewee** | SQLite ORM for trade journal persistence (fallback) |
| **Ollama / OpenAI** | LLM advisory (local Qwen3:8B or cloud GPT-4.1) |
| **zoneinfo** | IST timezone handling |

**Platform**: Python 3.14, NSE (National Stock Exchange of India), IST timezone.
