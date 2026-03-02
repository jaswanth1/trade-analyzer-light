# BTST Scanner — How It Works

## What It Does

The BTST (Buy Today Sell Tomorrow) scanner identifies stocks with **strong closing momentum** for an overnight hold. It runs in the **last 90 minutes** of the trading session (after 2:30 PM) and looks for stocks closing near their day's high with volume confirmation — the statistical setup for a positive overnight return.

---

## Strategy: Closing Strength Momentum

The core thesis is simple: stocks that close in the **top 20% of their daily range** with **above-average volume** have a historically elevated probability of opening higher or continuing upward the next day. This is the "BTST edge" — institutional buying in the final hour often spills into the next session.

### How the Edge is Measured

1. **Historical Overnight Returns** — For each ticker, the system computes overnight returns (today's close → tomorrow's close) on all historical "bullish close" days — defined as days where the close is above the open AND the close is in the top 20% of the day's range (close_position >= 0.80).

2. **Segmented Statistics** — Overnight returns are broken down by:
   - **Overall** — across all qualifying days
   - **Gap type** — flat, small gap-up, small gap-down, large gap-up, large gap-down
   - **Day of week** — Mon, Tue, Wed, Thu, Fri

   For each segment: win rate, average positive return, average negative return, median target (positive nights), and P90 stop (worst 10th percentile of losing nights).

3. **Adaptive Targets** — Instead of fixed target/stop percentages, the system adapts to each ticker's actual overnight behavior:
   - **Target** = max(median positive overnight return × 1.2, 0.7 × ATR%), floored at 1%, capped at 5%
   - **Stop** = max(P90 negative overnight return, 0.5 × ATR%), floored at 0.5%, capped at 2%

   This means volatile stocks get wider targets, and tickers with tight overnight distributions get tighter stops.

### Conditions Evaluated

Each ticker is checked against 10 conditions, split into **must-have gates** and **weighted signals**:

**Must-Have Gates** (all must pass):

| Gate | What It Checks |
|------|---------------|
| Closing near high | Close in top 20% of day range (close_position >= 0.80) |
| Above VWAP | Current price above the day's volume-weighted average price |
| Nifty OK | Benchmark not bearish, not making fresh intraday lows |

**Weighted Conditions** (scored by importance):

| Condition | Weight | What It Checks |
|-----------|--------|---------------|
| Volume surge | 3.0 | Last-hour volume ≥ 1.3x same-window historical median |
| Closing near high | 2.5 | (also a gate — double-weighted for scoring) |
| Above VWAP | 2.5 | (also a gate) |
| Higher low pattern | 2.0 | Recent 3-bar low > prior 3-bar low on intraday chart |
| RS vs Nifty | 2.0 | Stock's intraday return > Nifty's intraday return |
| Not overextended | 1.5 | Day's move from open ≤ 1.5× ATR% |
| ATR range OK | 1.5 | Day range ≥ 0.5× ATR% (sufficient movement today) |
| No earnings near | 1.5 | No earnings announcement within 3 days |
| Bullish close | 1.0 | Green candle (close > open) |
| Sector momentum | 1.0 | Sector index positive on the day |

### Signal Classification

Based on gate passage and weighted score:

| Signal | Criteria |
|--------|----------|
| **STRONG_BUY** | All gates pass + weighted score ≥ 85% + overnight win rate > 55% |
| **BUY** | All gates pass + weighted score ≥ 70% |
| **WATCH** | Gates pass but score < 70%, or a filter triggers |
| **AVOID** | Gate blocked, VIX stress, or earnings proximity |

---

## Day-of-Week Filtering

Historical overnight returns are segmented by DOW. If a ticker's overnight win rate on the current weekday is **below 40%** (with at least 5 samples), the signal is downgraded to WATCH regardless of other conditions.

For example, if TATAPOWER.NS has a 35% overnight win rate on Thursdays, a Thursday BTST signal would be suppressed even if all other conditions pass.

---

## Portfolio Risk Management

The BTST scanner applies multiple layers of risk control after ranking signals:

| Layer | Mechanism |
|-------|-----------|
| Position limit | Max 3 concurrent BTST positions |
| Capital allocation | Max 30% of total capital in BTST trades |
| Correlation clusters | Max 2 positions from the same correlation cluster (20-day returns, r > 0.6) |
| VIX regime | VIX > 22 (stress) → all BTST suspended, VIX 18-22 → position size × 0.7 |
| Earnings filter | Skip any ticker with earnings within 3 calendar days |
| Position sizing | Half-Kelly criterion × VIX scale × beta scale |
| Max risk per trade | 0.5% of capital at risk |

### Position Sizing

Uses the Kelly criterion with a conservative half-Kelly approach:

1. Compute Kelly fraction from overnight win rate and reward/risk ratio
2. Apply half-Kelly (× 0.5) for estimation error safety margin
3. Scale by VIX regime (1.2× in low-vol, 0.7× in elevated, 0× in stress)
4. Scale by Nifty beta (0.5× if Nifty bearish, 0.75× if range-bound)
5. Cap at max risk per trade (0.5% of capital)

---

## Analysis Cache

Expensive computed metrics are cached in Supabase (`analysis_cache` table) so repeated scanner runs and cross-scanner calls reuse results within a trading day. If cached data is fresh (within TTL), the function returns instantly; otherwise it computes normally and stores the result.

| Metric | TTL | Cache Key | Shared With |
|--------|-----|-----------|-------------|
| VIX + regime | 30 min | `vix` | Intraday, Scalp |
| Nifty regime | 30 min | `nifty_regime` | Intraday, Scalp |
| Institutional flow | 1 hour | `institutional_flow` | Intraday |
| Correlation clusters | 1 day | `correlation_clusters` | Intraday, Scalp |
| Symbol regime | 1 day | `symbol_regime` + symbol | Intraday |
| Overnight stats | 1 day | `overnight_stats` + symbol | BTST only |
| Overnight DOW/month stats | 1 day | `overnight_dow_month_stats` + symbol | BTST only |
| Overnight hit rate | 1 day | `overnight_hit_rate` + symbol + dow\|month_period | BTST only |
| Earnings proximity | 1 day | `earnings_proximity` + symbol | Intraday, Scalp |

Cache degrades silently — if Supabase is unreachable, all functions compute as before.

---

## Data Pipeline

| Data | Source | Granularity | Lookback |
|------|--------|-------------|----------|
| Intraday OHLCV | yfinance | 5-minute bars | 5 days |
| Daily OHLCV | yfinance | Daily bars | 6 months |
| India VIX | yfinance (`^INDIAVIX`) | Daily | 5 days |
| Nifty 50 | yfinance (`^NSEI`) | 5-min + daily | 5 days / 2 months |
| Sector indices | yfinance | Daily | 5 days |
| Earnings calendar | yfinance `.calendar` | Snapshot | Next event |

---

## Output

All BTST-related output files are created inside the `btst/` directory. The shared trade journal database remains in the project root since it is used by all scanners.

| Output | Format | Location |
|--------|--------|----------|
| Dashboard | Box-drawing terminal UI | stdout |
| Markdown report | `.md` file | `btst/reports/btst_YYYY-MM-DD.md` |
| AI advisory | LLM-generated text | Embedded in report + dashboard |
| Trade journal | Supabase (primary) / SQLite (fallback) | `trades` table / `scalp_journal.db` |
| Analysis cache | Supabase | `analysis_cache` table |

### Directory Layout

```
btst/
├── reports/             # Scanner run reports (from btst.scanner)
│   ├── btst_2026-02-26.md
│   └── ...
├── scanner.py           # Main BTST scanner + overnight stats
├── regime.py            # Overnight DOW/month-period statistics
├── convergence.py       # Daily convergence scoring + overnight hit rate
├── explanations.py      # Educational + LLM explanations
└── HOW_IT_WORKS.md
```

---

## AI Advisory

After ranking and risk-filtering signals, the system builds a structured context with all signal details and sends it to an LLM for a second opinion.

Works with any OpenAI-compatible LLM endpoint. Configured via 3 env vars in `.env`:

```
LLM_BASE_URL=http://localhost:11434/v1   # Ollama (default)
LLM_API_KEY=not-needed
LLM_MODEL=qwen3:8b
```

Switch to any provider (Ollama, Lightning AI, OpenAI, vLLM, etc.) by changing the URL and key. All scanners share the same `common/llm.py` module.

The LLM is asked to rank signals by conviction, explain the overnight edge thesis for each, flag correlated position risks, and recommend how many of the 3 available BTST slots to fill.

The advisory is presented as-is — the system does not auto-act on LLM recommendations.

---

## Libraries & Platforms

| Library | Role |
|---------|------|
| **yfinance** | Market data (OHLCV, VIX, earnings calendar) |
| **pandas** | Data manipulation, overnight return computation, gap classification |
| **numpy** | Statistical aggregations, quantile calculations |
| **PyYAML** | Config loading for LLM settings and capital parameters |
| **Peewee** | SQLite ORM for trade journal logging (fallback) |
| **psycopg2** | Direct PostgreSQL access via Supabase session pooler (primary) |
| **python-dotenv** | Environment variable loading for Supabase credentials |
| **requests** | Ollama API calls (local LLM) |
| **openai** | OpenAI / Lightning AI API calls (cloud LLM) |
| **zoneinfo** | IST timezone handling |

**Platform**: Python 3.14, NSE (National Stock Exchange of India), IST timezone.

---

## Timing

The scanner is designed to run **after 2:30 PM IST** — the last 90 minutes before market close (3:30 PM). This ensures:

- Closing strength can be meaningfully measured (most of the day has traded)
- Volume surge in the final hour is detectable
- Close position in day's range is near-final
- Enough time remains to place the order before close

The `--force` flag overrides the time check for testing outside market hours.
