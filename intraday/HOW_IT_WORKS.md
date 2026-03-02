# Intraday Scanner — How It Works

## What It Does

The intraday scanner is a **time-aware, multi-strategy trading idea engine** for NSE equities. Unlike the scalp scanner (single strategy, config-driven) or BTST scanner (single strategy, closing momentum), this system runs **six distinct strategies simultaneously** and selects the best setups based on the current market regime, symbol-level conditions, and day-of-week/month-period seasonality.

It operates **any time** — auto-detecting the market phase and adapting its output accordingly. Just run `python -m intraday.scanner` and it figures out what to do.

## Usage

```bash
python -m intraday.scanner            # auto-detects phase and runs
python -m intraday.scanner --force    # force LIVE mode anytime (testing)
python -m intraday.scanner --manage   # position management only
```

No CLI flag for phase selection — the scanner reads the IST clock and picks the right mode automatically. `--force` overrides to LIVE for testing outside market hours.

---

## Time-Aware Phase Detection

The scanner auto-detects one of four phases based on the IST clock:

| Window | Phase | What Happens |
|--------|-------|-------------|
| Before 9:00 | **PRE_MARKET** | Daily data only → conditional IF-THEN gap-scenario setups |
| 9:00 - 9:15 | **PRE_LIVE** | Pre-market auction data → institutional activity signals + refined scenarios |
| 9:15 - 15:15 | **LIVE** | Full scanner with time-relevance per strategy |
| After 15:15 | **POST_MARKET** | Session review + tomorrow's watchlist |

### PRE_MARKET (before 9:00)

Uses daily data (6 months) + overnight news to generate **conditional gap-scenario setups**:

For each ticker, 3 scenarios are projected:
- **Gap-up**: IF opens > 0.5% above prev close → likely strategy + entry/target/stop
- **Gap-down**: IF opens > 0.5% below prev close → strategy + levels
- **Flat**: IF opens ±0.3% → strategy + levels

Each scenario is scored by historical gap-day continuation rates + regime alignment. Convergence is computed from daily indicators only (5/7 dimensions — VWAP and candle imbalance unavailable pre-market).

### PRE_LIVE (9:00 - 9:15)

The NSE runs a pre-open auction session from 9:00-9:15. The scanner fetches this data to:
1. Determine the **indicated opening price** → which gap scenario is actually playing out
2. Narrow from 3 scenarios to 1 confirmed scenario
3. Detect **high pre-market volume** stocks (institutional participation signal)
4. Refine entry/target/stop based on actual indicated open
5. Boost probability for confirmed scenarios (+10%)

### LIVE (9:15 - 15:15)

Full scanner (existing behavior) enhanced with **strategy time-relevance**:

| Strategy | Window | Notes |
|----------|--------|-------|
| ORB | 9:15-12:00 | Edge decays after 10:30 |
| Pullback | 9:30-14:30 | Needs trend to establish first |
| Compression | 10:00-14:00 | Mid-session squeeze detection |
| Mean-revert | 10:00-14:30 | Needs VWAP bands to develop |
| Swing | 9:15-15:00 | All day, wider targets |
| MLR | 10:00-11:30 | Morning low recovery (post-settle), works in all regimes |

- **EXPIRED**: strategy window has passed → skip entirely (don't evaluate)
- **FADING**: past 75% of window → -0.05 score penalty
- **PRIME**: within optimal window → no penalty

### POST_MARKET (after 15:15)

Two sections:
1. **Session Review**: today's full data → classify completed day, top movers/losers, trade review
2. **Tomorrow's Watchlist**: project IF-THEN setups filtered to stocks with strong daily+weekly alignment and favorable DOW stats

### Educational Explanations

Every setup includes an educational breakdown:
1. **Stock profile**: ATR in ₹, beta ("moves ₹X for every ₹100 of Nifty movement")
2. **Strategy logic**: why this strategy applies here
3. **Entry conditions**: specific things to verify
4. **₹ terms**: risk/reward per ₹1L capital
5. **Convergence breakdown**: which indicators agree/conflict
6. **Historical context**: "X of Y similar setups were profitable"
7. **Timing**: strategy window status (PRIME/FADING/EXPIRED)
8. **Risks + verdict**: what could go wrong

Top 3 setups also get LLM-powered explanations with cricket analogies and rupee terms, adapted per phase.

---

## The Six Strategies

### 1. Opening Range Breakout (ORB)

**Concept**: The first 30 minutes of trading establish a range. When price breaks out with volume and holds, it tends to continue.

**How it's built**:
- Compute the high/low of the first 30 minutes (6 × 5-min bars) as the "opening range"
- Breakout requires price to clear the range + buffer (10% ATR) for 2 consecutive closes (multi-bar hold confirmation)
- Volume confirmed via **cumulative RVOL** (total volume from open vs 20-day average at same time) > 1.3×
- **RSI filter**: reject long if RSI(14) > 80 (already overbought), reject short if RSI < 20
- **Time decay**: confidence penalty of -0.05 per hour after 10:00 (ORB edge decays intraday)
- Stop at **OR low** (longs) / **OR high** (shorts) — proper structural level, not the midpoint
- Target at next pivot level (R1/S1), capped at 1.5× OR range or 1× ATR
- Transaction costs (0.08% round-trip) deducted from target for realistic RR

**Hardening (v2)**:
- **Gap-and-fade skip**: ORB assumes continuation; if day type is `gap_and_fade`, ORB is skipped entirely
- **Hard 12:00 cutoff**: returns None after 12:00 IST — the old -0.05/hr penalty was too gentle, edge is gone by noon
- **Failed ORB re-entry**: if OR was previously broken in the opposite direction and reversed, that direction is skipped (bull/bear trap detection)

**Best regime**: Trending days (trend_up, trend_down) and gap-and-go days.

### 2. Trend Pullback Entry

**Concept**: In a strong trend, temporary pullbacks to dynamic support offer low-risk entries in the direction of the larger move.

**How it's built**:
- Requires daily trend "strong_up"/"mild_up" (or mirror for shorts) based on EMA alignment
- **Adaptive proximity**: pullback must be within 0.3 × intraday ATR of EMA20 or VWAP (adapts to the stock's actual volatility instead of a fixed 0.3%)
- Rejection candle (lower wick > 1.5× body) + volume drying (3-bar avg < 80% prior)
- **MACD histogram confirmation**: for long pullbacks, histogram must be rising for 2 consecutive bars
- **Pivot level confluence**: if pullback lands near S1/R1, confidence +0.10
- Target capped at min(R1 distance, 1.5R) instead of day high (avoids spike-prone targets)

**Hardening (v2)**:
- **Trend maturity check**: if 5+ consecutive days move in the trend direction, skip — mature trends reverse, not continue
- **Max depth guard**: if pullback exceeds 1× ATR from the trend extreme, it's reclassified as a reversal and skipped

**Best regime**: Trending days with clean daily trends.

### 3. Breakout from Compression (Squeeze)

**Concept**: Bollinger Bands contracting inside Keltner Channels signals compressed volatility. The subsequent expansion produces a strong directional move.

**How it's built**:
- Bollinger (20, 2σ) inside Keltner (20 EMA, 1.5× ATR) = squeeze detected
- Compression range computed from **actual squeeze period bars** (not arbitrary 10-bar window)
- Direction from **close vs compression range** boundaries (not candle color)
- Volume must **trend down during squeeze** then expand on breakout (coiling → release pattern)
- **RSI divergence check**: hidden bullish divergence during squeeze adds confidence for long breakout
- No longer requires daily volatility to be "compressed" — intraday squeeze detection is sufficient

**Hardening (v2)**:
- **Min/max squeeze duration**: require 3-25 bars of squeeze. <3 = noise; >25 = dead stock
- **HTF level proximity**: if expansion target hits daily R1/S1, cap target there — pivots are natural reversal zones

**Best regime**: Range-bound days. A stock with normal daily volatility can still have an intraday squeeze.

### 4. Mean-Reversion to VWAP

**Concept**: On choppy, range-bound days, price snaps back to VWAP after overextending. VWAP standard deviation bands provide precise entry levels.

**How it's built**:
- Only active on range-bound or volatile two-sided day types
- Entry at **VWAP ±2 standard deviations** (using session-computed VWAP bands), falling back to 2× intraday ATR
- **Minimum wick size**: exhaustion wick must be > 0.2 × intraday ATR (prevents doji false signals)
- **RSI exhaustion confirmation**: RSI > 75 for short setups, RSI < 25 for long setups
- **Sector-relative check**: if the sector index is moving in the same direction as the stock's extension, skip (it's trending with sector, not mean-reverting)
- Partial target at VWAP ±1σ, full target at VWAP

**Hardening (v2)**:
- **Trend veto**: if `symbol_regime.trend` is `strong_up` or `strong_down`, skip — "the market can stay irrational longer than you can stay solvent"
- **Time cutoff**: skip after 14:30 IST — not enough time for mean-reversion to complete
- **Next-bar confirmation**: the bar after the exhaustion candle must move back toward VWAP — avoids catching falling knives by requiring actual reversal evidence

**Best regime**: Range-bound and volatile two-sided days.

### 5. Swing Continuation (1-5 Day Hold)

**Concept**: After a daily breakout, an intraday pullback to the breakout level offers a multi-day entry.

**How it's built**:
- Breakout confirmed by **daily Close** above 20-day high (not just intraday High spike)
- Pullback to yesterday's close or 9 EMA, currently above VWAP
- **Exempt from 15:00 hard exit** — `swing_hold = True` flag prevents intraday exit rules
- Position sizing uses wider stop → automatic 0.5× multiplier
- Target: 2.5× daily ATR (multi-day hold)

**Hardening (v2)**:
- **Stale breakout**: max 3 days since breakout (reduced from 5) — edge decays fast; 4-5 day old breakouts fail more often

**Best regime**: Trending days with strong daily trends and recent breakouts.

### 6. Morning Low Recovery (MLR)

**Concept**: Data analysis shows 57% of daily lows form between 9:15–11:00 AM, with average +2.2% recovery to close and +3.1% to subsequent high. MLR buys the morning dip after reversal confirmation.

**Post-settle filtering**: The first 45 minutes (9:15–10:00) are opening noise — every stock makes extreme moves there. The real MLR edge is the dip that forms *after the dust settles*. Both the config generator and live strategy ignore bars before 10:00 AM IST.

**How it's built**:
- **Time window**: 10:00–11:30 IST (post-settle only)
- Session low must form in the morning window (default before 11:30 AM; **per-stock adaptive** from config's `low_cutoff_recommendation`)
- At least 2 bars since the low (reversal confirmation, not catching a falling knife)
- **Minimum drop depth filter**: dip must be ≥ 0.3× ATR from open — rejects noise dips that aren't real sell-offs
- **Recovery-bar volume ratio**: avg volume on recovery bars vs sell bars must be ≥ 0.8× (buying conviction check, using only post-settle bars)
- 7 conditions checked:
  1. **Morning low formed** — post-settle session low occurred 10:00–11:30
  2. **Recovery started** — price recovered ≥0.3% from session low
  3. **Volume confirmation** — cumulative RVOL > 1.2× on recovery bars
  4. **RSI turning** — RSI(14) was ≤35 near low, now rising
  5. **VWAP reclaim** — price crossed back above VWAP (or within 0.2%)
  6. **Candle structure** — last 2 bars show positive imbalance (buyer dominance)
  7. **No lower lows** — last 2 bars' low > session low
- Must meet ≥4 of 7 conditions, with morning low + recovery + no lower lows mandatory
- Entry: current close (or VWAP if above)
- **ATR-adaptive stop**: session low − 0.15× ATR (scales with volatility, not a fixed %)
- **Target**: previous close or pivot level; falls back to entry + 0.8× ATR if no structural target is above entry
- **Exempt from both `nifty_ok` and `vwap_gate`** — works in bearish markets and intentionally buys below VWAP (VWAP reclaim is an internal condition, not a pre-filter)

**Config calibration** (`mlr_config.yaml`):
- Optional precomputed per-ticker config from `python -m intraday.mlr_config`
- **Per-stock phase window discovery**: analyzes 10 post-settle intraday phases (30-min windows from 10:00 to 15:15) to find when each stock forms its low and high. Outputs top 2 low phases, top 2 high phases, and `low_cutoff_recommendation` per ticker
- **Opening type correlation**: classifies each day's open as `gap_up` (≥+0.3%), `gap_down` (≤−0.3%), or `flat`, then tracks low/high formation windows per opening type. E.g., a stock may form lows at 10:30–11:00 on gap-down days but 11:00–11:30 on flat days
- The live strategy uses the per-ticker cutoff (e.g., some stocks form lows by 10:30, others by 12:00)
- Overrides stop/target with historically optimal values per ticker
- EV simulation uses actual PnL (3-outcome: target hit, stopped out, or exit-at-close with real close_vs_entry) with proportional entry model
- Includes DOW favorability, Monte Carlo CIs, and OOS walk-forward validation

**Confidence scoring**:
- Base 0.50, bonuses for strong RVOL (+0.10), deep RSI bounce (+0.08), VWAP reclaim (+0.07), consistent candle structure (+0.05), gap-down day (+0.05), favorable DOW (+0.05), deep drop depth (+0.05)
- Penalty for strong_down trend (−0.10)

**Best regime**: All day types — pattern works across trending, range-bound, volatile, and gap days.

---

## News & Sentiment Layer

### Stock-Level News (`common/news.py`)

Before the scan loop, news headlines are fetched for all tickers using `yfinance .news`. Headlines from the last 48 hours are extracted and sent to the LLM in a **single batched call** for sentiment scoring.

| Field | Description |
|-------|-------------|
| **sentiment** | -1.0 (very bearish) to +1.0 (very bullish) |
| **has_material_event** | True if earnings, M&A, regulatory action, block deal, rating change |
| **summary** | One-line summary of the news |

**Integration into scoring**:
- If `has_material_event=True` and sentiment opposes trade direction → AVOID
- Sentiment > ±0.5 aligned with direction → +0.05 score bonus
- Sentiment > ±0.5 opposing direction → -0.05 score penalty

### Market Macro Context

The LLM is asked for the top 3 market-moving events for Indian equities today (global cues, RBI, FII/DII flows, sector news). This context is included in the AI advisory prompt.

### Institutional Flow Estimate (`common/market.py`)

Uses **Nifty BeES ETF** (`0P0000XVSO.BO`) as a proxy for FII/DII flow:
- Volume spike (> 1.3× 5-day median) + positive return → `net_buying`
- Volume spike + negative return → `net_selling`
- Otherwise → `neutral`

If `net_selling`, the VIX position scale is reduced by 0.15 (more conservative sizing).

---

## Statistical Convergence & Historical Replay

### 7-Indicator Convergence Score (`intraday/convergence.py`)

Before a signal fires, **7 independent indicators** are checked for alignment with the trade direction. A signal with 5/7 aligned is fundamentally different from 2/7.

| # | Indicator | Bullish | Bearish |
|---|-----------|---------|---------|
| 1 | Price vs VWAP | Above | Below |
| 2 | RSI(14) | 40-70 (healthy) | 30-60 (healthy) |
| 3 | MACD histogram | Rising / positive | Falling / negative |
| 4 | EMA alignment | 9 > 20 > 50 | 9 < 20 < 50 |
| 5 | Candle imbalance (3-bar avg) | > 0.3 | < -0.3 |
| 6 | Volume trend (RVOL) | > 1.2× | < 0.8× |
| 7 | Relative strength vs Nifty | Outperforming | Underperforming |

**Scoring**: `aligned / total × 100`

**Integration**:
- Convergence < 40% → downgrade to WATCH regardless of score
- Convergence > 70% → score bonus +0.08
- Convergence detail shown in dashboard, report, and LLM context

### Historical Pattern Replay

For each candidate, the system scans 6 months of daily data for days with similar characteristics (gap type, direction, strategy-specific conditions) and checks next-day outcomes.

| Result | Action |
|--------|--------|
| `sample_size ≥ 10` and `hit_rate > 60%` | Score bonus +0.05 |
| `sample_size ≥ 10` and `hit_rate < 40%` | Downgrade to WATCH |
| `sample_size < 10` | No adjustment (insufficient data) |

Displayed as: "History: 65% win on 23 similar ORB setups"

---

## Multi-Timeframe Trend Alignment

### Weekly Trend Dimension

The symbol regime now includes a **6th dimension**: `weekly_trend` (up / down / sideways).

Computed by resampling daily closes to weekly bars and comparing EMA(9) vs EMA(20):
- Weekly price > EMA9 > EMA20 → `up`
- Weekly price < EMA9 < EMA20 → `down`
- Otherwise → `sideways`

**Impact**: If weekly and daily trends disagree (e.g., daily "mild_up" but weekly "down"), trend-following strategies (ORB, pullback, swing) get a **-0.05 confidence penalty**. Mean-revert and compression are unaffected.

---

## Market Regime Classification

### Day-Type (Market-Level)

Computed from the first 30 minutes of Nifty 50 action. After 11:00 IST, the system **re-classifies** using all available bars for higher accuracy:

| Day Type | Detection |
|----------|----------|
| **trend_up** | Directional move > 0.3× ATR with shallow pullbacks |
| **trend_down** | Directional move < -0.3× ATR |
| **range_bound** | Range < 0.5× ATR, small directional move |
| **volatile_two_sided** | Range > 1.5× ATR with 2+ reversals |
| **gap_and_go** | Gap > 0.5% continuing in gap direction |
| **gap_and_fade** | Gap > 0.5% reversed |

### Symbol-Level Regime (6 Dimensions)

| Dimension | Classification | Method |
|-----------|---------------|--------|
| **Trend** | strong_up / mild_up / sideways / mild_down / strong_down | 9/20/50 EMA alignment + 5-day return |
| **Volatility** | compressed / normal / expanded | 5-day ATR vs 20-day ATR ratio |
| **Liquidity** | normal / illiquid | Today's volume vs 20-day intraday median |
| **Momentum** | accelerating / steady / decelerating | EMA20 slope over 5 bars: >0.5% = accelerating, <-0.2% = decelerating |
| **Relative Strength** | outperforming / inline / underperforming | Today's stock return vs Nifty return |
| **Weekly Trend** | up / down / sideways | Weekly EMA9 vs EMA20 (multi-timeframe) |

Momentum and relative strength are used in score adjustments (accelerating/outperforming = +0.03/+0.02 confidence). Weekly trend is used to penalize trend-following strategies when timeframes disagree.

---

## Day-of-Week & Month-Period Seasonality

Historical daily returns segmented by DOW and month period (begin/mid/end/expiry_week).

| Impact | Mechanism |
|--------|-----------|
| Target scaling | Targets × (DOW WR / overall WR) × (month WR / overall WR) |
| Signal gating | DOW WR < 40% OR month WR < 40% → downgrade to WATCH |
| Position sizing | Expiry week: 0.7× normal size |

---

## Signal Scoring and Classification

1. **Strategy confidence** (0-1) — computed per-strategy with conditions
2. **Adjustments**: VWAP gate (+0.05), Nifty OK (+0.05), DOW WR, month WR, RR, momentum, relative strength
3. **News sentiment** adjustment: ±0.05 based on LLM-scored sentiment aligned/opposing direction
4. **Convergence score**: > 70% → +0.08, < 40% → downgrade to WATCH
5. **Historical hit rate**: > 60% → +0.05, < 40% → downgrade to WATCH
6. **Weekly trend disagreement**: -0.05 for trend-following strategies when weekly opposes daily
7. **Time-relevance penalty**: -0.05 if strategy window is FADING (past 75%); EXPIRED strategies are skipped entirely
8. **Minimum RR gate**: candidates with RR < 1.2 are discarded before scoring
9. **Transaction cost deduction**: 0.08% round-trip costs deducted from target for realistic RR

| Tier | Criteria |
|------|----------|
| **STRONG** | Score ≥ 80%, RR ≥ 2.0, all gates pass |
| **ACTIVE** | Score ≥ 65%, RR ≥ 1.5, all gates pass |
| **WATCH** | Score 50-65%, weak convergence/history, or DOW/month WR too low |
| **AVOID** | Gate blocked, VIX stress, earnings near, illiquid, material news conflict |

### Direction-Aware VWAP Gate

The VWAP gate is direction-aware:
- **Long signals**: price must be above VWAP
- **Short signals**: price must be below VWAP

### MLR Gate Exemptions

MLR is exempt from both the `nifty_ok` and `vwap_gate` checks. Unlike trend-following strategies that need a healthy market, morning low recovery works in bearish conditions — stocks that gap down or sell off in the morning often bounce regardless of Nifty direction. The VWAP gate is also bypassed because MLR intentionally buys stocks recovering *from below* VWAP; the strategy's own condition #5 (VWAP reclaim) handles this check internally.

---

## Portfolio Risk Management

| Layer | Rule |
|-------|------|
| Position limit | Max 5 concurrent intraday positions |
| Capital allocation | Max 50% of total capital in intraday trades |
| Sector concentration | Max 2 positions per sector index |
| Correlation clusters | Max 2 positions per cluster (r > 0.6) |
| **Net direction cap** | Max 4 positions in the same direction (long or short) |
| VIX regime scaling | Low-vol: 1.2×, Normal: 1.0×, Elevated: 0.7×, Stress: 0× |
| **VIX failure default** | If VIX unavailable, use conservative 0.7× (not 1.0×) |
| **Institutional flow** | If `net_selling`, reduce VIX scale by 0.15 |
| **Daily drawdown limit** | If today's realized P&L ≥ -2%, skip new signal generation |
| Earnings filter | Auto-AVOID any ticker with earnings within 3 days |
| **Per-stock beta sizing** | Position size scaled by 1/max(β, 0.5) — high-beta stocks get smaller positions |
| **Slippage-adjusted costs** | Base 0.08% + slippage (0.03% liquid / 0.06% illiquid) via `effective_cost()` |

### Execution Hardening (v2)

| Control | Mechanism |
|---------|-----------|
| **P&L velocity circuit breaker** | 3 consecutive losses within 30 minutes → pause new signals for 30 min. Prevents tilt/revenge trading. |
| **Per-strategy daily loss budget** | Each strategy has an independent daily loss limit (ORB: 0.5%, pullback: 0.5%, compression: 0.3%, mean-revert: 0.3%, swing: 0.5%, MLR: 0.5% of capital). Exceeded → skip that strategy. |
| **Repeat-entry guard** | If a symbol was stopped out today on the same strategy, don't re-enter it. |

### Intraday Position Management

| Rule | Trigger | Action |
|------|---------|--------|
| Breakeven stop | 50% progress | Move stop to entry |
| Trailing stop | 75% progress | Stop to entry + 50% target distance |
| Lunch exit | 12:00-13:00, < 30% progress | Exit (not swings) |
| Hard exit | 15:00 | Close all except `swing_hold` positions |
| Stop/target hit | Price at level | Exit |

---

## Persistence Layer

### Supabase (Primary)

All signals, scan runs, and metrics are persisted to Supabase (PostgreSQL):

| Table | Purpose |
|-------|---------|
| `trades` | Enriched signal log with strategy, conditions JSONB, regime, tier, sector, news_sentiment, convergence_score, historical_hit_rate |
| `scan_runs` | One row per scanner execution with VIX, regime, report, advisory |
| `daily_performance` | Daily aggregate metrics |
| `config_snapshots` | Config version tracking |
| `ohlcv_cache` | Cached OHLCV candle data to avoid repeated yfinance API calls across scanners |
| `analysis_cache` | Cached expensive computed metrics (VIX, regime, hit rates, DOW/month stats) shared across scanners |

### Analysis Cache (`common/analysis_cache.py`)

Expensive analysis metrics are cached in Supabase so multiple scanner runs reuse results within a trading day. The cache is keyed by `(metric, symbol, params)` with TTL-based freshness:

| Tier | Metrics | TTL | Key |
|------|---------|-----|-----|
| **Market-level** | VIX, Nifty regime | 30 min | metric only |
| **Market-level** | Institutional flow | 1 hour | metric only |
| **Market-level** | Correlation clusters | 1 day | metric only |
| **Per-symbol daily** | Symbol regime, DOW/month stats, overnight stats, overnight DOW/month stats, earnings proximity | 1 day | metric + symbol |
| **Per-candidate** | Historical hit rate (intraday) | 1 day | metric + symbol + strategy\|direction\|day_type |
| **Per-candidate** | Overnight hit rate (BTST) | 1 day | metric + symbol + dow\|month_period |

Cache degrades silently — if Supabase is down, functions compute normally. First scanner run of the day populates the cache; subsequent runs (same or different scanner) get instant results.

SQL migration: `migrations/002_upstox_tables.sql` (includes `ohlcv_cache` and `analysis_cache` tables).

### SQLite (Fallback)

If Supabase is unreachable, signals fall back to `scalp_journal.db` via Peewee ORM. The system logs a warning and continues operating.

SQL migrations: `migrations/001_create_tables.sql` and `migrations/002_upstox_tables.sql` — run once in Supabase SQL Editor.

---

## Data Pipeline

| Data | Source | Granularity | Lookback | Phase |
|------|--------|-------------|----------|-------|
| Intraday OHLCV | yfinance | 5-minute | 5 days | LIVE |
| Daily OHLCV | yfinance | Daily | 6 months | All |
| India VIX | yfinance (`^INDIAVIX`) | Daily | 5 days | All |
| Nifty 50 | yfinance (`^NSEI`) | 5-min + daily | 5 days / 6 months | All |
| Sector indices | yfinance | Daily | 5 days | LIVE |
| Earnings | yfinance `.calendar` | Snapshot | Next event | LIVE |
| **Pre-market auction** | yfinance (`prepost=True`) | 1-minute | 1 day | PRE_LIVE |
| **Stock news** | yfinance `.news` | Headlines | Last 48 hours | All |
| **Nifty BeES** | yfinance (`0P0000XVSO.BO`) | Daily | 5 days | All |
| **Market context** | LLM inference | Text | Current session | All |

---

## Output

All intraday-related output files are created inside the `intraday/` directory. The shared trade journal database remains in the project root since it is used by all scanners.

| Output | Format | Location |
|--------|--------|----------|
| Dashboard | Box-drawing terminal UI | stdout |
| Markdown report | `.md` file | `intraday/reports/{phase}_YYYY-MM-DD_HHMM.md` |
| Educational breakdown | Template + LLM text | stdout (below dashboard) |
| AI advisory | LLM-generated text | Report + dashboard |
| Supabase tables | PostgreSQL | `trades`, `scan_runs` |
| SQLite journal | Peewee/SQLite | `scalp_journal.db` (fallback) |

Reports are named by phase: `pre_market_*.md`, `pre_live_*.md`, `intraday_*.md`, `post_market_*.md`.

Each candidate in the dashboard and report now shows:
- **Convergence**: e.g., "71% — 5/7 (VWAP, RSI, MACD, EMA_align, rel_strength)"
- **History**: e.g., "65% win on 23 similar ORB setups"
- **News**: e.g., "Reliance Q3 beats estimates (sentiment: +0.7)"
- **Time window**: e.g., "ORB window 09:15-12:00 — PRIME (47 min left)"

### Directory Layout

```
intraday/
├── reports/             # Phase-specific reports
│   ├── pre_market_2026-02-26_0830.md
│   ├── pre_live_2026-02-26_0905.md
│   ├── intraday_2026-02-26_1030.md
│   ├── post_market_2026-02-26_1530.md
│   └── ...
├── scanner.py           # Main orchestrator (time-aware, phase dispatch)
├── backtest.py          # Historical backtest: replay phases, validate signals
├── explanations.py      # Template + LLM educational explanations
├── strategies.py        # 6 strategy implementations
├── features.py          # Intraday technical indicators (incl. session low analysis)
├── regime.py            # Day-type + symbol-level regime (6 dimensions)
├── convergence.py       # Convergence scoring + historical replay
├── mlr_config.py        # MLR config generator (precomputed per-ticker stats)
└── HOW_IT_WORKS.md
```

---

## AI Advisory

Works with any OpenAI-compatible LLM endpoint. Configured via 3 env vars in `.env`:

```
LLM_BASE_URL=http://localhost:11434/v1   # Ollama (default)
LLM_API_KEY=not-needed
LLM_MODEL=qwen3:8b
```

Switch to any provider (Ollama, Lightning AI, OpenAI, vLLM, etc.) by changing the URL and key. All scanners share the same `common/llm.py` module.

### Phase-Specific LLM Prompts (`intraday/explanations.py`)

Each phase gets a tailored system prompt for the educational LLM explanation of top 3 setups:

| Phase | LLM Focus |
|-------|-----------|
| **PRE_MARKET** | Scenario analysis, which gap is most likely, what to watch at open. Cricket analogy: "reading the pitch before the first ball." |
| **PRE_LIVE** | Institutional positioning from pre-market data, confirmed scenario, high-volume flags. Cricket analogy: "the toss has happened — we know the conditions now." |
| **LIVE** | Immediate action, time pressure, specific entry confirmations, "if this goes wrong you lose ₹X on ₹1L." |
| **POST_MARKET** | Lessons from today, tomorrow's preparation, overnight risk assessment. Cricket analogy: "studying the opponent's recent form." |

All prompts instruct the LLM to use rupee terms per ₹1L capital and cricket analogies where they genuinely help.

### LIVE Mode Advisory

The existing advisory system prompt (ranking STRONG/ACTIVE signals, flagging conflicts, DOW seasonality) is still used during LIVE mode. The educational LLM explanation is appended as a separate section.

**Context provided to LLM**:
- Market state: Nifty regime, VIX, day type, institutional flow, DOW/month period
- Market macro context from news LLM call
- Per candidate: entry/stop/target, score, regime (including weekly trend), convergence score + detail, historical hit rate, news summary + sentiment, **time window status**

**System prompt instructions (LIVE)**:
1. Rank STRONG and ACTIVE signals by conviction (max 5 trades)
2. Explain WHY the setup is valid given market regime and day-type
3. Flag conflicts: correlated positions, overexposure to one direction/sector
4. Comment on DOW/month-period seasonality impact
5. Give specific entry/target/stop levels and which strategies to prioritize
6. Consider news sentiment — flag if negative news conflicts with a long signal
7. Weight convergence score — prefer signals with 5+ indicators aligned
8. Reference historical hit rates when available
9. If institutional flow is "net_selling", be more cautious on longs

### Template-Based Explanations (`intraday/explanations.py`)

Every setup (all phases, not just top 3) gets a deterministic template explanation covering:

1. **Stock profile**: daily ATR in ₹, beta vs Nifty ("moves ₹X for every ₹100 of Nifty movement"), regime summary
2. **Strategy logic**: why this specific strategy applies (from `STRATEGY_DESCRIPTIONS` dict)
3. **Entry conditions**: specific things to verify, with unmet conditions flagged as WATCH
4. **₹ terms per ₹1L capital**: shares count, risk amount, reward amount
5. **Convergence breakdown**: which of 7 indicators agree/conflict, with interpretation
6. **Historical context**: hit rate on similar setups, with strength assessment
7. **Timing**: phase-aware messaging (conditional for pre-market, time pressure for live, preparation for post-market)
8. **Risks**: news sentiment conflicts, weekly trend disagreements, high beta warnings, expanded volatility
9. **Verdict**: STRONG = full size, ACTIVE = normal size, WATCH = monitor only, AVOID = skip

For pre-market, an additional **IF-THEN scenario format** is generated per gap scenario using `generate_scenario_explanation()`.

### "None" Output

If no setups qualify in any phase, the scanner explicitly says so:
- PRE_MARKET: "No qualifying setups for today. Check back at 9:00 for pre-live data or 9:15 for live scan."
- PRE_LIVE: "No qualifying setups. Wait for 9:15 live scan."
- LIVE: Dashboard shows "STRONG SIGNALS: None" with only WATCH/AVOID candidates listed
- POST_MARKET: "No strong setups for tomorrow."

---

## Libraries & Platforms

| Library | Role |
|---------|------|
| **yfinance** | Market data + stock news |
| **pandas / numpy** | Data manipulation, indicators |
| **PyYAML** | Config loading |
| **psycopg2** | Direct PostgreSQL access (Supabase session pooler) |
| **python-dotenv** | Environment variable loading |
| **Peewee** | SQLite ORM (fallback) |
| **requests / openai** | LLM API calls (Ollama / OpenAI / Lightning AI) |
| **zoneinfo / calendar** | IST timezone, expiry week detection |

**Platform**: Python 3.14, NSE, IST timezone.

---

## Backtest

The intraday backtest (`intraday/backtest.py`) replays historical days through the scanner's phase-aware pipeline to validate signal accuracy.

### Usage

```bash
python -m intraday.backtest --date 2026-02-20           # Single day
python -m intraday.backtest --start 2026-02-10 --end 2026-02-20  # Date range
python -m intraday.backtest --date 2026-02-20 --capital 500000    # Custom capital
python -m intraday.backtest --date 2026-02-20 --llm     # Add LLM summary
```

### How It Works

For a backtest date T:

1. **Post-market T-1**: Simulates the previous day's post-market scan. Generates tomorrow's watchlist = gap scenario predictions for T.
2. **Pre-market T**: Simulates morning pre-market scan using daily data up to T-1. Generates conditional gap scenarios.
3. **Live scans at 09:30, 11:00, 13:00, 14:30**: Simulates live scans by slicing intraday data up to each time point. Uses `evaluate_symbol()` with mock time injection.
4. **Validation**: Walks forward through actual T bars to check each signal: was entry hit? Target or stop first? Computes MFE (max favorable excursion) and MAE (max adverse excursion).

Pre-live (9:00-9:15) is skipped — yfinance doesn't store historical pre-market auction data.

### Data Slicing (No Look-Ahead)

| Phase | Daily Data | Intraday Data | Mock Time |
|-------|-----------|---------------|-----------|
| Post-market T-1 | Up to T-1 | Full T-1 bars | T-1 15:30 |
| Pre-market T | Up to T-1 | None | T 08:00 |
| Live @ 09:30 | Up to T-1 | T bars up to 09:30 | T 09:30 |
| Live @ 11:00 | Up to T-1 | T bars up to 11:00 | T 11:00 |
| Live @ 13:00 | Up to T-1 | T bars up to 13:00 | T 13:00 |
| Live @ 14:30 | Up to T-1 | T bars up to 14:30 | T 14:30 |

Scanner functions accept `now_ist`, `data_override`, and `skip_llm` parameters so the backtest can inject mock time, pre-fetched data, and disable LLM/rendering.

### Signal Validation

| Outcome | Definition |
|---------|-----------|
| **CORRECT** | Entry hit AND target hit before stop (or EOD exit at profit) |
| **WRONG** | Entry hit AND stop hit before target (or EOD exit at loss) |
| **CLOSE_CALL** | Wrong, but MFE reached >50% of target distance |
| **NO_ENTRY** | Entry price was never reached |

### Report Output

Reports saved to `intraday/reports/backtest_YYYY-MM-DD.md` with:
- Summary (signals, entered, win rate, avg RR achieved)
- Pre-market scenario accuracy
- Per-live-scan breakdown table
- Per-strategy breakdown (signals, win rate, avg MFE/MAE)
- Per-stock detail with entry/exit/MFE/MAE
- Wrong calls analysis table

Multi-day runs produce per-day reports + an aggregate report.
