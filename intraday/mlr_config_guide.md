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
| KFINTECH | 5 | 0.631 | 77% | 64 | 0.5% | 15:15 |
| CAMS | 5 | 0.502 | 69% | 64 | 0.6% | 15:15 |
| IDBI | 5 | 0.749 | 66% | 64 | 0.8% | 15:15 |
| BSE | 5 | 0.617 | 73% | 64 | 0.6% | 15:15 |
| INDIANB | 5 | 0.517 | 67% | 64 | 0.9% | 15:15 |
| ADANIPOWER | 5 | 0.604 | 80% | 64 | 0.7% | 15:15 |
| BHEL | 5 | 0.657 | 78% | 64 | 0.7% | 15:15 |
| RVNL | 5 | 0.608 | 78% | 64 | 0.8% | 15:15 |
| NBCC | 5 | 0.569 | 73% | 64 | 0.6% | 15:15 |
| ANANTRAJ | 5 | 0.875 | 70% | 64 | 0.7% | 11:30 |
| AEROFLEX | 5 | 0.904 | 73% | 64 | 0.8% | 15:15 |
| SAILIFE | 4 | 0.550 | 52% | 64 | 0.8% | 14:30 |
| PFC | 4 | 0.359 | 53% | 64 | 0.8% | 15:15 |
| ABCAPITAL | 4 | 0.582 | 56% | 64 | 0.6% | 15:15 |
| TATAPOWER | 4 | 0.325 | 59% | 64 | 0.5% | 15:15 |
| COALINDIA | 4 | 0.428 | 64% | 64 | 0.7% | 14:00 |
| GPIL | 4 | 0.711 | 61% | 64 | 0.7% | 15:15 |
| ADANIENT | 4 | 0.404 | 61% | 64 | 0.4% | 15:15 |
| GRAPHITE | 4 | 0.900 | 59% | 64 | 0.9% | 11:30 |
| BEL | 4 | 0.388 | 58% | 64 | 0.5% | 15:15 |
| HAL | 4 | 0.338 | 55% | 64 | 0.6% | 15:15 |
| DATAPATTNS | 4 | 0.857 | 62% | 64 | 0.8% | 11:30 |
| MTARTECH | 4 | 0.966 | 58% | 64 | 0.8% | 15:15 |
| ADANIPORTS | 4 | 0.285 | 52% | 64 | 0.4% | 15:15 |
| FINCABLES | 4 | 0.448 | 66% | 64 | 0.9% | 15:15 |
| CUMMINSIND | 4 | 0.534 | 56% | 64 | 0.6% | 15:15 |
| NETWEB | 4 | 0.858 | 58% | 64 | 0.9% | 11:30 |
| EXIDEIND | 4 | 0.482 | 73% | 64 | 0.4% | 15:15 |
| TRENT | 4 | 0.435 | 64% | 64 | 0.7% | 14:30 |
| GLENMARK | 4 | 0.417 | 59% | 64 | 0.7% | 15:15 |

### Open-Type Profiles

Predictability-scored profiles per opening type (drop -> recovery -> timing):

**KFINTECH**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 17 | 0.41 | 10:00-10:30 | 0.7 | 0.6 | 10:00-10:30 | 41% |
| Flat | 21 | 0.41 | 10:00-10:30 | 1.1 | 0.7 | 10:00-10:30 | 33% |

**CAMS**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 3 | 0.73 | 10:00-10:30 | 0.2 | 1.4 | 10:00-10:30 | 33% |
| Gap Down Small | 18 | 0.40 | 10:00-10:30 | 0.7 | 0.8 | 10:00-10:30 | 44% |
| Gap Up Large | 5 | 0.62 | 10:00-10:30 | 0.9 | 1.3 | 10:30-11:00 | 80% |

**IDBI**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 7 | 0.53 | 10:00-10:30 | 1.6 | 2.4 | 10:30-11:00 | 57% |
| Gap Up Large | 6 | 0.50 | 10:30-11:00 | 1.6 | 1.9 | 10:30-11:00 | 33% |

**BSE**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 4 | 0.44 | 10:00-10:30 | 0.2 | 0.7 | 10:00-10:30 | 75% |
| Gap Up Large | 7 | 0.58 | 10:00-10:30 | 0.3 | 1.9 | 10:00-10:30 | 71% |

**INDIANB**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 4 | 0.70 | 10:00-10:30 | 0.6 | 1.7 | 10:00-10:30 | 50% |
| Gap Down Small | 10 | 0.48 | 10:00-10:30 | 0.8 | 0.8 | 10:00-10:30 | 70% |
| Gap Up Small | 14 | 0.46 | 10:00-10:30 | 0.6 | 0.5 | 10:00-10:30 | 57% |

**ADANIPOWER**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 14 | 0.43 | 10:00-10:30 | 0.7 | 0.9 | 10:00-10:30 | 43% |
| Flat | 26 | 0.45 | 10:00-10:30 | 0.7 | 1.1 | 10:00-10:30 | 38% |
| Gap Up Large | 9 | 0.46 | 10:30-11:00 | 1.1 | 1.9 | 10:30-11:00 | 44% |

**BHEL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 6 | 0.42 | 11:00-11:30 | 0.9 | 1.8 | — | 33% |
| Gap Down Small | 7 | 0.54 | 10:00-10:30 | 1.3 | 1.7 | 10:00-10:30 | 43% |

**RVNL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 6 | 0.50 | 10:00-10:30 | 0.8 | 1.7 | 10:00-10:30 | 83% |
| Gap Down Small | 16 | 0.46 | 10:00-10:30 | 0.9 | 1.9 | 10:00-10:30 | 38% |
| Gap Up Small | 17 | 0.50 | 10:00-10:30 | 0.1 | 1.5 | 10:00-10:30 | 59% |
| Gap Up Large | 6 | 0.64 | 11:00-11:30 | 2.0 | 3.1 | 11:00-11:30 | 50% |

**ANANTRAJ**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 6 | 0.55 | 10:00-10:30 | 0.6 | 1.3 | 10:00-10:30 | 67% |
| Gap Up Small | 11 | 0.42 | 10:30-11:00 | 1.0 | 2.0 | 10:30-11:00 | 46% |
| Gap Up Large | 4 | 0.49 | 10:00-10:30 | 1.6 | -0.4 | — | 0% |

**AEROFLEX**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 8 | 0.47 | 10:00-10:30 | 0.6 | 1.3 | 10:00-10:30 | 75% |
| Gap Up Large | 7 | 0.57 | 10:00-10:30 | -0.1 | 1.0 | 10:00-10:30 | 71% |

**SAILIFE**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 17 | 0.42 | 10:00-10:30 | 0.8 | 0.8 | 10:00-10:30 | 53% |
| Gap Up Large | 8 | 0.70 | 10:30-11:00 | 1.4 | 1.8 | 10:30-11:00 | 50% |

**PFC**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 4 | 0.69 | 10:30-11:00 | -0.0 | 0.4 | 10:30-11:00 | 75% |
| Gap Down Small | 14 | 0.40 | 10:00-10:30 | 0.4 | 1.6 | 10:00-10:30 | 50% |
| Gap Up Small | 14 | 0.44 | 10:00-10:30 | 0.5 | 0.6 | 10:00-10:30 | 57% |
| Gap Up Large | 3 | 0.50 | 10:00-10:30 | 2.7 | 0.7 | — | 0% |

**ABCAPITAL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 3 | 0.68 | 10:00-10:30 | -0.2 | 2.0 | 10:00-10:30 | 100% |
| Gap Up Large | 4 | 0.54 | 10:30-11:00 | 1.6 | 0.5 | 10:30-11:00 | 50% |

**TATAPOWER**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 5 | 0.44 | 10:00-10:30 | 0.1 | 0.6 | 10:00-10:30 | 60% |
| Gap Up Small | 15 | 0.42 | 10:00-10:30 | 0.6 | 0.8 | 10:30-11:00 | 67% |

**COALINDIA**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 9 | 0.60 | 10:30-11:00 | 0.8 | 1.5 | 11:00-11:30 | 67% |

**GPIL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 6 | 0.47 | 10:00-10:30 | 0.0 | 1.2 | 10:00-10:30 | 50% |
| Gap Down Small | 14 | 0.48 | 10:00-10:30 | 0.8 | 1.9 | 10:00-10:30 | 43% |
| Gap Up Small | 13 | 0.49 | 10:00-10:30 | 0.5 | 1.1 | 10:00-10:30 | 69% |
| Gap Up Large | 6 | 0.40 | 10:00-10:30 | 1.2 | 0.6 | 10:00-10:30 | 67% |

**ADANIENT**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 11 | 0.46 | 10:00-10:30 | 0.4 | 1.3 | 10:00-10:30 | 54% |
| Gap Up Large | 3 | 0.50 | 10:00-10:30 | -0.3 | 0.7 | 10:00-10:30 | 67% |

**GRAPHITE**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Flat | 28 | 0.48 | 10:00-10:30 | 0.3 | 2.2 | 10:00-10:30 | 64% |
| Gap Up Large | 6 | 0.43 | 10:30-11:00 | 1.7 | 0.6 | 10:30-11:00 | 50% |

**BEL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 9 | 0.47 | 10:00-10:30 | 0.7 | 0.9 | 10:00-10:30 | 56% |
| Gap Up Large | 3 | 0.84 | 10:00-10:30 | 2.1 | 1.6 | 10:00-10:30 | 67% |

**HAL**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 4 | 0.84 | 10:00-10:30 | 1.9 | 1.2 | 10:00-10:30 | 25% |
| Gap Down Small | 8 | 0.45 | 10:00-10:30 | 0.8 | 0.6 | 10:00-10:30 | 50% |
| Gap Up Small | 12 | 0.40 | 10:00-10:30 | 0.4 | 0.0 | 10:00-10:30 | 58% |

**DATAPATTNS**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 4 | 0.56 | 10:00-10:30 | -0.0 | 3.3 | 10:00-10:30 | 75% |
| Gap Up Small | 11 | 0.40 | 10:00-10:30 | 0.7 | 0.8 | 10:00-10:30 | 36% |
| Gap Up Large | 6 | 0.45 | 10:30-11:00 | 2.2 | 2.7 | 11:00-11:30 | 67% |

**MTARTECH**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 12 | 0.46 | 10:00-10:30 | 0.1 | 1.1 | 10:00-10:30 | 83% |
| Gap Up Large | 6 | 0.40 | 11:00-11:30 | 0.9 | 0.3 | 11:00-11:30 | 83% |

**ADANIPORTS**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 11 | 0.47 | 10:30-11:00 | 0.9 | 1.4 | 11:00-11:30 | 36% |
| Gap Up Large | 4 | 0.41 | 10:00-10:30 | 0.1 | 1.1 | 12:00-12:30 | 75% |

**FINCABLES**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Flat | 31 | 0.46 | 10:00-10:30 | 0.7 | 2.0 | 10:00-10:30 | 52% |
| Gap Up Large | 4 | 0.44 | 11:00-11:30 | 0.7 | -0.2 | 11:00-11:30 | 50% |

**CUMMINSIND**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 5 | 0.63 | 10:30-11:00 | 0.7 | 2.3 | 11:00-11:30 | 60% |
| Gap Down Small | 9 | 0.67 | 10:00-10:30 | 0.9 | 1.6 | 10:30-11:00 | 44% |
| Gap Up Small | 13 | 0.40 | 11:00-11:30 | 0.4 | -0.1 | 11:00-11:30 | 69% |

**NETWEB**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 7 | 0.41 | 10:00-10:30 | 0.8 | 0.6 | 10:00-10:30 | 43% |

**EXIDEIND**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Large | 4 | 0.49 | 10:00-10:30 | 0.9 | 0.8 | 10:30-11:00 | 75% |
| Gap Down Small | 12 | 0.48 | 10:00-10:30 | 0.8 | 0.4 | 10:30-11:00 | 33% |
| Gap Up Small | 8 | 0.57 | 10:00-10:30 | 0.2 | 0.9 | 10:00-10:30 | 75% |

**TRENT**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Up Small | 12 | 0.47 | 10:00-10:30 | 0.6 | 1.4 | 10:00-10:30 | 67% |

**GLENMARK**:

| Open Type | n | Pred | Low Window | Drop% | Recovery% | Recov By | Past Open% |
|-----------|---|------|------------|-------|-----------|----------|------------|
| Gap Down Small | 6 | 0.48 | 10:00-10:30 | 0.8 | 1.2 | 10:00-10:30 | 50% |
| Gap Up Small | 14 | 0.44 | 10:00-10:30 | 0.7 | 1.3 | 10:00-10:30 | 43% |
| Gap Up Large | 4 | 0.65 | 10:00-10:30 | 0.3 | 1.0 | 10:00-10:30 | 75% |

## Disabled Tickers

- **HAVELLS**: low EV/WR or OOS degraded
- **NTPC**: low EV/WR or OOS degraded
- **SCI**: low EV/WR or OOS degraded
- **VBL**: low EV/WR or OOS degraded

## Key Parameters

- Minimum sample: 15 trading days with morning low
- Minimum win rate: 50.0%
- Round-trip cost: 0.1%
- OOS train/test split: 70/30
- Monte Carlo iterations: 10,000

Generated: 2026-03-16 23:44