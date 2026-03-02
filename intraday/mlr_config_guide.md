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

| Ticker | Edge | EV | WR% | n | Avg Rec | Cutoff |
|--------|------|----|-----|---|---------|--------|
| KFINTECH | 5 | 0.641 | 78% | 58 | 0.5% | 15:15 |
| ADANIPOWER | 5 | 0.605 | 79% | 58 | 0.6% | 14:30 |
| GRAPHITE | 5 | 0.851 | 67% | 58 | 0.8% | 11:30 |
| BHEL | 5 | 0.615 | 74% | 58 | 0.7% | 15:15 |
| SCI | 5 | 0.547 | 69% | 58 | 0.6% | 15:15 |
| NBCC | 5 | 0.556 | 72% | 58 | 0.5% | 15:15 |
| CUMMINSIND | 5 | 0.514 | 67% | 58 | 0.6% | 15:15 |
| VBL | 5 | 0.602 | 78% | 58 | 0.7% | 14:30 |
| ANANTRAJ | 5 | 0.876 | 71% | 58 | 0.7% | 15:15 |
| AEROFLEX | 5 | 0.930 | 76% | 58 | 0.6% | 15:15 |
| SAILIFE | 4 | 0.542 | 52% | 58 | 0.7% | 14:30 |
| CAMS | 4 | 0.496 | 69% | 58 | 0.5% | 15:15 |
| IDBI | 4 | 0.708 | 64% | 58 | 0.8% | 15:15 |
| BSE | 4 | 0.659 | 59% | 58 | 0.6% | 15:15 |
| PFC | 4 | 0.338 | 52% | 58 | 0.8% | 15:15 |
| ABCAPITAL | 4 | 0.607 | 59% | 58 | 0.6% | 15:15 |
| INDIANB | 4 | 0.478 | 53% | 58 | 0.9% | 14:30 |
| TATAPOWER | 4 | 0.300 | 59% | 58 | 0.4% | 15:15 |
| COALINDIA | 4 | 0.397 | 62% | 58 | 0.6% | 14:00 |
| GPIL | 4 | 0.691 | 60% | 58 | 0.7% | 15:15 |
| ADANIENT | 4 | 0.395 | 60% | 58 | 0.4% | 15:15 |
| BEL | 4 | 0.358 | 55% | 58 | 0.5% | 15:15 |
| HAL | 4 | 0.331 | 55% | 58 | 0.5% | 15:15 |
| DATAPATTNS | 4 | 0.823 | 62% | 58 | 0.8% | 11:30 |
| MTARTECH | 4 | 0.915 | 57% | 58 | 0.9% | 15:15 |
| RVNL | 4 | 0.581 | 64% | 58 | 0.8% | 15:15 |
| FINCABLES | 4 | 0.402 | 62% | 58 | 1.0% | 15:15 |
| HAVELLS | 4 | 0.313 | 53% | 58 | 0.5% | 14:30 |
| NETWEB | 4 | 0.822 | 55% | 58 | 0.8% | 11:30 |
| EXIDEIND | 4 | 0.456 | 72% | 58 | 0.4% | 15:15 |
| TRENT | 4 | 0.404 | 62% | 58 | 0.7% | 14:30 |
| GLENMARK | 4 | 0.401 | 59% | 58 | 0.6% | 15:15 |

### Open-Type Profiles

Predictability-scored profiles per opening type (drop -> recovery -> timing):

**KFINTECH**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Flat | 18 | 0.42 | 10:00-10:30 | 1.1 | 0.7 | 10:00-10:30 | 28% |

**ADANIPOWER**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 13 | 0.43 | 10:00-10:30 | 1.0 | 0.9 | 10:00-10:30 | 38% |
| Flat | 25 | 0.45 | 10:00-10:30 | 0.7 | 1.2 | 10:00-10:30 | 40% |

**GRAPHITE**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Flat | 27 | 0.47 | 10:00-10:30 | 0.2 | 2.1 | 10:00-10:30 | 63% |
| Gap Up Large | 6 | 0.43 | 10:30-11:00 | 1.7 | 0.6 | 10:30-11:00 | 50% |

**BHEL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 6 | 0.42 | 11:00-11:30 | 0.9 | 1.8 | — | 33% |
| Gap Down Small | 5 | 0.48 | 10:00-10:30 | 1.4 | 0.9 | 10:00-10:30 | 40% |

**SCI**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Up Large | 6 | 0.52 | 10:00-10:30 | -0.0 | 3.7 | 10:00-10:30 | 100% |

**CUMMINSIND**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 5 | 0.63 | 10:30-11:00 | 0.7 | 2.3 | 11:00-11:30 | 60% |
| Gap Down Small | 7 | 0.65 | 10:00-10:30 | 0.4 | 1.6 | 10:30-11:00 | 57% |
| Flat | 32 | 0.41 | 10:00-10:30 | 0.4 | 0.9 | 10:00-10:30 | 53% |
| Gap Up Small | 13 | 0.42 | 10:00-10:30 | 0.4 | 0.8 | 10:00-10:30 | 77% |

**VBL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 3 | 0.54 | 11:30-12:00 | -1.1 | 0.7 | 11:30-12:00 | 100% |
| Gap Down Small | 13 | 0.43 | 10:00-10:30 | 0.8 | 1.4 | 10:00-10:30 | 38% |
| Gap Up Small | 12 | 0.45 | 11:00-11:30 | 0.8 | 0.8 | 11:00-11:30 | 58% |

**ANANTRAJ**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 4 | 0.44 | 10:00-10:30 | -0.2 | 0.4 | 10:00-10:30 | 50% |
| Gap Up Small | 11 | 0.42 | 10:30-11:00 | 1.0 | 2.0 | 10:30-11:00 | 46% |
| Gap Up Large | 4 | 0.49 | 10:00-10:30 | 1.6 | -0.4 | — | 0% |

**AEROFLEX**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 5 | 0.41 | 10:00-10:30 | 0.6 | 0.6 | 10:00-10:30 | 60% |
| Gap Up Large | 7 | 0.57 | 10:00-10:30 | -0.1 | 1.0 | 10:00-10:30 | 71% |

**SAILIFE**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 15 | 0.43 | 10:00-10:30 | 0.7 | 1.0 | 10:00-10:30 | 53% |
| Gap Up Large | 6 | 0.61 | 10:30-11:00 | 0.5 | 1.8 | 10:30-11:00 | 67% |

**CAMS**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 17 | 0.47 | 10:00-10:30 | 0.7 | 1.0 | 10:00-10:30 | 47% |
| Gap Up Large | 4 | 0.54 | 11:00-11:30 | 0.8 | 1.6 | 11:00-11:30 | 75% |

**IDBI**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 4 | 0.58 | 11:00-11:30 | 0.8 | -0.3 | — | 50% |
| Gap Up Large | 6 | 0.50 | 10:30-11:00 | 1.6 | 1.9 | 10:30-11:00 | 33% |

**BSE**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 3 | 0.42 | 10:00-10:30 | 0.1 | -0.1 | 10:30-11:00 | 67% |
| Gap Up Large | 6 | 0.61 | 10:00-10:30 | 0.4 | 2.4 | 10:00-10:30 | 67% |

**PFC**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 3 | 0.68 | 10:30-11:00 | 0.1 | 0.5 | 10:30-11:00 | 67% |
| Gap Down Small | 11 | 0.44 | 10:00-10:30 | 0.3 | 1.4 | 10:00-10:30 | 54% |
| Gap Up Small | 14 | 0.44 | 10:00-10:30 | 0.5 | 0.6 | 10:00-10:30 | 57% |
| Gap Up Large | 3 | 0.72 | 10:30-11:00 | 3.0 | 1.7 | — | 0% |

**ABCAPITAL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Up Large | 4 | 0.54 | 10:30-11:00 | 1.6 | 0.5 | 10:30-11:00 | 50% |

**INDIANB**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 8 | 0.53 | 10:00-10:30 | 0.4 | 0.8 | 10:00-10:30 | 75% |
| Gap Up Small | 14 | 0.46 | 10:00-10:30 | 0.6 | 0.5 | 10:00-10:30 | 57% |

**TATAPOWER**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 5 | 0.44 | 10:00-10:30 | 0.1 | 0.6 | 10:00-10:30 | 60% |
| Gap Up Small | 13 | 0.45 | 10:00-10:30 | 0.5 | 0.8 | 10:30-11:00 | 69% |

**COALINDIA**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 9 | 0.60 | 10:30-11:00 | 0.8 | 1.5 | 11:00-11:30 | 67% |

**GPIL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 5 | 0.57 | 10:00-10:30 | -0.1 | 0.7 | — | 40% |
| Gap Down Small | 11 | 0.50 | 10:00-10:30 | 0.6 | 1.9 | 10:00-10:30 | 46% |
| Gap Up Small | 13 | 0.49 | 10:00-10:30 | 0.5 | 1.1 | 10:00-10:30 | 69% |
| Gap Up Large | 5 | 0.49 | 10:00-10:30 | 0.8 | 0.6 | 10:00-10:30 | 80% |

**ADANIENT**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 9 | 0.42 | 10:00-10:30 | 0.4 | 1.2 | 10:00-10:30 | 56% |
| Gap Up Large | 3 | 0.50 | 10:00-10:30 | -0.3 | 0.7 | 10:00-10:30 | 67% |

**BEL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 7 | 0.51 | 10:00-10:30 | 0.5 | 1.1 | 10:00-10:30 | 57% |

**HAL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 3 | 0.80 | 10:00-10:30 | 2.6 | 1.2 | — | 0% |
| Gap Down Small | 6 | 0.42 | 10:00-10:30 | 0.7 | 0.6 | 10:00-10:30 | 50% |
| Flat | 36 | 0.40 | 10:00-10:30 | 0.5 | 0.8 | 10:00-10:30 | 61% |
| Gap Up Small | 12 | 0.40 | 10:00-10:30 | 0.4 | 0.0 | 10:00-10:30 | 58% |

**DATAPATTNS**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 3 | 0.58 | 10:00-10:30 | -1.4 | 3.3 | 10:00-10:30 | 100% |
| Gap Up Small | 10 | 0.41 | 10:00-10:30 | 0.3 | 0.8 | 10:00-10:30 | 40% |
| Gap Up Large | 5 | 0.70 | 10:30-11:00 | 2.1 | 4.3 | 11:00-11:30 | 80% |

**MTARTECH**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 12 | 0.46 | 10:00-10:30 | 0.1 | 1.1 | 10:00-10:30 | 83% |
| Gap Up Large | 6 | 0.40 | 11:00-11:30 | 0.9 | 0.3 | 11:00-11:30 | 83% |

**RVNL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 14 | 0.45 | 10:00-10:30 | 0.9 | 1.7 | 10:00-10:30 | 36% |
| Gap Up Small | 17 | 0.50 | 10:00-10:30 | 0.1 | 1.5 | 10:00-10:30 | 59% |
| Gap Up Large | 5 | 0.72 | 11:00-11:30 | 2.3 | 3.1 | 11:00-11:30 | 60% |

**FINCABLES**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Flat | 29 | 0.49 | 10:00-10:30 | 0.6 | 2.0 | 10:00-10:30 | 55% |
| Gap Up Large | 3 | 0.57 | 11:00-11:30 | 0.6 | 1.6 | 11:00-11:30 | 67% |

**HAVELLS**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 5 | 0.73 | 10:00-10:30 | 0.8 | 1.4 | 10:00-10:30 | 80% |
| Flat | 39 | 0.44 | 10:00-10:30 | 0.6 | 0.7 | 10:00-10:30 | 49% |

**NETWEB**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 4 | 0.59 | 10:00-10:30 | 0.3 | -0.5 | — | 25% |

**EXIDEIND**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 12 | 0.48 | 10:00-10:30 | 0.8 | 0.4 | 10:30-11:00 | 33% |
| Gap Up Small | 8 | 0.57 | 10:00-10:30 | 0.2 | 0.9 | 10:00-10:30 | 75% |

**TRENT**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 9 | 0.40 | 10:00-10:30 | 0.8 | 1.0 | 10:00-10:30 | 44% |
| Gap Up Small | 12 | 0.47 | 10:00-10:30 | 0.6 | 1.4 | 10:00-10:30 | 67% |

**GLENMARK**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 4 | 0.78 | 10:00-10:30 | 0.2 | 1.7 | 10:00-10:30 | 75% |
| Gap Up Small | 13 | 0.43 | 10:00-10:30 | 0.8 | 0.9 | 10:00-10:30 | 38% |
| Gap Up Large | 4 | 0.65 | 10:00-10:30 | 0.3 | 1.0 | 10:00-10:30 | 75% |

## Disabled Tickers

- **ADANIPORTS**: low EV/WR or OOS degraded
- **NTPC**: low EV/WR or OOS degraded

## Key Parameters

- Minimum sample: 15 trading days with morning low
- Minimum win rate: 50.0%
- Round-trip cost: 0.1%
- OOS train/test split: 70/30
- Monte Carlo iterations: 10,000

Generated: 2026-03-02 15:10