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
| SAILIFE | 5 | 0.890 | 69% | 58 | 0.9% | 0.00x | 0.00x | 10:00-10:30 | 10:00-10:30 | 172min | 15:15 |
| KFINTECH | 5 | 0.597 | 74% | 58 | 0.5% | 0.27x | 0.26x | 10:30-11:00 | 13:30-14:00 | 142min | 15:15 |
| INDIANB | 5 | 0.517 | 69% | 58 | 1.2% | 0.22x | 0.37x | 10:30-11:00 | 13:30-14:00 | 171min | 14:30 |
| ABCAPITAL | 5 | 0.517 | 69% | 58 | 1.2% | 0.00x | 0.00x | 10:00-10:30 | 10:00-10:30 | 171min | 14:30 |
| JSWENERGY | 5 | 0.606 | 75% | 57 | 0.8% | 0.55x | 1.24x | 10:00-10:30 | 12:00-12:30 | 154min | 15:15 |
| HCLTECH | 5 | 0.512 | 67% | 58 | 0.5% | 0.24x | 0.32x | 11:00-11:30 | 14:30-15:15 | 135min | 15:15 |
| AETHER | 5 | 0.571 | 71% | 58 | 0.6% | 0.09x | 0.25x | 10:30-11:00 | 14:30-15:15 | 120min | 15:15 |
| PNB | 4 | 0.405 | 62% | 58 | 0.8% | 0.25x | 0.29x | 10:00-10:30 | 14:30-15:15 | 160min | 15:15 |
| RECLTD | 4 | 0.475 | 60% | 58 | 0.9% | 0.18x | 0.38x | 11:00-11:30 | 14:30-15:15 | 158min | 15:15 |
| BAJAJHFL | 4 | 0.375 | 50% | 58 | 0.5% | 0.35x | 0.23x | 11:00-11:30 | 14:30-15:15 | 162min | 15:15 |
| MCX | 4 | 0.644 | 57% | 58 | 1.2% | 0.21x | 0.36x | 10:30-11:00 | 14:30-15:15 | 150min | 14:30 |
| NTPC | 4 | 0.251 | 51% | 57 | 0.5% | 1.15x | 5.22x | 11:30-12:00 | 14:30-15:15 | 157min | 15:15 |
| TATAPOWER | 4 | 0.330 | 53% | 58 | 0.5% | 0.06x | 0.05x | 10:30-11:00 | 13:30-14:00 | 126min | 15:15 |
| RELIANCE | 4 | 0.330 | 53% | 58 | 0.5% | 0.06x | 0.05x | 10:30-11:00 | 13:30-14:00 | 126min | 15:15 |
| JIOFIN | 4 | 0.330 | 53% | 58 | 0.5% | 0.20x | 0.16x | 10:30-11:00 | 13:30-14:00 | 126min | 15:15 |
| COALINDIA | 4 | 0.397 | 53% | 57 | 0.6% | 1.05x | 1.46x | 11:00-11:30 | 14:30-15:15 | 156min | 14:00 |
| POWERGRID | 4 | 0.346 | 55% | 58 | 0.6% | 0.24x | 0.32x | 11:30-12:00 | 10:30-11:00 | 132min | 15:15 |
| BPCL | 4 | 0.464 | 50% | 58 | 0.8% | 0.29x | 0.26x | 10:00-10:30 | 14:30-15:15 | 156min | 14:30 |
| JSWSTEEL | 4 | 0.357 | 53% | 58 | 0.6% | 0.24x | 0.36x | 10:30-11:00 | 14:30-15:15 | 156min | 15:15 |
| HINDPETRO | 4 | 0.585 | 60% | 57 | 0.7% | 1.56x | 1.07x | 10:30-11:00 | 11:00-11:30 | 151min | 14:30 |
| GAIL | 4 | 0.434 | 57% | 58 | 0.8% | 0.04x | 0.09x | 11:00-11:30 | 14:30-15:15 | 152min | 14:00 |
| ADANIGREEN | 4 | 0.434 | 57% | 58 | 0.8% | 0.04x | 0.09x | 11:00-11:30 | 14:30-15:15 | 152min | 14:00 |
| VEDL | 4 | 0.624 | 57% | 58 | 0.9% | 0.16x | 0.41x | 10:30-11:00 | 14:30-15:15 | 174min | 15:15 |
| ADANIENT | 4 | 0.619 | 55% | 58 | 0.9% | 0.03x | 0.07x | 11:00-11:30 | 14:30-15:15 | 145min | 15:15 |
| GPIL | 4 | 0.619 | 55% | 58 | 0.9% | 0.03x | 0.07x | 11:00-11:30 | 14:30-15:15 | 145min | 15:15 |
| GRAPHITE | 4 | 1.070 | 55% | 58 | 0.7% | 0.15x | 0.43x | 10:30-11:00 | 14:30-15:15 | 137min | 15:15 |
| BHEL | 4 | 0.596 | 53% | 58 | 0.9% | 0.20x | 0.35x | 10:30-11:00 | 14:30-15:15 | 162min | 15:15 |
| MTARTECH | 4 | 0.724 | 52% | 58 | 1.1% | 0.15x | 0.36x | 11:00-11:30 | 14:30-15:15 | 120min | 15:15 |
| SCI | 4 | 0.724 | 52% | 58 | 1.1% | 2.63x | 5.89x | 10:30-11:00 | 14:30-15:15 | 120min | 15:15 |
| RVNL | 4 | 0.613 | 61% | 57 | 0.9% | 1.60x | 1.66x | 10:00-10:30 | 12:00-12:30 | 162min | 14:30 |
| COCHINSHIP | 4 | 0.461 | 71% | 58 | 0.7% | 0.22x | 0.31x | 10:30-11:00 | 12:00-12:30 | 134min | 15:15 |
| LT | 4 | 0.346 | 51% | 57 | 0.7% | 86.83x | 133.90x | 10:00-10:30 | 14:30-15:15 | 174min | 15:15 |
| CUMMINSIND | 4 | 0.629 | 50% | 58 | 1.1% | 0.22x | 0.42x | 10:30-11:00 | 13:30-14:00 | 175min | 14:30 |
| HAVELLS | 4 | 0.378 | 59% | 58 | 0.7% | 0.31x | 0.31x | 11:00-11:30 | 14:30-15:15 | 164min | 15:15 |
| FINCABLES | 4 | 0.393 | 60% | 58 | 0.9% | 0.21x | 0.40x | 11:00-11:30 | 14:30-15:15 | 173min | 15:15 |
| TCS | 4 | 0.327 | 53% | 58 | 0.3% | 0.29x | 0.18x | 11:00-11:30 | 12:30-13:00 | 135min | 15:15 |
| INFY | 4 | 0.384 | 60% | 58 | 0.5% | 0.26x | 0.23x | 11:00-11:30 | 12:00-12:30 | 150min | 15:15 |
| WIPRO | 4 | 0.353 | 64% | 58 | 0.4% | 0.27x | 0.21x | 10:30-11:00 | 12:00-12:30 | 130min | 15:15 |
| NAUKRI | 4 | 0.707 | 57% | 58 | 0.9% | 0.32x | 0.30x | 10:30-11:00 | 14:30-15:15 | 156min | 15:15 |
| BAJAJ-AUTO | 4 | 0.373 | 55% | 58 | 0.6% | 0.08x | 0.14x | 11:00-11:30 | 14:00-14:30 | 150min | 15:15 |
| TVSMOTOR | 4 | 0.460 | 55% | 58 | 0.8% | 0.27x | 0.34x | 11:00-11:30 | 14:30-15:15 | 162min | 15:15 |
| MOTHERSON | 4 | 0.574 | 52% | 58 | 0.8% | 0.24x | 0.34x | 10:00-10:30 | 14:00-14:30 | 152min | 15:15 |
| EXIDEIND | 4 | 0.409 | 55% | 58 | 0.6% | 0.24x | 0.31x | 10:30-11:00 | 14:30-15:15 | 170min | 15:15 |
| TMPV | 4 | 0.461 | 53% | 58 | 0.7% | 0.19x | 0.34x | 10:00-10:30 | 11:00-11:30 | 134min | 14:30 |
| ITC | 4 | 0.397 | 52% | 58 | 0.5% | 2.57x | 2.10x | 11:00-11:30 | 13:30-14:00 | 156min | 15:15 |
| TRENT | 4 | 0.558 | 55% | 58 | 0.6% | 0.23x | 0.34x | 11:00-11:30 | 14:30-15:15 | 148min | 15:15 |
| TATACONSUM | 4 | 0.350 | 59% | 58 | 0.6% | 0.26x | 0.35x | 11:00-11:30 | 14:30-15:15 | 151min | 15:15 |
| PIDILITIND | 4 | 0.363 | 50% | 58 | 0.6% | 0.31x | 0.27x | 10:30-11:00 | 14:30-15:15 | 158min | 15:15 |
| VBL | 4 | 0.399 | 50% | 58 | 0.6% | 0.24x | 0.31x | 11:00-11:30 | 14:00-14:30 | 159min | 15:15 |
| UNITDSPR | 4 | 0.373 | 66% | 58 | 0.7% | 0.27x | 0.36x | 10:00-10:30 | 14:30-15:15 | 152min | 15:15 |
| GODREJCP | 4 | 0.462 | 67% | 58 | 0.6% | 0.30x | 0.27x | 11:00-11:30 | 11:00-11:30 | 154min | 15:15 |
| ETERNAL | 4 | 0.482 | 64% | 58 | 0.8% | 0.28x | 0.23x | 10:00-10:30 | 12:30-13:00 | 128min | 15:15 |
| DMART | 4 | 0.422 | 50% | 58 | 0.7% | 0.19x | 0.42x | 10:30-11:00 | 11:30-12:00 | 156min | 14:30 |
| DLF | 4 | 0.527 | 53% | 58 | 0.7% | 0.28x | 0.27x | 10:00-10:30 | 14:30-15:15 | 147min | 15:15 |
| LODHA | 4 | 0.594 | 50% | 58 | 0.8% | 0.30x | 0.24x | 11:00-11:30 | 14:30-15:15 | 154min | 15:15 |
| DRREDDY | 4 | 0.315 | 53% | 58 | 0.5% | 0.23x | 0.34x | 10:30-11:00 | 14:30-15:15 | 156min | 15:15 |
| CIPLA | 4 | 0.281 | 52% | 58 | 0.5% | 0.31x | 0.29x | 10:00-10:30 | 14:30-15:15 | 153min | 15:15 |
| DIVISLAB | 4 | 0.274 | 51% | 57 | 0.5% | 5.10x | 4.57x | 10:00-10:30 | 14:30-15:15 | 151min | 15:15 |
| GLENMARK | 4 | 0.494 | 66% | 58 | 0.9% | 0.31x | 0.35x | 10:00-10:30 | 14:30-15:15 | 162min | 15:15 |
| INDIGO | 4 | 0.376 | 53% | 58 | 0.8% | 0.18x | 0.38x | 10:00-10:30 | 14:30-15:15 | 160min | 15:15 |
| TORNTPHARM | 4 | 0.376 | 60% | 58 | 0.7% | 0.22x | 0.38x | 11:00-11:30 | 14:30-15:15 | 176min | 14:30 |
| AEROFLEX | 4 | 0.909 | 57% | 58 | 1.1% | 0.09x | 0.49x | 11:00-11:30 | 10:00-10:30 | 129min | 15:15 |

### Open-Type Profiles

Predictability-scored profiles per opening type (drop -> recovery -> timing):

**SAILIFE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 58 | 0.42 | 10:00-10:30 | 0.00x | other | 0.00x | 172 | 0% |

**KFINTECH**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.40 | 10:00-10:30 | 0.23x | 11:00-11:30 | 0.38x | 145 | 50% |
| Gap Down Small | 11 | 0.43 | 10:00-10:30 | 0.40x | 10:30-11:00 | 0.08x | 130 | 27% |

**INDIANB**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 11 | 0.40 | 10:00-10:30 | 0.50x | 14:30-15:15 | 0.03x | 162 | 27% |
| Gap Down Small | 9 | 0.54 | 10:00-10:30 | 0.12x | other | 0.41x | 164 | 78% |
| Flat | 18 | 0.41 | 10:00-10:30 | 0.20x | 13:30-14:00 | 0.41x | 153 | 56% |
| Gap Up Small | 13 | 0.41 | 11:30-12:00 | 0.23x | other | 0.42x | 206 | 54% |
| Gap Up Large | 7 | 0.55 | 10:00-10:30 | 0.00x | — | 0.00x | 178 | 100% |

**ABCAPITAL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 11 | 0.40 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 162 | 27% |
| Gap Down Small | 9 | 0.54 | 10:00-10:30 | 0.00x | other | 0.00x | 164 | 78% |
| Flat | 18 | 0.41 | 10:00-10:30 | 0.00x | 13:30-14:00 | 0.00x | 153 | 56% |
| Gap Up Small | 12 | 0.42 | 10:00-10:30 | 0.00x | other | 0.00x | 207 | 58% |
| Gap Up Large | 8 | 0.49 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 180 | 88% |

**JSWENERGY**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 9 | 0.50 | 10:00-10:30 | 0.36x | 12:00-12:30 | 1.41x | 147 | 67% |
| Gap Up Small | 14 | 0.43 | 10:00-10:30 | 0.66x | 14:30-15:15 | 1.11x | 173 | 43% |
| Gap Up Large | 5 | 0.47 | 10:00-10:30 | 0.27x | 12:30-13:00 | 1.16x | 106 | 60% |

**HCLTECH**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 10 | 0.46 | 10:00-10:30 | 0.07x | 12:30-13:00 | 0.55x | 101 | 50% |
| Gap Down Small | 15 | 0.40 | 11:00-11:30 | 0.19x | other | 0.37x | 170 | 53% |
| Flat | 14 | 0.41 | 10:00-10:30 | 0.23x | 12:30-13:00 | 0.28x | 142 | 50% |
| Gap Up Small | 12 | 0.41 | 10:00-10:30 | 0.29x | 14:30-15:15 | 0.25x | 156 | 33% |

**PNB**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 8 | 0.41 | 10:00-10:30 | 0.20x | 11:30-12:00 | 0.29x | 181 | 62% |
| Flat | 22 | 0.42 | 10:00-10:30 | 0.24x | 14:30-15:15 | 0.40x | 165 | 46% |
| Gap Up Large | 8 | 0.58 | 10:00-10:30 | 0.15x | 13:30-14:00 | 0.38x | 170 | 62% |

**RECLTD**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 8 | 0.60 | 10:30-11:00 | 0.32x | 14:00-14:30 | 0.40x | 170 | 50% |
| Gap Up Large | 14 | 0.53 | 10:00-10:30 | 0.04x | 14:30-15:15 | 0.53x | 166 | 79% |

**BAJAJHFL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.56 | 10:00-10:30 | 0.43x | other | 0.41x | 203 | 57% |
| Gap Up Large | 5 | 0.46 | 10:00-10:30 | 0.43x | — | 0.15x | 174 | 40% |

**MCX**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 12 | 0.40 | 10:30-11:00 | 0.16x | 12:00-12:30 | 0.42x | 142 | 58% |
| Gap Down Small | 10 | 0.42 | 10:00-10:30 | 0.28x | 14:30-15:15 | 0.31x | 151 | 50% |
| Flat | 9 | 0.54 | 10:00-10:30 | 0.15x | 14:30-15:15 | 0.34x | 175 | 56% |
| Gap Up Small | 14 | 0.47 | 10:00-10:30 | 0.14x | 12:30-13:00 | 0.47x | 128 | 57% |

**NTPC**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 7 | 0.42 | 10:30-11:00 | 0.00x | 14:30-15:15 | 0.00x | 180 | 71% |
| Flat | 24 | 0.40 | 10:00-10:30 | 0.38x | 14:30-15:15 | 5.57x | 160 | 62% |
| Gap Up Large | 6 | 0.42 | 10:00-10:30 | 9.17x | 11:30-12:00 | 0.02x | 118 | 17% |

**TATAPOWER**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 10 | 0.44 | 10:00-10:30 | 0.05x | 11:30-12:00 | 0.06x | 94 | 40% |
| Gap Up Small | 10 | 0.43 | 10:00-10:30 | 0.03x | 11:00-11:30 | 0.07x | 129 | 50% |
| Gap Up Large | 8 | 0.42 | 10:30-11:00 | 0.06x | other | 0.07x | 184 | 75% |

**RELIANCE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 10 | 0.44 | 10:00-10:30 | 0.05x | 11:30-12:00 | 0.06x | 94 | 40% |
| Gap Up Small | 10 | 0.43 | 10:00-10:30 | 0.03x | 11:00-11:30 | 0.07x | 129 | 50% |
| Gap Up Large | 8 | 0.42 | 10:30-11:00 | 0.06x | other | 0.07x | 184 | 75% |

**JIOFIN**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 10 | 0.44 | 10:00-10:30 | 0.15x | 11:30-12:00 | 0.23x | 94 | 40% |
| Gap Up Small | 10 | 0.43 | 10:00-10:30 | 0.09x | 11:00-11:30 | 0.22x | 129 | 50% |
| Gap Up Large | 8 | 0.42 | 10:30-11:00 | 0.21x | other | 0.22x | 184 | 75% |

**COALINDIA**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 11 | 0.50 | 10:00-10:30 | 1.67x | other | 1.27x | 214 | 54% |
| Gap Up Small | 13 | 0.52 | 10:00-10:30 | 1.46x | 11:30-12:00 | 1.07x | 134 | 46% |
| Gap Up Large | 3 | 0.43 | 10:00-10:30 | 2.43x | — | 0.55x | 168 | 67% |

**POWERGRID**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.50 | 11:30-12:00 | 0.02x | 14:30-15:15 | 0.72x | 242 | 67% |
| Gap Down Small | 11 | 0.57 | 10:30-11:00 | 0.00x | 10:30-11:00 | 0.00x | 115 | 73% |
| Gap Up Large | 13 | 0.49 | 10:00-10:30 | 0.50x | 14:30-15:15 | 0.13x | 156 | 62% |

**BPCL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 8 | 0.68 | 10:00-10:30 | 0.18x | other | 0.39x | 215 | 75% |
| Flat | 15 | 0.53 | 10:00-10:30 | 0.36x | 14:30-15:15 | 0.23x | 174 | 53% |
| Gap Up Small | 13 | 0.49 | 10:00-10:30 | 0.09x | 11:00-11:30 | 0.39x | 153 | 69% |

**JSWSTEEL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 15 | 0.45 | 10:00-10:30 | 0.31x | 11:30-12:00 | 0.29x | 166 | 53% |
| Flat | 15 | 0.49 | 10:00-10:30 | 0.04x | 12:30-13:00 | 0.54x | 119 | 73% |
| Gap Up Small | 18 | 0.43 | 10:00-10:30 | 0.23x | 11:30-12:00 | 0.38x | 164 | 67% |
| Gap Up Large | 6 | 0.60 | 10:00-10:30 | 0.06x | other | 0.59x | 210 | 67% |

**HINDPETRO**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 11 | 0.44 | 10:00-10:30 | 1.46x | other | 1.11x | 187 | 46% |
| Gap Up Large | 11 | 0.68 | 10:30-11:00 | 3.03x | 14:30-15:15 | -0.05x | 188 | 46% |

**GAIL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.72 | 10:30-11:00 | 0.06x | 10:00-10:30 | 0.09x | 132 | 57% |
| Gap Down Small | 6 | 0.45 | 10:00-10:30 | 0.07x | 14:30-15:15 | 0.08x | 211 | 67% |
| Flat | 22 | 0.43 | 10:00-10:30 | 0.01x | 14:30-15:15 | 0.10x | 145 | 68% |
| Gap Up Small | 18 | 0.41 | 10:00-10:30 | 0.01x | 14:30-15:15 | 0.10x | 166 | 50% |

**ADANIGREEN**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.72 | 10:30-11:00 | 0.06x | 10:00-10:30 | 0.09x | 132 | 57% |
| Gap Down Small | 6 | 0.45 | 10:00-10:30 | 0.07x | 14:30-15:15 | 0.08x | 211 | 67% |
| Flat | 22 | 0.43 | 10:00-10:30 | 0.01x | 14:30-15:15 | 0.10x | 145 | 68% |
| Gap Up Small | 18 | 0.41 | 10:00-10:30 | 0.01x | 14:30-15:15 | 0.10x | 166 | 50% |

**VEDL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 12 | 0.44 | 10:00-10:30 | 0.30x | 14:30-15:15 | 0.41x | 206 | 50% |
| Gap Down Small | 6 | 0.48 | 10:00-10:30 | 0.14x | 10:30-11:00 | 0.30x | 142 | 67% |
| Gap Up Small | 17 | 0.49 | 10:00-10:30 | 0.01x | 14:30-15:15 | 0.52x | 187 | 76% |
| Gap Up Large | 13 | 0.53 | 10:00-10:30 | 0.17x | other | 0.43x | 159 | 54% |

**ADANIENT**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 12 | 0.41 | 10:30-11:00 | 0.02x | 14:00-14:30 | 0.07x | 144 | 50% |
| Gap Up Small | 10 | 0.56 | 10:00-10:30 | 0.01x | 14:30-15:15 | 0.07x | 217 | 80% |

**GPIL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 12 | 0.41 | 10:30-11:00 | 0.02x | 14:00-14:30 | 0.07x | 144 | 50% |
| Gap Up Small | 10 | 0.56 | 10:00-10:30 | 0.01x | 14:30-15:15 | 0.07x | 217 | 80% |

**GRAPHITE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 14 | 0.48 | 10:30-11:00 | 0.09x | 14:30-15:15 | 0.60x | 160 | 79% |
| Gap Up Large | 13 | 0.45 | 10:00-10:30 | 0.19x | 11:30-12:00 | 0.32x | 151 | 46% |

**BHEL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 15 | 0.45 | 10:00-10:30 | 0.10x | 14:30-15:15 | 0.43x | 198 | 73% |
| Gap Up Large | 7 | 0.51 | 10:00-10:30 | 0.33x | 12:30-13:00 | 0.20x | 182 | 43% |

**MTARTECH**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Large | 10 | 0.40 | 11:00-11:30 | 0.34x | other | 0.23x | 123 | 50% |

**RVNL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 14 | 0.55 | 10:00-10:30 | 1.34x | 14:30-15:15 | 1.92x | 170 | 43% |
| Gap Up Large | 9 | 0.57 | 10:00-10:30 | 2.04x | other | 2.70x | 226 | 78% |

**COCHINSHIP**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 17 | 0.40 | 10:00-10:30 | 0.25x | 14:30-15:15 | 0.36x | 168 | 53% |

**LT**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 12 | 0.40 | 10:30-11:00 | 164.81x | 12:30-13:00 | 83.15x | 187 | 33% |
| Gap Up Small | 9 | 0.58 | 10:00-10:30 | 38.52x | 14:30-15:15 | 155.26x | 175 | 56% |
| Gap Up Large | 8 | 0.58 | 10:00-10:30 | 52.51x | 13:30-14:00 | 263.06x | 194 | 62% |

**CUMMINSIND**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 8 | 0.62 | 10:00-10:30 | 0.23x | 14:30-15:15 | 0.38x | 166 | 50% |
| Flat | 22 | 0.49 | 10:00-10:30 | 0.16x | other | 0.49x | 177 | 59% |
| Gap Up Small | 11 | 0.40 | 11:00-11:30 | 0.15x | 13:30-14:00 | 0.35x | 182 | 82% |
| Gap Up Large | 8 | 0.63 | 10:00-10:30 | 0.44x | other | 0.28x | 242 | 62% |

**HAVELLS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.43 | 10:00-10:30 | 0.44x | other | 0.29x | 153 | 50% |
| Gap Down Small | 8 | 0.47 | 10:30-11:00 | 0.47x | 14:30-15:15 | 0.30x | 171 | 50% |
| Flat | 23 | 0.46 | 10:00-10:30 | 0.31x | 14:30-15:15 | 0.30x | 184 | 44% |

**FINCABLES**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.49 | 10:00-10:30 | 0.24x | 11:30-12:00 | 0.43x | 141 | 50% |
| Gap Down Small | 11 | 0.42 | 10:30-11:00 | 0.34x | other | 0.42x | 160 | 54% |
| Flat | 19 | 0.40 | 10:00-10:30 | 0.14x | other | 0.44x | 174 | 53% |
| Gap Up Small | 10 | 0.49 | 10:00-10:30 | 0.28x | 14:30-15:15 | 0.31x | 216 | 50% |
| Gap Up Large | 10 | 0.43 | 11:00-11:30 | 0.14x | other | 0.39x | 166 | 50% |

**TCS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Large | 7 | 0.42 | 10:00-10:30 | 0.34x | 11:30-12:00 | 0.13x | 158 | 29% |

**INFY**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 13 | 0.45 | 10:00-10:30 | 0.29x | other | 0.31x | 142 | 38% |

**NAUKRI**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 11 | 0.47 | 10:00-10:30 | 0.20x | 12:00-12:30 | 0.55x | 152 | 64% |
| Flat | 14 | 0.46 | 10:30-11:00 | 0.41x | 14:30-15:15 | 0.09x | 164 | 29% |
| Gap Up Large | 7 | 0.46 | 10:00-10:30 | 0.65x | other | 0.12x | 182 | 43% |

**BAJAJ-AUTO**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 14 | 0.46 | 10:00-10:30 | 0.04x | other | 0.17x | 196 | 50% |
| Gap Up Large | 6 | 0.55 | 10:00-10:30 | 0.06x | 10:00-10:30 | 0.17x | 136 | 50% |

**TVSMOTOR**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 14 | 0.42 | 10:00-10:30 | 0.37x | other | 0.21x | 162 | 43% |
| Gap Up Small | 11 | 0.47 | 10:00-10:30 | 0.03x | other | 0.54x | 198 | 64% |
| Gap Up Large | 6 | 0.67 | 11:00-11:30 | 0.15x | 14:30-15:15 | 0.49x | 174 | 67% |

**MOTHERSON**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 10 | 0.40 | 10:00-10:30 | 0.37x | 11:00-11:30 | 0.17x | 116 | 30% |
| Flat | 11 | 0.60 | 10:00-10:30 | 0.12x | 12:00-12:30 | 0.38x | 152 | 64% |
| Gap Up Large | 11 | 0.48 | 10:00-10:30 | 0.15x | 11:00-11:30 | 0.48x | 168 | 73% |

**EXIDEIND**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.48 | 10:00-10:30 | 0.27x | other | 0.40x | 170 | 62% |
| Gap Down Small | 10 | 0.41 | 10:00-10:30 | 0.37x | 11:00-11:30 | 0.13x | 136 | 30% |
| Gap Up Small | 12 | 0.57 | 10:00-10:30 | 0.04x | 14:30-15:15 | 0.55x | 214 | 75% |
| Gap Up Large | 6 | 0.65 | 10:30-11:00 | 0.10x | 14:30-15:15 | 0.69x | 198 | 83% |

**TMPV**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 11 | 0.45 | 10:30-11:00 | 0.38x | other | 0.22x | 164 | 46% |
| Gap Up Small | 12 | 0.51 | 10:00-10:30 | 0.07x | 10:30-11:00 | 0.41x | 139 | 75% |
| Gap Up Large | 7 | 0.48 | 10:00-10:30 | 0.21x | 11:00-11:30 | 0.40x | 136 | 57% |

**ITC**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 5 | 0.62 | 10:00-10:30 | 2.71x | 11:00-11:30 | 2.02x | 134 | 20% |
| Flat | 28 | 0.40 | 10:00-10:30 | 1.89x | 14:30-15:15 | 2.81x | 180 | 61% |
| Gap Up Small | 12 | 0.40 | 10:30-11:00 | 2.96x | 11:30-12:00 | 1.73x | 111 | 33% |

**TRENT**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 10 | 0.40 | 10:00-10:30 | 0.28x | 14:30-15:15 | 0.37x | 167 | 50% |
| Gap Up Small | 9 | 0.49 | 10:00-10:30 | 0.04x | other | 0.61x | 184 | 78% |
| Gap Up Large | 6 | 0.45 | 10:00-10:30 | 0.35x | 14:30-15:15 | 0.18x | 176 | 33% |

**TATACONSUM**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 5 | 0.49 | 10:00-10:30 | 0.55x | — | 0.10x | 218 | 40% |
| Gap Up Large | 4 | 0.62 | 10:00-10:30 | 0.59x | 12:00-12:30 | 0.09x | 160 | 25% |

**PIDILITIND**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.41 | 10:30-11:00 | 0.28x | 11:30-12:00 | 0.14x | 176 | 17% |
| Gap Up Small | 12 | 0.64 | 10:00-10:30 | 0.27x | 14:30-15:15 | 0.38x | 195 | 67% |
| Gap Up Large | 5 | 0.56 | 11:00-11:30 | 0.42x | 13:30-14:00 | 0.30x | 155 | 60% |

**VBL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.50 | 11:30-12:00 | 0.00x | 14:30-15:15 | 0.00x | 150 | 62% |
| Gap Up Large | 6 | 0.48 | 10:00-10:30 | 0.07x | — | 0.75x | 209 | 67% |

**UNITDSPR**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 16 | 0.58 | 10:00-10:30 | 0.23x | 14:30-15:15 | 0.33x | 186 | 62% |
| Gap Up Small | 14 | 0.47 | 10:00-10:30 | 0.27x | 11:30-12:00 | 0.36x | 176 | 43% |
| Gap Up Large | 4 | 0.49 | 10:00-10:30 | 0.24x | 12:30-13:00 | 0.29x | 80 | 25% |

**GODREJCP**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 7 | 0.47 | 10:00-10:30 | 0.36x | 12:00-12:30 | 0.24x | 180 | 29% |
| Flat | 25 | 0.48 | 10:00-10:30 | 0.33x | 14:30-15:15 | 0.22x | 179 | 44% |
| Gap Up Small | 9 | 0.66 | 10:00-10:30 | 0.03x | 11:30-12:00 | 0.58x | 130 | 89% |

**ETERNAL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 5 | 0.52 | 10:00-10:30 | 0.16x | 11:00-11:30 | 0.24x | 134 | 60% |
| Gap Up Small | 12 | 0.42 | 10:00-10:30 | 0.25x | 14:30-15:15 | 0.29x | 172 | 50% |
| Gap Up Large | 9 | 0.46 | 10:00-10:30 | 0.45x | 12:00-12:30 | 0.09x | 104 | 44% |

**DMART**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 4 | 0.65 | 10:00-10:30 | 0.00x | 11:30-12:00 | 0.00x | 94 | 75% |
| Gap Down Small | 16 | 0.44 | 10:00-10:30 | 0.13x | other | 0.49x | 199 | 69% |
| Flat | 19 | 0.48 | 10:00-10:30 | 0.39x | 11:00-11:30 | 0.17x | 141 | 32% |
| Gap Up Large | 5 | 0.66 | 10:00-10:30 | 0.21x | — | 0.59x | 130 | 80% |

**DLF**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 11 | 0.42 | 10:00-10:30 | 0.31x | 14:00-14:30 | 0.26x | 142 | 46% |
| Gap Down Small | 8 | 0.41 | 11:00-11:30 | 0.17x | 14:30-15:15 | 0.40x | 184 | 50% |
| Gap Up Large | 9 | 0.65 | 10:30-11:00 | 0.15x | other | 0.38x | 168 | 56% |

**LODHA**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 9 | 0.40 | 10:00-10:30 | 0.37x | 11:00-11:30 | 0.25x | 122 | 56% |
| Gap Up Large | 8 | 0.47 | 10:00-10:30 | 0.23x | other | 0.24x | 162 | 25% |

**DRREDDY**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.55 | 10:00-10:30 | 0.11x | 14:30-15:15 | 0.78x | 230 | 67% |
| Gap Up Large | 8 | 0.56 | 10:30-11:00 | 0.41x | 11:30-12:00 | 0.10x | 104 | 38% |

**CIPLA**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.62 | 10:00-10:30 | 0.00x | 11:00-11:30 | 0.00x | 136 | 71% |
| Gap Down Small | 10 | 0.66 | 10:00-10:30 | 0.31x | 14:30-15:15 | 0.21x | 124 | 40% |
| Flat | 29 | 0.43 | 10:00-10:30 | 0.30x | 14:30-15:15 | 0.28x | 166 | 52% |

**DIVISLAB**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.62 | 10:00-10:30 | 0.00x | 11:00-11:30 | 0.00x | 136 | 71% |
| Gap Down Small | 10 | 0.66 | 10:00-10:30 | 4.84x | 14:30-15:15 | 2.38x | 124 | 40% |
| Flat | 28 | 0.43 | 10:00-10:30 | 6.09x | 14:30-15:15 | 4.70x | 162 | 54% |

**GLENMARK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 11 | 0.47 | 10:00-10:30 | 0.57x | other | 0.18x | 191 | 54% |
| Flat | 21 | 0.44 | 10:30-11:00 | 0.26x | 14:30-15:15 | 0.33x | 142 | 29% |
| Gap Up Large | 8 | 0.48 | 10:00-10:30 | 0.19x | other | 0.41x | 165 | 62% |

**INDIGO**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 14 | 0.44 | 10:00-10:30 | 0.20x | 14:30-15:15 | 0.34x | 140 | 71% |
| Flat | 11 | 0.46 | 10:00-10:30 | 0.03x | 11:00-11:30 | 0.34x | 138 | 54% |
| Gap Up Small | 9 | 0.50 | 10:00-10:30 | 0.20x | 11:00-11:30 | 0.58x | 174 | 78% |
| Gap Up Large | 6 | 0.53 | 10:00-10:30 | 0.13x | other | 0.42x | 218 | 67% |

**TORNTPHARM**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 4 | 0.70 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 195 | 75% |
| Gap Down Small | 10 | 0.53 | 10:00-10:30 | 0.37x | other | 0.26x | 214 | 40% |
| Flat | 27 | 0.48 | 10:00-10:30 | 0.29x | 14:30-15:15 | 0.36x | 180 | 56% |
| Gap Up Small | 13 | 0.41 | 10:00-10:30 | 0.26x | 11:00-11:30 | 0.24x | 154 | 54% |

**AEROFLEX**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 13 | 0.41 | 10:30-11:00 | 0.12x | 14:00-14:30 | 0.40x | 153 | 62% |
| Gap Up Large | 13 | 0.56 | 10:00-10:30 | 0.13x | other | 0.66x | 152 | 77% |

## Disabled Tickers

- **ADANIENSOL**: low EV/WR or OOS degraded
- **ADANIPORTS**: low EV/WR or OOS degraded
- **AMBUJACEM**: low EV/WR or OOS degraded
- **ANANTRAJ**: low EV/WR or OOS degraded
- **APOLLOHOSP**: low EV/WR or OOS degraded
- **ASIANPAINT**: low EV/WR or OOS degraded
- **AUBANK**: low EV/WR or OOS degraded
- **AXISBANK**: low EV/WR or OOS degraded
- **BANKBARODA**: low EV/WR or OOS degraded
- **BEL**: low EV/WR or OOS degraded
- **BHARTIARTL**: low EV/WR or OOS degraded
- **BRITANNIA**: low EV/WR or OOS degraded
- **BSE**: low EV/WR or OOS degraded
- **EICHERMOT**: low EV/WR or OOS degraded
- **FIRSTCRY**: low EV/WR or OOS degraded
- **GRASIM**: low EV/WR or OOS degraded
- **HAL**: low EV/WR or OOS degraded
- **HDFCBANK**: low EV/WR or OOS degraded
- **HDFCLIFE**: low EV/WR or OOS degraded
- **HINDZINC**: low EV/WR or OOS degraded
- **ICICIBANK**: low EV/WR or OOS degraded
- **IDBI**: low EV/WR or OOS degraded
- **INDHOTEL**: low EV/WR or OOS degraded
- **INDUSINDBK**: low EV/WR or OOS degraded
- **IOC**: low EV/WR or OOS degraded
- **KOTAKBANK**: low EV/WR or OOS degraded
- **MAZDOCK**: low EV/WR or OOS degraded
- **NESTLEIND**: low EV/WR or OOS degraded
- **PFC**: low EV/WR or OOS degraded
- **SBILIFE**: low EV/WR or OOS degraded
- **SBIN**: low EV/WR or OOS degraded
- **SHRIRAMFIN**: low EV/WR or OOS degraded
- **SIEMENS**: low EV/WR or OOS degraded
- **SUNPHARMA**: low EV/WR or OOS degraded
- **TATASTEEL**: low EV/WR or OOS degraded
- **TITAN**: low EV/WR or OOS degraded

## Key Parameters

- Minimum sample: 15 trading days with morning low
- Minimum win rate: 50.0%
- Round-trip cost: 0.1%
- OOS train/test split: 70/30
- Monte Carlo iterations: 10,000

Generated: 2026-04-13 12:29