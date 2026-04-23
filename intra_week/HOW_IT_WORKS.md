# IntraWeek Scanner — How It Works

Systematic swing trading module for Indian equities (NSE). Identifies stocks with high probability of delivering 10-20% upside within a single trading week using three complementary sub-strategies.

## Architecture

```
intra_week/
  __main__.py          Entry point (python -m intra_week)
  scanner.py           Main orchestrator: fetch → evaluate → score → rank → dashboard → report
  strategies.py        Three strategy evaluators (oversold_recovery, vol_compression, weekly_context)
  convergence.py       7-dimension convergence scoring + historical hit rate computation
  scoring.py           Weighted condition scoring, composite blend, tier classification
  weekly_context.py    Day-of-week awareness, NSE holidays, F&O expiry detection
  explanations.py      Human-readable reason strings and risk notes
  backtest.py          Week-by-week historical simulation
  backtest_report.py   Markdown report generation for backtest results
```

## Pipeline

```
1. Load universe (intra_week tier from universe.yaml, fallback to btst)
2. Fetch market context (VIX, Nifty regime, institutional flow)
3. Build weekly context (day-of-week, holidays, expiry, remaining days)
4. Fetch 3-month daily OHLCV for all symbols + sector indices
5. For each symbol × 3 strategies:
   a. Evaluate strategy conditions → candidate dict or None
   b. Compute 7-dimension convergence score
   c. Compute historical hit rate (RSI<35 + 5% drawdown → forward 5d return)
   d. Compute regime alignment score
   e. Blend into composite score (40/25/20/15 weights)
   f. Classify tier (STRONG/ACTIVE/WATCH/AVOID)
6. Rank by tier then score
7. Apply sector cap (max 2 per sector)
8. Render terminal dashboard + write markdown report
```

## Strategies

### 1. Oversold Recovery

**Thesis**: Stock drops 5%+ in 1-3 days while its sector holds — a stock-specific selloff that typically mean-reverts within a week.

**Trigger conditions**:
- 3-day drawdown >= 5%
- Sector dropped < 2% in same period (divergence = stock-specific)
- RSI(14) < 35 (oversold)

**Confirmation**:
- Volume spike on down days >= 1.3x 20-day median (capitulation)
- MACD histogram turning (momentum reversal)
- Close near range low (exhaustion)

**Target/Stop**: Recovery to 2.5x ATR upside, stop at 1x ATR downside.

### 2. Volatility Compression

**Thesis**: When Bollinger Bands compress inside Keltner Channels (a "squeeze"), energy builds up for a directional breakout. EMA bullish alignment biases the breakout direction upward.

**Trigger conditions**:
- Bollinger bandwidth at or within 5% of 20-day minimum
- Keltner squeeze active (BB upper < KC upper AND BB lower > KC lower)

**Confirmation**:
- ATR percentile < 30 (low volatility state)
- Volume declining into squeeze (energy building)
- EMA 9 > 20 > 50 (bullish structure)

**Target/Stop**: Breakout to 3x ATR upside, stop at 1x ATR downside.

### 3. Weekly Context Recovery

**Thesis**: Calendar-driven dislocations (holiday weeks, F&O expiry weeks) create temporary selling pressure early in the week that reverses by Friday.

**Trigger conditions**:
- Monday/Tuesday weakness: stock dropped > 2% in last 2 days
- OR 3% drawdown in last 2 days (any day)
- At least 2 trading days remaining in the week

**Confirmation**:
- Holiday or expiry week (context bonus)
- Sector still positive (stock-specific weakness)
- Institutional flow supportive

**Target/Stop**: Recovery to 2x ATR upside, stop at 1x ATR downside.

## Scoring System

### Weighted Condition Scoring (Base Score)

Each strategy returns a dict of boolean conditions. These are weighted:

| Condition | Weight | Description |
|-----------|--------|-------------|
| downside_exhaustion | 3.5 | RSI oversold, ATR percentile low |
| momentum_reversal | 3.0 | MACD histogram turning positive |
| volume_expansion | 2.5 | Volume spike on down days or declining into squeeze |
| sector_strength | 2.5 | Sector outperforming the stock |
| relative_strength | 2.0 | Stock-vs-sector divergence magnitude |
| ema_alignment | 2.0 | EMA 9 > 20 > 50 bullish structure |
| weekly_context | 1.5 | Holiday/expiry week bonus |
| vwap_reclaim | 1.5 | Price above VWAP (intraday only) |
| atr_range_ok | 1.5 | ATR% in 1.5-8% range (**must-have gate**) |
| not_overextended | 1.0 | Drawdown < 15-20% (not a collapse) |

**Must-have gate**: `atr_range_ok` must pass or the signal is classified as AVOID regardless of score.

### Composite Blend

```
composite = 0.40 × base_score
          + 0.25 × convergence_norm
          + 0.20 × historical_hit_rate_norm
          + 0.15 × regime_alignment
```

### Convergence (7 Dimensions)

Daily indicators checked for LONG alignment:

1. Close > 20-EMA (trend support)
2. RSI(14) < 70 (not overbought)
3. MACD histogram positive or rising
4. EMA alignment: 9 > 20 > 50
5. Volume trend: recent 3-day avg > 1.2x 20-day median
6. Weekly trend: from symbol regime or 5d/20d return comparison
7. Higher low forming: recent 5-day low > prior 5-day low

Score = (aligned / total) × 100, normalized to 0-1 for composite blend.

### Historical Hit Rate

Scans last 6 months of daily data for prior instances matching:
- RSI(14) < 35 AND 3-day drawdown > 5%

For each match, measures the forward 5-day return. Reports hit rate at 10%+ and 20%+ thresholds. Normalized to 0-1 for composite blend (0.5 if no samples).

### Regime Alignment

Scores how favorable the current regime is for weekly longs:

| Factor | Weight | Bullish | Sideways | Bearish |
|--------|--------|---------|----------|---------|
| Price trend | 0.40 | strong_up/mild_up | sideways | down |
| Weekly trend | 0.30 | up | sideways | down |
| Momentum | 0.20 | accelerating | steady | decelerating |
| Relative strength | 0.10 | outperforming | inline | underperforming |

### Risk Penalties

- **VIX stress**: composite × 0.7
- **VIX elevated**: flagged but no penalty
- **Bearish market**: composite × 0.85
- **<=1 trading day left**: composite × 0.3
- **Earnings within 5 days**: auto-AVOID

### Signal Tiers

| Tier | Min Score | Min Upside | Action |
|------|-----------|------------|--------|
| STRONG | 0.80 | 12% | High conviction entry |
| ACTIVE | 0.65 | 10% | Standard entry |
| WATCH | 0.50 | 8% | Monitor, enter on confirmation |
| AVOID | — | — | Skip |

## Weekly Context Engine

### Day-of-Week Awareness

Scanner is optimized for Mon-Wed entries (Thu/Fri skipped unless `--force`). Remaining trading days are computed factoring in NSE holidays.

### NSE Holidays

Hardcoded holiday calendar for 2025-2026 (update annually from NSE circulars). Used for:
- Holiday week detection
- Remaining trading day computation
- Holiday proximity alerts

### F&O Expiry Detection

Monthly expiry = last Thursday of the month. If that Thursday is an NSE holiday, expiry shifts to Wednesday. The scanner detects expiry weeks and passes this context to the weekly_context strategy.

## Backtest Engine

### Simulation

Week-by-week replay:
1. For each Monday in the backtest range, run the full scanner pipeline
2. Select top 5 candidates (MAX_POSITIONS)
3. Simulate each trade through the week using daily OHLC:
   - Check stop hit (intraday low <= stop price)
   - Check target hit (intraday high >= target price)
   - Force exit on Friday close if neither hit ("time_exit")
4. Track MFE (max favorable excursion) and MAE (max adverse excursion) for each trade

### Metrics Computed

- Win rate, avg PnL, total PnL
- % reaching 10%+ and 20%+ returns
- Average holding days
- Max drawdown (cumulative PnL based)
- Profit factor (gross profit / gross loss)
- Sharpe ratio (annualized, √52 scaling)
- Average MFE and MAE
- Breakdowns by strategy and exit reason
- Monthly PnL table

### Usage

```bash
python -m intra_week.backtest --last-quarter                         # last 3 months
python -m intra_week.backtest --start 2025-01-01 --end 2026-04-01   # custom range
python -m intra_week.backtest --start 2025-06-01 --end 2026-01-01 --capital 500000
```

Reports saved to `intra_week/reports/backtest_*.md`.

## Dashboard Output

Terminal output uses box-drawing characters (from `common/display.py`). Candidates grouped by tier (STRONG → ACTIVE → WATCH), showing:

- Score percentage
- Strategy name
- Expected upside range
- Entry / Target / Stop prices
- Reason strings (strategy-specific explanations)
- Risk flags

## Dependencies

Reuses shared modules from the codebase:

| Module | What's Used |
|--------|------------|
| `common/data.py` | `fetch_yf()`, `fetch_bulk_single()`, `load_universe_for_tier()`, path constants |
| `common/indicators.py` | `compute_atr()`, `compute_atr_percentile()`, `compute_relative_performance()` |
| `common/market.py` | VIX, Nifty regime, institutional flow, earnings proximity, higher lows |
| `common/risk.py` | Position sizing, correlation clusters, beta scaling |
| `common/display.py` | Box-drawing terminal utilities |
| `intraday/features.py` | EMA, RSI, MACD, Bollinger, Keltner computations |
| `intraday/regime.py` | Symbol-level regime classification (optional, graceful fallback) |
