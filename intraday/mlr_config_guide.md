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
| KFINTECH | 5 | 0.530 | 69% | 58 | 0.5% | 0.28x | 0.26x | 10:30-11:00 | 13:30-14:00 | 144min | 15:15 |
| GRAPHITE | 5 | 0.933 | 72% | 58 | 0.8% | 0.13x | 0.50x | 10:30-11:00 | 12:00-12:30 | 136min | 11:30 |
| BHEL | 5 | 0.578 | 71% | 58 | 0.7% | 0.21x | 0.39x | 11:00-11:30 | 14:30-15:15 | 146min | 15:15 |
| GMDCLTD | 5 | 0.551 | 69% | 58 | 0.7% | 0.14x | 0.45x | 11:00-11:30 | 14:30-15:15 | 112min | 15:15 |
| KOTAKBANK | 4 | 0.289 | 53% | 58 | 0.4% | 0.30x | 0.32x | 11:00-11:30 | 14:30-15:15 | 136min | 15:15 |
| INDUSINDBK | 4 | 0.482 | 62% | 58 | 0.7% | 0.23x | 0.38x | 10:30-11:00 | 10:30-11:00 | 158min | 14:30 |
| BANKBARODA | 4 | 0.341 | 53% | 58 | 0.7% | 0.20x | 0.40x | 10:00-10:30 | 14:30-15:15 | 154min | 15:15 |
| PNB | 4 | 0.327 | 53% | 58 | 0.5% | 0.16x | 0.40x | 10:30-11:00 | 14:30-15:15 | 133min | 14:30 |
| FEDERALBNK | 4 | 0.290 | 55% | 58 | 0.6% | 0.23x | 0.36x | 10:30-11:00 | 13:30-14:00 | 151min | 14:30 |
| AUBANK | 4 | 0.362 | 57% | 58 | 0.7% | 0.25x | 0.34x | 11:00-11:30 | 14:30-15:15 | 170min | 15:15 |
| IDBI | 4 | 0.683 | 59% | 58 | 0.7% | 0.23x | 0.40x | 11:00-11:30 | 14:30-15:15 | 108min | 15:15 |
| BAJFINANCE | 4 | 0.320 | 55% | 58 | 0.4% | 0.27x | 0.30x | 10:00-10:30 | 12:00-12:30 | 142min | 15:15 |
| BAJAJFINSV | 4 | 0.240 | 52% | 58 | 0.4% | 0.29x | 0.31x | 10:00-10:30 | 14:30-15:15 | 138min | 15:15 |
| BSE | 4 | 0.421 | 57% | 58 | 0.7% | 0.18x | 0.37x | 10:00-10:30 | 14:30-15:15 | 160min | 15:15 |
| RECLTD | 4 | 0.319 | 55% | 58 | 0.6% | 0.25x | 0.34x | 11:00-11:30 | 14:30-15:15 | 118min | 15:15 |
| ABCAPITAL | 4 | 0.561 | 53% | 58 | 0.7% | 0.26x | 0.31x | 10:30-11:00 | 14:30-15:15 | 156min | 15:15 |
| INDIANB | 4 | 0.470 | 52% | 58 | 1.0% | 0.22x | 0.43x | 10:30-11:00 | 14:30-15:15 | 164min | 14:30 |
| BAJAJHFL | 4 | 0.234 | 50% | 58 | 0.4% | 0.39x | 0.20x | 10:00-10:30 | 14:30-15:15 | 145min | 15:15 |
| MCX | 4 | 0.570 | 52% | 58 | 0.9% | 0.17x | 0.40x | 10:30-11:00 | 12:30-13:00 | 120min | 15:15 |
| JIOFIN | 4 | 0.298 | 53% | 58 | 0.4% | 0.28x | 0.28x | 11:30-12:00 | 11:00-11:30 | 108min | 15:15 |
| TATAPOWER | 4 | 0.247 | 50% | 58 | 0.5% | 0.14x | 0.47x | 11:00-11:30 | 14:30-15:15 | 148min | 15:15 |
| ADANIPOWER | 4 | 0.498 | 67% | 58 | 0.7% | 0.20x | 0.44x | 10:00-10:30 | 14:30-15:15 | 137min | 15:15 |
| COALINDIA | 4 | 0.309 | 55% | 58 | 0.7% | 0.15x | 0.52x | 10:30-11:00 | 14:30-15:15 | 144min | 14:00 |
| BPCL | 4 | 0.436 | 62% | 58 | 0.6% | 0.31x | 0.29x | 10:00-10:30 | 14:30-15:15 | 156min | 14:30 |
| HINDPETRO | 4 | 0.468 | 59% | 58 | 0.5% | 0.31x | 0.27x | 10:30-11:00 | 11:00-11:30 | 139min | 15:15 |
| GAIL | 4 | 0.298 | 57% | 58 | 0.4% | 0.24x | 0.35x | 11:00-11:30 | 14:30-15:15 | 164min | 15:15 |
| ADANIGREEN | 4 | 0.379 | 53% | 58 | 0.5% | 0.29x | 0.28x | 11:00-11:30 | 14:30-15:15 | 134min | 15:15 |
| JSWENERGY | 4 | 0.493 | 66% | 58 | 0.8% | 0.20x | 0.43x | 10:00-10:30 | 12:00-12:30 | 164min | 15:15 |
| ADANIENSOL | 4 | 0.439 | 60% | 58 | 0.9% | 0.24x | 0.39x | 11:00-11:30 | 14:30-15:15 | 146min | 15:15 |
| JINDALSTEL | 4 | 0.413 | 57% | 58 | 0.8% | 0.21x | 0.42x | 11:00-11:30 | 14:30-15:15 | 157min | 15:15 |
| HINDZINC | 4 | 0.364 | 50% | 58 | 0.6% | 0.25x | 0.27x | 10:30-11:00 | 11:30-12:00 | 128min | 15:15 |
| VEDL | 4 | 0.526 | 52% | 58 | 0.8% | 0.18x | 0.40x | 10:30-11:00 | 14:30-15:15 | 164min | 15:15 |
| DATAPATTNS | 4 | 0.735 | 55% | 58 | 0.9% | 0.13x | 0.47x | 10:30-11:00 | 14:30-15:15 | 126min | 15:15 |
| MTARTECH | 4 | 0.868 | 52% | 58 | 0.8% | 0.05x | 0.49x | 11:00-11:30 | 11:30-12:00 | 144min | 15:15 |
| RVNL | 4 | 0.523 | 60% | 58 | 0.7% | 0.26x | 0.26x | 10:30-11:00 | 11:30-12:00 | 144min | 15:15 |
| COCHINSHIP | 4 | 0.440 | 67% | 58 | 0.6% | 0.21x | 0.31x | 10:00-10:30 | 10:30-11:00 | 128min | 15:15 |
| CUMMINSIND | 4 | 0.521 | 52% | 58 | 0.6% | 0.20x | 0.43x | 10:30-11:00 | 14:00-14:30 | 157min | 15:15 |
| HAVELLS | 4 | 0.321 | 55% | 58 | 0.6% | 0.28x | 0.39x | 10:00-10:30 | 14:30-15:15 | 161min | 15:15 |
| FINCABLES | 4 | 0.317 | 55% | 58 | 0.9% | 0.23x | 0.40x | 11:00-11:30 | 14:30-15:15 | 160min | 15:15 |
| INFY | 4 | 0.335 | 59% | 58 | 0.4% | 0.30x | 0.18x | 11:00-11:30 | 14:00-14:30 | 144min | 15:15 |
| HCLTECH | 4 | 0.482 | 66% | 58 | 0.4% | 0.31x | 0.25x | 10:00-10:30 | 11:00-11:30 | 132min | 15:15 |
| TECHM | 4 | 0.283 | 52% | 58 | 0.5% | 0.30x | 0.29x | 10:00-10:30 | 14:30-15:15 | 130min | 15:15 |
| WIPRO | 4 | 0.250 | 52% | 58 | 0.3% | 0.28x | 0.23x | 10:30-11:00 | 11:00-11:30 | 123min | 15:15 |
| NETWEB | 4 | 0.703 | 60% | 58 | 0.8% | 0.17x | 0.39x | 10:30-11:00 | 12:30-13:00 | 110min | 11:30 |
| HAPPSTMNDS | 4 | 0.453 | 50% | 58 | 0.7% | 0.34x | 0.26x | 11:00-11:30 | 14:00-14:30 | 130min | 15:15 |
| NAUKRI | 4 | 0.444 | 50% | 58 | 0.6% | 0.29x | 0.27x | 10:30-11:00 | 11:30-12:00 | 130min | 15:15 |
| M&M | 4 | 0.315 | 50% | 58 | 0.4% | 0.19x | 0.38x | 11:00-11:30 | 14:30-15:15 | 142min | 15:15 |
| BAJAJ-AUTO | 4 | 0.352 | 52% | 58 | 0.6% | 0.27x | 0.39x | 10:30-11:00 | 14:30-15:15 | 154min | 15:15 |
| EXIDEIND | 4 | 0.337 | 60% | 58 | 0.4% | 0.25x | 0.31x | 11:00-11:30 | 14:30-15:15 | 150min | 15:15 |
| TRENT | 4 | 0.435 | 52% | 58 | 0.7% | 0.27x | 0.34x | 10:00-10:30 | 14:30-15:15 | 147min | 14:30 |
| TATACONSUM | 4 | 0.235 | 50% | 58 | 0.4% | 0.25x | 0.34x | 11:00-11:30 | 13:30-14:00 | 139min | 15:15 |
| ASIANPAINT | 4 | 0.281 | 53% | 58 | 0.4% | 0.21x | 0.35x | 10:30-11:00 | 14:30-15:15 | 138min | 15:15 |
| VBL | 4 | 0.367 | 59% | 58 | 0.5% | 0.26x | 0.34x | 10:30-11:00 | 14:30-15:15 | 165min | 15:15 |
| UNITDSPR | 4 | 0.300 | 59% | 58 | 0.6% | 0.27x | 0.37x | 11:00-11:30 | 14:30-15:15 | 142min | 15:15 |
| GODREJCP | 4 | 0.429 | 66% | 58 | 0.6% | 0.32x | 0.28x | 10:30-11:00 | 14:30-15:15 | 164min | 14:30 |
| ETERNAL | 4 | 0.477 | 67% | 58 | 0.7% | 0.26x | 0.29x | 10:00-10:30 | 14:30-15:15 | 130min | 15:15 |
| INDHOTEL | 4 | 0.336 | 53% | 58 | 0.6% | 0.32x | 0.28x | 10:30-11:00 | 14:30-15:15 | 176min | 15:15 |
| DMART | 4 | 0.363 | 52% | 58 | 0.5% | 0.25x | 0.35x | 10:30-11:00 | 11:30-12:00 | 146min | 14:30 |
| DLF | 4 | 0.456 | 55% | 58 | 0.6% | 0.34x | 0.26x | 11:00-11:30 | 14:30-15:15 | 147min | 15:15 |
| ANANTRAJ | 4 | 0.679 | 53% | 58 | 0.7% | 0.24x | 0.31x | 10:30-11:00 | 11:00-11:30 | 135min | 15:15 |
| LODHA | 4 | 0.456 | 64% | 58 | 0.6% | 0.30x | 0.23x | 10:00-10:30 | 14:30-15:15 | 144min | 15:15 |
| DRREDDY | 4 | 0.247 | 50% | 58 | 0.5% | 0.26x | 0.32x | 10:00-10:30 | 14:30-15:15 | 166min | 14:30 |
| CIPLA | 4 | 0.251 | 53% | 58 | 0.4% | 0.32x | 0.29x | 10:00-10:30 | 14:30-15:15 | 166min | 15:15 |
| GLENMARK | 4 | 0.412 | 50% | 58 | 0.7% | 0.28x | 0.41x | 10:30-11:00 | 14:30-15:15 | 162min | 15:15 |
| INDIGO | 4 | 0.303 | 52% | 58 | 0.6% | 0.23x | 0.33x | 10:00-10:30 | 14:30-15:15 | 152min | 14:00 |
| AEROFLEX | 4 | 0.699 | 50% | 58 | 0.7% | 0.14x | 0.40x | 11:00-11:30 | 10:00-10:30 | 122min | 15:15 |
| GUJALKALI | 4 | 0.438 | 69% | 58 | 0.9% | 0.25x | 0.40x | 10:30-11:00 | 14:30-15:15 | 156min | 14:30 |

### Open-Type Profiles

Predictability-scored profiles per opening type (drop -> recovery -> timing):

**KFINTECH**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.62 | 11:00-11:30 | 0.34x | 12:30-13:00 | 0.25x | 148 | 33% |
| Gap Down Small | 15 | 0.41 | 10:00-10:30 | 0.25x | 14:30-15:15 | 0.29x | 160 | 40% |
| Gap Up Large | 5 | 0.48 | 10:00-10:30 | 0.23x | 10:30-11:00 | 0.27x | 89 | 40% |

**GRAPHITE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 20 | 0.43 | 10:30-11:00 | 0.07x | 14:30-15:15 | 0.62x | 138 | 70% |
| Gap Up Large | 9 | 0.57 | 10:00-10:30 | 0.26x | 11:30-12:00 | 0.29x | 153 | 56% |

**BHEL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 5 | 0.48 | 10:00-10:30 | 0.26x | 10:30-11:00 | 0.50x | 109 | 60% |
| Gap Up Small | 20 | 0.40 | 10:00-10:30 | 0.11x | 11:30-12:00 | 0.51x | 157 | 65% |
| Gap Up Large | 3 | 0.71 | 10:00-10:30 | 0.61x | other | -0.04x | 232 | 0% |

**KOTAKBANK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 9 | 0.49 | 11:00-11:30 | 0.29x | 13:00-13:30 | 0.31x | 118 | 44% |
| Gap Up Large | 3 | 0.71 | 11:00-11:30 | 0.34x | — | 0.25x | 146 | 0% |

**INDUSINDBK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.57 | 10:00-10:30 | 0.47x | other | 0.17x | 179 | 17% |
| Gap Down Small | 8 | 0.51 | 10:00-10:30 | 0.08x | 11:00-11:30 | 0.46x | 113 | 62% |
| Flat | 32 | 0.48 | 10:00-10:30 | 0.19x | 14:30-15:15 | 0.46x | 187 | 59% |
| Gap Up Large | 3 | 0.70 | 10:00-10:30 | 0.53x | 11:00-11:30 | 0.09x | 86 | 33% |

**PNB**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.46 | 10:00-10:30 | 0.04x | 11:00-11:30 | 0.58x | 109 | 67% |
| Gap Down Small | 8 | 0.47 | 10:00-10:30 | 0.12x | 11:30-12:00 | 0.53x | 128 | 50% |
| Gap Up Large | 3 | 0.58 | 10:00-10:30 | 0.50x | — | -0.15x | 194 | 33% |

**FEDERALBNK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 11 | 0.54 | 10:00-10:30 | 0.28x | 13:00-13:30 | 0.38x | 147 | 46% |
| Flat | 25 | 0.41 | 10:00-10:30 | 0.16x | 11:30-12:00 | 0.45x | 148 | 48% |

**AUBANK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.42 | 10:30-11:00 | 0.18x | other | 0.27x | 166 | 33% |
| Gap Down Small | 10 | 0.48 | 10:00-10:30 | 0.08x | other | 0.51x | 225 | 80% |
| Gap Up Small | 12 | 0.47 | 10:30-11:00 | 0.35x | 14:30-15:15 | 0.30x | 194 | 42% |

**IDBI**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 9 | 0.41 | 11:00-11:30 | 0.40x | 11:30-12:00 | 0.23x | 81 | 33% |
| Flat | 20 | 0.40 | 10:00-10:30 | 0.12x | 12:00-12:30 | 0.48x | 115 | 60% |

**BAJFINANCE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.48 | 10:00-10:30 | 0.10x | 13:30-14:00 | 0.48x | 166 | 57% |
| Gap Up Small | 12 | 0.42 | 10:00-10:30 | 0.45x | 14:30-15:15 | 0.07x | 155 | 33% |

**BAJAJFINSV**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.43 | 10:30-11:00 | 0.73x | — | 0.10x | 178 | 33% |
| Gap Down Small | 18 | 0.45 | 10:00-10:30 | 0.26x | 14:30-15:15 | 0.29x | 144 | 39% |
| Gap Up Small | 11 | 0.45 | 10:00-10:30 | 0.25x | other | 0.51x | 172 | 54% |

**BSE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 14 | 0.41 | 10:00-10:30 | 0.17x | 14:30-15:15 | 0.27x | 158 | 43% |
| Gap Up Large | 9 | 0.66 | 10:00-10:30 | 0.01x | 14:30-15:15 | 0.59x | 208 | 78% |

**RECLTD**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 18 | 0.43 | 10:00-10:30 | 0.28x | 14:30-15:15 | 0.34x | 124 | 44% |
| Gap Up Large | 4 | 0.75 | 10:00-10:30 | 0.26x | — | 0.33x | 156 | 50% |

**ABCAPITAL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 10 | 0.43 | 10:00-10:30 | 0.34x | other | 0.35x | 188 | 60% |
| Gap Up Large | 6 | 0.75 | 11:30-12:00 | 0.42x | other | 0.14x | 154 | 67% |

**INDIANB**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.48 | 10:30-11:00 | 0.55x | 14:30-15:15 | 0.02x | 166 | 17% |
| Gap Down Small | 10 | 0.48 | 10:00-10:30 | 0.25x | 11:30-12:00 | 0.35x | 148 | 70% |
| Flat | 25 | 0.40 | 10:00-10:30 | 0.16x | 14:30-15:15 | 0.51x | 150 | 56% |
| Gap Up Small | 14 | 0.42 | 10:00-10:30 | 0.23x | 14:30-15:15 | 0.46x | 198 | 50% |
| Gap Up Large | 3 | 0.63 | 10:00-10:30 | 0.00x | — | 0.00x | 180 | 100% |

**BAJAJHFL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 4 | 0.60 | 10:00-10:30 | 0.62x | other | 0.40x | 218 | 50% |

**MCX**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 16 | 0.42 | 10:00-10:30 | 0.10x | 12:30-13:00 | 0.53x | 111 | 62% |

**JIOFIN**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 10 | 0.46 | 10:00-10:30 | 0.20x | 11:30-12:00 | 0.35x | 84 | 40% |
| Gap Up Large | 4 | 0.41 | 10:30-11:00 | 0.45x | — | 0.20x | 155 | 50% |

**TATAPOWER**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 5 | 0.58 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 143 | 80% |
| Gap Up Small | 13 | 0.44 | 10:00-10:30 | 0.13x | other | 0.46x | 190 | 69% |
| Gap Up Large | 3 | 0.43 | 10:30-11:00 | 0.06x | — | 0.52x | 38 | 67% |

**ADANIPOWER**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 4 | 0.41 | 11:30-12:00 | 0.00x | — | 0.00x | 134 | 100% |
| Gap Down Small | 13 | 0.46 | 10:00-10:30 | 0.23x | 11:30-12:00 | 0.61x | 122 | 46% |
| Flat | 18 | 0.43 | 10:00-10:30 | 0.22x | 14:30-15:15 | 0.32x | 180 | 39% |

**COALINDIA**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 10 | 0.44 | 10:00-10:30 | 0.24x | other | 0.52x | 218 | 60% |
| Gap Up Large | 3 | 0.57 | 10:00-10:30 | 0.00x | — | 0.00x | 140 | 67% |

**BPCL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 7 | 0.44 | 10:00-10:30 | 0.13x | other | 0.48x | 194 | 57% |
| Flat | 23 | 0.43 | 10:00-10:30 | 0.39x | 14:30-15:15 | 0.31x | 182 | 52% |
| Gap Up Large | 5 | 0.48 | 11:00-11:30 | 0.61x | 10:00-10:30 | -0.01x | 146 | 40% |

**HINDPETRO**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Large | 5 | 0.45 | 10:30-11:00 | 0.60x | 14:30-15:15 | -0.17x | 197 | 20% |

**GAIL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 9 | 0.65 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 214 | 78% |

**ADANIGREEN**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Large | 6 | 0.55 | 10:30-11:00 | 0.26x | 11:30-12:00 | 0.20x | 111 | 50% |

**JSWENERGY**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.48 | 10:00-10:30 | 0.22x | 11:00-11:30 | 0.36x | 141 | 67% |
| Gap Down Small | 7 | 0.40 | 10:00-10:30 | 0.04x | 14:30-15:15 | 0.44x | 216 | 57% |
| Gap Up Small | 16 | 0.56 | 10:00-10:30 | 0.17x | 14:30-15:15 | 0.47x | 186 | 56% |

**ADANIENSOL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.46 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 133 | 86% |
| Flat | 19 | 0.40 | 10:00-10:30 | 0.27x | 14:30-15:15 | 0.40x | 128 | 47% |
| Gap Up Large | 7 | 0.46 | 10:30-11:00 | 0.38x | 12:00-12:30 | 0.28x | 156 | 57% |

**JINDALSTEL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.53 | 10:00-10:30 | 0.41x | 14:00-14:30 | 0.41x | 204 | 50% |
| Flat | 23 | 0.50 | 10:00-10:30 | 0.30x | 12:30-13:00 | 0.38x | 144 | 52% |
| Gap Up Small | 19 | 0.40 | 10:00-10:30 | 0.09x | 10:30-11:00 | 0.42x | 150 | 47% |
| Gap Up Large | 4 | 0.55 | 10:00-10:30 | 0.11x | — | 0.51x | 160 | 50% |

**HINDZINC**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 6 | 0.48 | 10:30-11:00 | 0.53x | 13:30-14:00 | -0.05x | 84 | 33% |

**VEDL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.56 | 11:00-11:30 | 0.27x | 14:00-14:30 | 0.46x | 197 | 50% |
| Gap Down Small | 7 | 0.48 | 10:00-10:30 | 0.35x | 14:30-15:15 | 0.14x | 153 | 57% |
| Flat | 12 | 0.40 | 10:00-10:30 | 0.21x | 14:30-15:15 | 0.34x | 141 | 50% |
| Gap Up Small | 20 | 0.48 | 10:00-10:30 | 0.09x | 14:30-15:15 | 0.43x | 168 | 60% |
| Gap Up Large | 11 | 0.54 | 10:00-10:30 | 0.15x | other | 0.52x | 168 | 54% |

**DATAPATTNS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 12 | 0.40 | 10:00-10:30 | 0.06x | 14:30-15:15 | 0.45x | 138 | 42% |
| Gap Up Large | 6 | 0.43 | 11:00-11:30 | 0.37x | other | 0.30x | 152 | 67% |

**MTARTECH**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 11 | 0.44 | 10:00-10:30 | 0.10x | other | 0.44x | 198 | 73% |
| Gap Up Large | 8 | 0.44 | 10:00-10:30 | 0.16x | other | 0.47x | 180 | 75% |

**RVNL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 11 | 0.41 | 10:30-11:00 | 0.17x | 14:30-15:15 | 0.34x | 184 | 46% |
| Gap Up Large | 6 | 0.69 | 11:00-11:30 | 0.66x | other | 0.13x | 236 | 50% |

**CUMMINSIND**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.51 | 10:30-11:00 | 0.33x | 11:30-12:00 | 0.50x | 124 | 38% |
| Gap Down Small | 6 | 0.66 | 10:00-10:30 | 0.14x | 11:00-11:30 | 0.39x | 162 | 50% |
| Gap Up Large | 3 | 0.63 | 10:00-10:30 | 0.72x | — | 0.00x | 256 | 33% |

**HAVELLS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 5 | 0.70 | 10:30-11:00 | 0.39x | 14:30-15:15 | 0.47x | 160 | 60% |
| Flat | 32 | 0.49 | 10:00-10:30 | 0.31x | 14:30-15:15 | 0.39x | 188 | 41% |

**FINCABLES**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 4 | 0.49 | 10:00-10:30 | 0.12x | — | 0.64x | 178 | 50% |
| Flat | 22 | 0.47 | 10:00-10:30 | 0.14x | other | 0.48x | 178 | 59% |
| Gap Up Large | 6 | 0.47 | 11:00-11:30 | 0.15x | 11:30-12:00 | 0.45x | 138 | 50% |

**INFY**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 14 | 0.46 | 10:00-10:30 | 0.45x | other | 0.18x | 147 | 36% |
| Gap Up Small | 11 | 0.43 | 11:00-11:30 | 0.21x | 14:30-15:15 | 0.20x | 172 | 54% |
| Gap Up Large | 7 | 0.43 | 10:00-10:30 | 0.35x | 11:00-11:30 | 0.03x | 110 | 14% |

**HCLTECH**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 14 | 0.41 | 10:00-10:30 | 0.28x | other | 0.30x | 164 | 36% |
| Gap Up Large | 6 | 0.47 | 10:00-10:30 | 0.57x | 11:00-11:30 | -0.01x | 54 | 33% |

**TECHM**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.45 | 10:00-10:30 | 0.36x | 14:30-15:15 | 0.27x | 142 | 62% |
| Gap Down Small | 12 | 0.41 | 10:00-10:30 | 0.30x | 12:00-12:30 | 0.23x | 166 | 42% |
| Flat | 17 | 0.54 | 10:00-10:30 | 0.37x | 12:30-13:00 | 0.33x | 144 | 65% |

**WIPRO**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 6 | 0.50 | 10:30-11:00 | 0.17x | 13:30-14:00 | 0.47x | 141 | 67% |
| Flat | 24 | 0.43 | 10:00-10:30 | 0.28x | 14:30-15:15 | 0.21x | 134 | 50% |

**NETWEB**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Large | 10 | 0.42 | 10:00-10:30 | 0.52x | 12:00-12:30 | 0.24x | 101 | 50% |

**NAUKRI**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.44 | 11:00-11:30 | 0.18x | 14:00-14:30 | 0.42x | 106 | 50% |
| Gap Up Small | 10 | 0.41 | 10:00-10:30 | 0.32x | 14:30-15:15 | 0.24x | 172 | 50% |
| Gap Up Large | 3 | 0.43 | 10:00-10:30 | 0.97x | — | -0.04x | 118 | 33% |

**M&M**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 17 | 0.44 | 10:00-10:30 | 0.27x | other | 0.30x | 164 | 41% |

**BAJAJ-AUTO**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 11 | 0.42 | 10:00-10:30 | 0.14x | 14:30-15:15 | 0.41x | 192 | 73% |
| Gap Up Large | 3 | 0.51 | 10:00-10:30 | 0.44x | — | 0.25x | 145 | 67% |

**EXIDEIND**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.43 | 10:00-10:30 | 0.32x | other | 0.30x | 166 | 50% |
| Gap Down Small | 8 | 0.50 | 10:00-10:30 | 0.33x | 11:00-11:30 | 0.33x | 124 | 38% |
| Gap Up Small | 9 | 0.57 | 10:00-10:30 | 0.04x | 14:30-15:15 | 0.52x | 212 | 78% |
| Gap Up Large | 3 | 0.57 | 10:00-10:30 | 0.23x | — | 0.67x | 208 | 67% |

**TRENT**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.58 | 11:00-11:30 | 0.35x | 14:30-15:15 | 0.29x | 175 | 43% |
| Gap Up Small | 10 | 0.48 | 10:00-10:30 | 0.22x | 11:30-12:00 | 0.47x | 158 | 70% |

**TATACONSUM**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.43 | — | 0.68x | — | 0.04x | 254 | 33% |

**ASIANPAINT**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.56 | 10:00-10:30 | 0.26x | 11:00-11:30 | 0.41x | 168 | 67% |
| Gap Down Small | 12 | 0.41 | 11:00-11:30 | 0.20x | 14:30-15:15 | 0.37x | 147 | 33% |

**VBL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.49 | 11:30-12:00 | 0.00x | 14:30-15:15 | 0.00x | 144 | 50% |
| Gap Up Small | 13 | 0.41 | 11:00-11:30 | 0.23x | 14:30-15:15 | 0.35x | 194 | 62% |

**UNITDSPR**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.50 | 10:00-10:30 | 0.36x | 10:30-11:00 | 0.35x | 126 | 50% |
| Flat | 20 | 0.44 | 10:00-10:30 | 0.27x | other | 0.28x | 146 | 55% |
| Gap Up Small | 14 | 0.47 | 10:00-10:30 | 0.27x | 11:30-12:00 | 0.36x | 176 | 50% |

**GODREJCP**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 10 | 0.65 | 10:00-10:30 | 0.27x | 14:30-15:15 | 0.46x | 204 | 60% |
| Flat | 29 | 0.50 | 10:00-10:30 | 0.28x | 14:30-15:15 | 0.30x | 180 | 48% |

**ETERNAL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 4 | 0.41 | 11:00-11:30 | 0.00x | 11:00-11:30 | 0.00x | 131 | 75% |
| Gap Up Small | 13 | 0.44 | 10:00-10:30 | 0.20x | 14:30-15:15 | 0.34x | 178 | 54% |
| Gap Up Large | 4 | 0.41 | 11:00-11:30 | 0.90x | — | -0.33x | 92 | 25% |

**INDHOTEL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.42 | 10:00-10:30 | 0.35x | — | 0.16x | 128 | 33% |
| Gap Down Small | 14 | 0.50 | 10:00-10:30 | 0.24x | other | 0.46x | 186 | 57% |
| Gap Up Small | 13 | 0.45 | 10:00-10:30 | 0.38x | other | 0.17x | 172 | 38% |

**DMART**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 12 | 0.45 | 10:00-10:30 | 0.03x | other | 0.66x | 181 | 67% |
| Gap Up Small | 17 | 0.40 | 10:00-10:30 | 0.23x | 10:30-11:00 | 0.27x | 132 | 35% |

**DLF**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.45 | 10:00-10:30 | 0.22x | 14:00-14:30 | 0.25x | 149 | 50% |
| Flat | 27 | 0.51 | 10:00-10:30 | 0.41x | 14:30-15:15 | 0.24x | 160 | 41% |
| Gap Up Large | 5 | 0.62 | 10:30-11:00 | 0.14x | other | 0.39x | 197 | 60% |

**ANANTRAJ**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 9 | 0.62 | 10:30-11:00 | 0.30x | 11:30-12:00 | 0.28x | 160 | 33% |
| Gap Up Large | 6 | 0.48 | 10:00-10:30 | 0.40x | 14:30-15:15 | 0.10x | 156 | 0% |

**LODHA**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 5 | 0.48 | 10:30-11:00 | 0.34x | 14:30-15:15 | 0.22x | 130 | 60% |
| Gap Down Small | 16 | 0.40 | 10:00-10:30 | 0.22x | 14:30-15:15 | 0.34x | 150 | 50% |
| Gap Up Large | 5 | 0.53 | 10:00-10:30 | 0.32x | 11:00-11:30 | 0.14x | 146 | 20% |

**DRREDDY**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 4 | 0.77 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 244 | 75% |
| Gap Up Large | 5 | 0.55 | 10:30-11:00 | 0.19x | 11:30-12:00 | 0.28x | 112 | 60% |

**CIPLA**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.55 | 10:00-10:30 | 0.24x | 14:30-15:15 | 0.77x | 156 | 71% |
| Gap Down Small | 5 | 0.79 | 10:00-10:30 | 0.26x | 11:30-12:00 | 0.24x | 135 | 40% |
| Flat | 37 | 0.40 | 10:00-10:30 | 0.31x | 14:30-15:15 | 0.26x | 170 | 49% |
| Gap Up Small | 8 | 0.48 | 10:00-10:30 | 0.35x | 14:30-15:15 | 0.24x | 196 | 50% |

**GLENMARK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 6 | 0.58 | 10:00-10:30 | 0.62x | other | 0.14x | 214 | 33% |
| Gap Up Small | 17 | 0.40 | 10:00-10:30 | 0.25x | 11:30-12:00 | 0.44x | 154 | 47% |
| Gap Up Large | 4 | 0.65 | 10:00-10:30 | 0.09x | other | 0.42x | 182 | 75% |

**INDIGO**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 16 | 0.44 | 10:00-10:30 | 0.21x | 14:30-15:15 | 0.33x | 130 | 69% |
| Gap Up Small | 9 | 0.47 | 10:00-10:30 | 0.33x | 14:30-15:15 | 0.45x | 190 | 67% |

**AEROFLEX**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 8 | 0.42 | 10:00-10:30 | 0.19x | 14:30-15:15 | 0.36x | 160 | 62% |
| Gap Up Large | 9 | 0.57 | 10:00-10:30 | 0.21x | other | 0.52x | 161 | 67% |

**GUJALKALI**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 11 | 0.52 | 10:00-10:30 | 0.00x | other | 0.00x | 155 | 73% |
| Gap Down Small | 8 | 0.47 | 10:00-10:30 | 0.04x | other | 0.54x | 217 | 75% |
| Gap Up Large | 3 | 0.57 | 11:30-12:00 | 0.18x | — | 0.97x | 184 | 67% |

## Disabled Tickers

- **ABB**: low EV/WR or OOS degraded
- **ADANIENT**: low EV/WR or OOS degraded
- **ADANIPORTS**: low EV/WR or OOS degraded
- **AETHER**: low EV/WR or OOS degraded
- **AMBUJACEM**: low EV/WR or OOS degraded
- **APOLLOHOSP**: low EV/WR or OOS degraded
- **AXISBANK**: low EV/WR or OOS degraded
- **BEL**: low EV/WR or OOS degraded
- **BHARTIARTL**: low EV/WR or OOS degraded
- **BRITANNIA**: low EV/WR or OOS degraded
- **CAMS**: low EV/WR or OOS degraded
- **CANBK**: low EV/WR or OOS degraded
- **CGPOWER**: low EV/WR or OOS degraded
- **DIVISLAB**: low EV/WR or OOS degraded
- **EICHERMOT**: low EV/WR or OOS degraded
- **FIRSTCRY**: low EV/WR or OOS degraded
- **GPIL**: low EV/WR or OOS degraded
- **GRASIM**: low EV/WR or OOS degraded
- **HAL**: low EV/WR or OOS degraded
- **HDFCBANK**: low EV/WR or OOS degraded
- **HDFCLIFE**: low EV/WR or OOS degraded
- **HINDALCO**: low EV/WR or OOS degraded
- **HINDUNILVR**: low EV/WR or OOS degraded
- **ICICIBANK**: low EV/WR or OOS degraded
- **IDFCFIRSTB**: low EV/WR or OOS degraded
- **IOC**: low EV/WR or OOS degraded
- **ITC**: low EV/WR or OOS degraded
- **JSWSTEEL**: low EV/WR or OOS degraded
- **LICI**: low EV/WR or OOS degraded
- **LT**: low EV/WR or OOS degraded
- **MAZDOCK**: low EV/WR or OOS degraded
- **MOTHERSON**: low EV/WR or OOS degraded
- **NBCC**: low EV/WR or OOS degraded
- **NESTLEIND**: low EV/WR or OOS degraded
- **NTPC**: low EV/WR or OOS degraded
- **ONGC**: low EV/WR or OOS degraded
- **PFC**: low EV/WR or OOS degraded
- **PIDILITIND**: low EV/WR or OOS degraded
- **POWERGRID**: low EV/WR or OOS degraded
- **RELIANCE**: low EV/WR or OOS degraded
- **SAILIFE**: low EV/WR or OOS degraded
- **SBILIFE**: low EV/WR or OOS degraded
- **SBIN**: low EV/WR or OOS degraded
- **SCI**: low EV/WR or OOS degraded
- **SHRIRAMFIN**: low EV/WR or OOS degraded
- **SIEMENS**: low EV/WR or OOS degraded
- **SUNPHARMA**: low EV/WR or OOS degraded
- **TATASTEEL**: low EV/WR or OOS degraded
- **TCS**: low EV/WR or OOS degraded
- **TITAN**: low EV/WR or OOS degraded
- **TMPV**: low EV/WR or OOS degraded
- **TORNTPHARM**: low EV/WR or OOS degraded
- **TVSMOTOR**: low EV/WR or OOS degraded
- **UNIONBANK**: low EV/WR or OOS degraded
- **ZYDUSLIFE**: low EV/WR or OOS degraded

## Key Parameters

- Minimum sample: 15 trading days with morning low
- Minimum win rate: 50.0%
- Round-trip cost: 0.1%
- OOS train/test split: 70/30
- Monte Carlo iterations: 10,000

Generated: 2026-03-20 12:30