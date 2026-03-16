# MLR Config Guide

Auto-generated documentation for Morning Low Recovery configuration.

## What is MLR?

Morning Low Recovery buys stocks that form their post-settle daily low
(after 10:00 AM, once opening noise clears) and show confirmed reversal.
The first 45 minutes are ignored — every stock shows extreme moves then.
The real edge is in lows that form after the dust settles.

## How the Config Works

For each ticker, the generator:
1. Fetches 60 days of 5-minute OHLCV data (+ 1 year daily)
2. Identifies days where the session low formed in the morning window
3. Runs full-session phase analysis — discovers when each stock forms lows/highs
4. Computes recovery statistics (to close, to high)
5. Grid-searches optimal entry delay, stop, and target combinations
6. Validates with 70/30 walk-forward out-of-sample test
7. Runs Monte Carlo bootstrap for 95% confidence intervals
8. Computes DOW/month seasonality and per-phase window probabilities
9. Recommends per-stock low cutoff based on where 80%+ of lows form

## Enabled Tickers

| Ticker | Edge | EV | WR% | n | Avg Rec | Drop(xATR) | High(xATR) | Best Low | Best Post-Low High | Window | Cutoff |
|--------|------|----|-----|---|---------|------------|------------|----------|---------------------|--------|--------|
| KFINTECH | 5 | 0.549 | 71% | 58 | 0.6% | 0.27x | 0.28x | 10:30-11:00 | 14:00-14:30 | 152min | 15:15 |
| BHEL | 5 | 0.561 | 69% | 58 | 0.8% | 0.23x | 0.39x | 11:00-11:30 | 14:30-15:15 | 150min | 15:15 |
| IDBI | 4 | 0.666 | 59% | 58 | 0.7% | 0.22x | 0.43x | 11:00-11:30 | 14:30-15:15 | 110min | 15:15 |
| BSE | 4 | 0.423 | 57% | 58 | 0.7% | 0.18x | 0.39x | 10:00-10:30 | 14:30-15:15 | 160min | 15:15 |
| ABCAPITAL | 4 | 0.576 | 57% | 58 | 0.7% | 0.28x | 0.30x | 10:00-10:30 | 14:30-15:15 | 159min | 15:15 |
| INDIANB | 4 | 0.497 | 52% | 58 | 1.1% | 0.23x | 0.42x | 10:30-11:00 | 14:30-15:15 | 174min | 14:30 |
| TATAPOWER | 4 | 0.252 | 52% | 58 | 0.5% | 0.17x | 0.45x | 11:00-11:30 | 14:30-15:15 | 153min | 15:15 |
| ADANIPOWER | 4 | 0.472 | 67% | 58 | 0.7% | 0.20x | 0.42x | 10:00-10:30 | 14:30-15:15 | 139min | 15:15 |
| COALINDIA | 4 | 0.280 | 53% | 58 | 0.7% | 0.13x | 0.55x | 10:30-11:00 | 14:30-15:15 | 150min | 14:00 |
| GPIL | 4 | 0.637 | 50% | 58 | 0.8% | 0.15x | 0.46x | 11:00-11:30 | 14:30-15:15 | 148min | 15:15 |
| GRAPHITE | 4 | 0.891 | 57% | 58 | 0.9% | 0.12x | 0.54x | 10:30-11:00 | 12:00-12:30 | 140min | 11:30 |
| HAL | 4 | 0.308 | 50% | 58 | 0.6% | 0.21x | 0.33x | 10:00-10:30 | 14:30-15:15 | 152min | 15:15 |
| DATAPATTNS | 4 | 0.696 | 64% | 58 | 0.9% | 0.13x | 0.48x | 10:30-11:00 | 14:30-15:15 | 131min | 15:15 |
| MTARTECH | 4 | 0.895 | 53% | 58 | 0.9% | 0.07x | 0.50x | 11:00-11:30 | 11:30-12:00 | 152min | 15:15 |
| RVNL | 4 | 0.506 | 60% | 58 | 0.8% | 0.23x | 0.35x | 10:30-11:00 | 11:30-12:00 | 147min | 15:15 |
| FINCABLES | 4 | 0.276 | 52% | 58 | 0.9% | 0.24x | 0.41x | 11:00-11:30 | 14:30-15:15 | 154min | 15:15 |
| HAVELLS | 4 | 0.306 | 53% | 58 | 0.6% | 0.28x | 0.41x | 10:00-10:30 | 14:30-15:15 | 160min | 15:15 |
| NETWEB | 4 | 0.701 | 57% | 58 | 0.8% | 0.18x | 0.39x | 10:30-11:00 | 12:30-13:00 | 115min | 11:30 |
| EXIDEIND | 4 | 0.366 | 52% | 58 | 0.4% | 0.27x | 0.31x | 11:00-11:30 | 14:30-15:15 | 156min | 15:15 |
| VBL | 4 | 0.432 | 50% | 58 | 0.6% | 0.28x | 0.35x | 10:30-11:00 | 14:30-15:15 | 174min | 15:15 |
| TRENT | 4 | 0.408 | 53% | 58 | 0.7% | 0.28x | 0.34x | 10:00-10:30 | 14:30-15:15 | 153min | 14:30 |
| ANANTRAJ | 4 | 0.656 | 53% | 58 | 0.7% | 0.25x | 0.31x | 10:30-11:00 | 11:00-11:30 | 136min | 15:15 |
| GLENMARK | 4 | 0.414 | 55% | 58 | 0.8% | 0.26x | 0.45x | 10:00-10:30 | 14:30-15:15 | 168min | 15:15 |

### Open-Type Profiles

Predictability-scored profiles per opening type (drop -> recovery -> timing):

**KFINTECH**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 5 | 0.59 | 11:00-11:30 | 0.32x | 10:00-10:30 | 0.32x | 159 | 40% |
| Gap Down Small | 15 | 0.50 | 10:00-10:30 | 0.24x | 14:30-15:15 | 0.34x | 170 | 47% |
| Gap Up Large | 5 | 0.51 | 10:00-10:30 | 0.23x | 10:30-11:00 | 0.41x | 150 | 60% |

**BHEL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 6 | 0.52 | 10:00-10:30 | 0.32x | 10:30-11:00 | 0.41x | 142 | 50% |
| Gap Up Small | 17 | 0.43 | 10:00-10:30 | 0.16x | 11:30-12:00 | 0.49x | 156 | 59% |
| Gap Up Large | 3 | 0.71 | 10:00-10:30 | 0.61x | other | -0.04x | 232 | 0% |

**IDBI**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.46 | 11:00-11:30 | 0.41x | 11:30-12:00 | 0.28x | 88 | 38% |
| Gap Up Large | 7 | 0.46 | 10:00-10:30 | 0.48x | 14:30-15:15 | 0.02x | 117 | 29% |

**BSE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.40 | 10:00-10:30 | 0.11x | other | 0.53x | 168 | 83% |
| Gap Up Small | 15 | 0.41 | 10:00-10:30 | 0.09x | 14:30-15:15 | 0.40x | 167 | 47% |
| Gap Up Large | 8 | 0.62 | 10:00-10:30 | 0.04x | 14:30-15:15 | 0.55x | 205 | 75% |

**ABCAPITAL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 10 | 0.43 | 10:00-10:30 | 0.34x | other | 0.35x | 188 | 60% |
| Gap Up Large | 6 | 0.75 | 11:30-12:00 | 0.42x | other | 0.14x | 154 | 67% |

**INDIANB**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 5 | 0.74 | 10:00-10:30 | 0.58x | 14:30-15:15 | 0.05x | 198 | 20% |
| Gap Down Small | 10 | 0.48 | 10:00-10:30 | 0.25x | 11:30-12:00 | 0.35x | 148 | 70% |
| Gap Up Small | 15 | 0.45 | 10:00-10:30 | 0.23x | other | 0.46x | 217 | 53% |

**TATAPOWER**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 4 | 0.56 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 155 | 75% |
| Gap Down Small | 7 | 0.41 | 10:00-10:30 | 0.00x | 10:30-11:00 | 0.00x | 84 | 57% |
| Gap Up Small | 12 | 0.43 | 10:00-10:30 | 0.19x | 14:30-15:15 | 0.50x | 200 | 75% |

**ADANIPOWER**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.50 | — | 0.00x | — | 0.00x | 174 | 100% |
| Gap Down Small | 14 | 0.43 | 10:00-10:30 | 0.23x | 11:30-12:00 | 0.57x | 123 | 43% |
| Flat | 19 | 0.44 | 10:00-10:30 | 0.20x | 14:30-15:15 | 0.33x | 174 | 42% |

**COALINDIA**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 9 | 0.54 | 11:00-11:30 | 0.30x | other | 0.51x | 233 | 56% |
| Gap Up Large | 3 | 0.63 | 10:00-10:30 | 0.05x | — | 0.79x | 138 | 100% |

**GPIL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 21 | 0.41 | 10:00-10:30 | 0.12x | 11:00-11:30 | 0.59x | 124 | 67% |
| Gap Up Small | 10 | 0.47 | 10:00-10:30 | 0.15x | other | 0.37x | 196 | 70% |
| Gap Up Large | 6 | 0.47 | 10:00-10:30 | 0.30x | other | 0.30x | 194 | 83% |

**GRAPHITE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 21 | 0.61 | 10:00-10:30 | 0.02x | 14:30-15:15 | 0.73x | 145 | 76% |
| Gap Up Large | 8 | 0.54 | 10:00-10:30 | 0.33x | 13:30-14:00 | 0.25x | 161 | 50% |

**HAL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 4 | 0.84 | 10:00-10:30 | 0.49x | 11:30-12:00 | 0.09x | 118 | 25% |
| Gap Down Small | 9 | 0.46 | 10:00-10:30 | 0.25x | 11:30-12:00 | 0.31x | 126 | 44% |
| Gap Up Small | 15 | 0.41 | 10:00-10:30 | 0.28x | 14:30-15:15 | 0.27x | 168 | 60% |

**DATAPATTNS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 12 | 0.40 | 10:00-10:30 | 0.09x | 14:30-15:15 | 0.41x | 140 | 42% |
| Gap Up Large | 5 | 0.70 | 10:30-11:00 | 0.36x | other | 0.40x | 177 | 80% |

**MTARTECH**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 11 | 0.44 | 10:00-10:30 | 0.10x | other | 0.44x | 198 | 73% |
| Gap Up Large | 8 | 0.44 | 10:00-10:30 | 0.16x | other | 0.47x | 180 | 75% |

**RVNL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 12 | 0.48 | 10:00-10:30 | 0.19x | 14:30-15:15 | 0.34x | 181 | 42% |
| Gap Up Small | 16 | 0.51 | 10:00-10:30 | 0.01x | 11:00-11:30 | 0.62x | 100 | 56% |
| Gap Up Large | 6 | 0.72 | 11:00-11:30 | 0.58x | other | 0.43x | 244 | 67% |

**FINCABLES**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.47 | 10:00-10:30 | 0.03x | — | 0.89x | 156 | 67% |
| Flat | 24 | 0.48 | 10:00-10:30 | 0.17x | other | 0.45x | 169 | 58% |
| Gap Up Large | 4 | 0.44 | 11:00-11:30 | 0.20x | 11:30-12:00 | 0.56x | 131 | 50% |

**HAVELLS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 5 | 0.70 | 10:30-11:00 | 0.39x | 14:30-15:15 | 0.47x | 160 | 60% |
| Flat | 34 | 0.49 | 10:00-10:30 | 0.31x | other | 0.39x | 180 | 41% |

**NETWEB**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Large | 9 | 0.40 | 10:00-10:30 | 0.57x | 12:00-12:30 | 0.24x | 101 | 44% |

**EXIDEIND**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 5 | 0.40 | 10:00-10:30 | 0.34x | other | 0.33x | 192 | 60% |
| Gap Down Small | 9 | 0.52 | 10:00-10:30 | 0.35x | 11:00-11:30 | 0.28x | 126 | 33% |
| Gap Up Small | 9 | 0.57 | 10:00-10:30 | 0.09x | 14:30-15:15 | 0.50x | 231 | 78% |
| Gap Up Large | 3 | 0.57 | 10:00-10:30 | 0.23x | — | 0.67x | 208 | 67% |

**VBL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 5 | 0.48 | 11:30-12:00 | 0.00x | 14:30-15:15 | 0.00x | 163 | 60% |
| Gap Up Small | 12 | 0.44 | 11:00-11:30 | 0.28x | 14:30-15:15 | 0.30x | 202 | 58% |
| Gap Up Large | 3 | 0.63 | 10:00-10:30 | 0.22x | — | 0.87x | 245 | 67% |

**TRENT**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.48 | 11:30-12:00 | 0.32x | 14:30-15:15 | 0.38x | 198 | 50% |
| Gap Up Small | 12 | 0.47 | 10:00-10:30 | 0.22x | other | 0.57x | 180 | 67% |

**ANANTRAJ**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 9 | 0.62 | 10:30-11:00 | 0.31x | other | 0.25x | 156 | 33% |
| Gap Up Large | 6 | 0.48 | 10:00-10:30 | 0.40x | 14:30-15:15 | 0.10x | 156 | 0% |

**GLENMARK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 6 | 0.70 | 10:00-10:30 | 0.56x | other | 0.28x | 241 | 50% |
| Gap Up Small | 15 | 0.43 | 10:00-10:30 | 0.28x | 14:30-15:15 | 0.47x | 166 | 47% |
| Gap Up Large | 4 | 0.65 | 10:00-10:30 | 0.09x | other | 0.42x | 182 | 75% |

## Disabled Tickers

- **ADANIENT**: low EV/WR or OOS degraded
- **ADANIPORTS**: low EV/WR or OOS degraded
- **AEROFLEX**: low EV/WR or OOS degraded
- **BEL**: low EV/WR or OOS degraded
- **CAMS**: low EV/WR or OOS degraded
- **CUMMINSIND**: low EV/WR or OOS degraded
- **NBCC**: low EV/WR or OOS degraded
- **NTPC**: low EV/WR or OOS degraded
- **PFC**: low EV/WR or OOS degraded
- **SAILIFE**: low EV/WR or OOS degraded
- **SCI**: low EV/WR or OOS degraded

## Key Parameters

- Minimum sample: 15 trading days with morning low
- Minimum win rate: 50.0%
- Round-trip cost: 0.1%
- OOS train/test split: 70/30
- Monte Carlo iterations: 10,000

Generated: 2026-03-17 01:50