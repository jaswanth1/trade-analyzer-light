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
| KFINTECH | 5 | 0.531 | 69% | 58 | 0.6% | 0.26x | 0.29x | 10:30-11:00 | 13:30-14:00 | 148min | 15:15 |
| GRAPHITE | 5 | 0.907 | 71% | 58 | 0.9% | 0.12x | 0.53x | 10:30-11:00 | 12:00-12:30 | 136min | 11:30 |
| BHEL | 5 | 0.544 | 67% | 58 | 0.8% | 0.22x | 0.39x | 11:00-11:30 | 14:30-15:15 | 149min | 15:15 |
| DATAPATTNS | 5 | 0.709 | 66% | 58 | 1.0% | 0.13x | 0.48x | 10:30-11:00 | 14:30-15:15 | 132min | 15:15 |
| CAMS | 4 | 0.294 | 53% | 58 | 0.6% | 0.28x | 0.31x | 11:30-12:00 | 12:00-12:30 | 141min | 15:15 |
| IDBI | 4 | 0.663 | 59% | 58 | 0.7% | 0.23x | 0.42x | 11:00-11:30 | 14:30-15:15 | 109min | 15:15 |
| BSE | 4 | 0.423 | 57% | 58 | 0.8% | 0.18x | 0.39x | 10:00-10:30 | 14:30-15:15 | 164min | 15:15 |
| ABCAPITAL | 4 | 0.582 | 57% | 58 | 0.7% | 0.27x | 0.31x | 10:00-10:30 | 14:30-15:15 | 159min | 15:15 |
| INDIANB | 4 | 0.469 | 50% | 58 | 1.0% | 0.22x | 0.42x | 10:30-11:00 | 14:30-15:15 | 170min | 14:30 |
| ADANIPOWER | 4 | 0.498 | 67% | 58 | 0.7% | 0.21x | 0.42x | 10:00-10:30 | 14:30-15:15 | 140min | 15:15 |
| COALINDIA | 4 | 0.292 | 53% | 58 | 0.7% | 0.13x | 0.54x | 10:30-11:00 | 14:30-15:15 | 152min | 14:00 |
| MTARTECH | 4 | 0.867 | 52% | 58 | 0.8% | 0.05x | 0.51x | 11:00-11:30 | 11:30-12:00 | 152min | 15:15 |
| RVNL | 4 | 0.527 | 60% | 58 | 0.8% | 0.23x | 0.32x | 10:30-11:00 | 11:30-12:00 | 148min | 15:15 |
| FINCABLES | 4 | 0.293 | 53% | 58 | 0.9% | 0.24x | 0.40x | 11:00-11:30 | 14:30-15:15 | 156min | 15:15 |
| HAVELLS | 4 | 0.306 | 53% | 58 | 0.6% | 0.27x | 0.41x | 10:00-10:30 | 14:30-15:15 | 162min | 15:15 |
| NETWEB | 4 | 0.704 | 57% | 58 | 0.8% | 0.18x | 0.39x | 10:30-11:00 | 12:30-13:00 | 115min | 11:30 |
| TRENT | 4 | 0.442 | 53% | 58 | 0.7% | 0.26x | 0.36x | 10:30-11:00 | 14:30-15:15 | 154min | 14:30 |
| ANANTRAJ | 4 | 0.689 | 55% | 58 | 0.7% | 0.25x | 0.31x | 10:30-11:00 | 11:00-11:30 | 136min | 15:15 |
| GLENMARK | 4 | 0.436 | 57% | 58 | 0.7% | 0.26x | 0.44x | 10:00-10:30 | 14:30-15:15 | 164min | 15:15 |
| AEROFLEX | 4 | 0.702 | 50% | 58 | 0.8% | 0.12x | 0.44x | 11:00-11:30 | 10:00-10:30 | 122min | 15:15 |

### Open-Type Profiles

Predictability-scored profiles per opening type (drop -> recovery -> timing):

**KFINTECH**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 5 | 0.59 | 11:00-11:30 | 0.32x | 10:00-10:30 | 0.32x | 159 | 40% |
| Gap Down Small | 15 | 0.50 | 10:00-10:30 | 0.24x | 14:30-15:15 | 0.34x | 170 | 47% |
| Gap Up Large | 5 | 0.48 | 10:00-10:30 | 0.23x | 10:30-11:00 | 0.27x | 89 | 40% |

**GRAPHITE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 20 | 0.60 | 10:00-10:30 | 0.09x | 14:30-15:15 | 0.63x | 136 | 75% |
| Gap Up Large | 8 | 0.54 | 10:00-10:30 | 0.33x | 13:30-14:00 | 0.25x | 161 | 50% |

**BHEL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 5 | 0.48 | 10:00-10:30 | 0.26x | 10:30-11:00 | 0.50x | 109 | 60% |
| Gap Up Small | 19 | 0.43 | 10:00-10:30 | 0.16x | 14:30-15:15 | 0.48x | 161 | 63% |
| Gap Up Large | 3 | 0.71 | 10:00-10:30 | 0.61x | other | -0.04x | 232 | 0% |

**DATAPATTNS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 12 | 0.40 | 10:00-10:30 | 0.06x | 14:30-15:15 | 0.45x | 138 | 42% |
| Gap Up Large | 5 | 0.70 | 10:30-11:00 | 0.36x | other | 0.40x | 177 | 80% |

**CAMS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 5 | 0.66 | 10:00-10:30 | 0.18x | other | 0.62x | 248 | 40% |
| Gap Up Large | 5 | 0.60 | 10:00-10:30 | 0.14x | 12:00-12:30 | 0.50x | 169 | 80% |

**IDBI**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.46 | 11:00-11:30 | 0.41x | 11:30-12:00 | 0.28x | 88 | 38% |

**BSE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.40 | 10:00-10:30 | 0.11x | other | 0.53x | 168 | 83% |
| Gap Up Small | 14 | 0.43 | 10:00-10:30 | 0.11x | 14:30-15:15 | 0.38x | 176 | 50% |
| Gap Up Large | 9 | 0.66 | 10:00-10:30 | 0.01x | 14:30-15:15 | 0.59x | 208 | 78% |

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
| Gap Up Small | 15 | 0.43 | 10:00-10:30 | 0.22x | other | 0.44x | 206 | 53% |

**ADANIPOWER**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.50 | — | 0.00x | — | 0.00x | 174 | 100% |
| Gap Down Small | 13 | 0.46 | 10:00-10:30 | 0.23x | 11:30-12:00 | 0.61x | 122 | 46% |
| Flat | 19 | 0.44 | 10:00-10:30 | 0.20x | 14:30-15:15 | 0.33x | 174 | 42% |

**COALINDIA**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 9 | 0.54 | 11:00-11:30 | 0.30x | other | 0.51x | 233 | 56% |
| Gap Up Large | 3 | 0.63 | 10:00-10:30 | 0.00x | — | 0.00x | 230 | 100% |

**MTARTECH**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 11 | 0.44 | 10:00-10:30 | 0.10x | other | 0.44x | 198 | 73% |
| Gap Up Large | 8 | 0.44 | 10:00-10:30 | 0.16x | other | 0.47x | 180 | 75% |

**RVNL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 11 | 0.41 | 10:30-11:00 | 0.17x | 14:30-15:15 | 0.34x | 184 | 46% |
| Gap Up Small | 16 | 0.51 | 10:00-10:30 | 0.01x | 11:00-11:30 | 0.62x | 100 | 56% |
| Gap Up Large | 6 | 0.69 | 11:00-11:30 | 0.66x | other | 0.13x | 236 | 50% |

**FINCABLES**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.47 | 10:00-10:30 | 0.03x | — | 0.89x | 156 | 67% |
| Flat | 23 | 0.46 | 10:00-10:30 | 0.16x | other | 0.45x | 172 | 56% |
| Gap Up Large | 5 | 0.48 | 11:00-11:30 | 0.21x | 11:30-12:00 | 0.47x | 153 | 40% |

**HAVELLS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 5 | 0.70 | 10:30-11:00 | 0.39x | 14:30-15:15 | 0.47x | 160 | 60% |
| Flat | 33 | 0.48 | 10:00-10:30 | 0.30x | 14:30-15:15 | 0.38x | 182 | 39% |

**NETWEB**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Large | 9 | 0.40 | 10:00-10:30 | 0.57x | 12:00-12:30 | 0.24x | 101 | 44% |

**TRENT**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.48 | 11:30-12:00 | 0.32x | 14:30-15:15 | 0.38x | 198 | 50% |
| Gap Up Small | 12 | 0.47 | 10:00-10:30 | 0.22x | other | 0.57x | 180 | 67% |

**ANANTRAJ**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 9 | 0.60 | 10:00-10:30 | 0.30x | 11:30-12:00 | 0.27x | 155 | 33% |
| Gap Up Large | 6 | 0.48 | 10:00-10:30 | 0.40x | 14:30-15:15 | 0.10x | 156 | 0% |

**GLENMARK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 5 | 0.65 | 10:00-10:30 | 0.61x | other | 0.22x | 252 | 40% |
| Gap Up Large | 4 | 0.65 | 10:00-10:30 | 0.09x | other | 0.42x | 182 | 75% |

**AEROFLEX**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.64 | 10:30-11:00 | 0.16x | 14:30-15:15 | 0.43x | 180 | 71% |
| Gap Up Large | 8 | 0.56 | 10:00-10:30 | 0.22x | other | 0.55x | 170 | 75% |

## Disabled Tickers

- **ADANIENT**: low EV/WR or OOS degraded
- **ADANIPORTS**: low EV/WR or OOS degraded
- **BEL**: low EV/WR or OOS degraded
- **CUMMINSIND**: low EV/WR or OOS degraded
- **EXIDEIND**: low EV/WR or OOS degraded
- **GPIL**: low EV/WR or OOS degraded
- **HAL**: low EV/WR or OOS degraded
- **NBCC**: low EV/WR or OOS degraded
- **NTPC**: low EV/WR or OOS degraded
- **PFC**: low EV/WR or OOS degraded
- **SAILIFE**: low EV/WR or OOS degraded
- **SCI**: low EV/WR or OOS degraded
- **TATAPOWER**: low EV/WR or OOS degraded
- **VBL**: low EV/WR or OOS degraded

## Key Parameters

- Minimum sample: 15 trading days with morning low
- Minimum win rate: 50.0%
- Round-trip cost: 0.1%
- OOS train/test split: 70/30
- Monte Carlo iterations: 10,000

Generated: 2026-03-19 08:53