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
| PNB | 5 | 0.583 | 65% | 63 | 0.5% | 75.16x | -73.91x | 10:00-10:30 | 13:30-14:00 | 146min | 15:15 |
| BANKBARODA | 5 | 0.574 | 69% | 80 | 0.7% | 97.12x | -96.36x | 10:00-10:30 | 10:00-10:30 | 157min | 15:15 |
| IDBI | 5 | 0.884 | 65% | 92 | -5.3% | 297.60x | -288.83x | 11:00-11:30 | 14:00-14:30 | 146min | 14:30 |
| KFINTECH | 5 | 0.873 | 81% | 75 | -0.8% | 601.99x | -590.05x | 10:00-10:30 | 12:30-13:00 | 181min | 15:15 |
| ADANIPOWER | 5 | 0.764 | 71% | 75 | 0.7% | 82.34x | -81.18x | 10:00-10:30 | 13:30-14:00 | 143min | 15:15 |
| BPCL | 5 | 0.717 | 76% | 63 | 0.7% | 14.87x | -13.44x | 10:00-10:30 | 10:00-10:30 | 168min | 14:30 |
| TATASTEEL | 5 | 0.800 | 70% | 63 | -3.3% | 197.38x | -193.19x | 10:00-10:30 | 13:30-14:00 | 190min | 15:15 |
| JSWENERGY | 5 | 0.546 | 81% | 63 | 0.5% | 87.75x | -85.80x | 10:00-10:30 | 13:30-14:00 | 127min | 15:15 |
| GPIL | 5 | 0.827 | 74% | 92 | 0.8% | 354.32x | -353.77x | 10:00-10:30 | 13:30-14:00 | 145min | 15:15 |
| HAL | 5 | 2.214 | 80% | 75 | -72.3% | 8.96x | -1.01x | 10:00-10:30 | 11:00-11:30 | 145min | 15:15 |
| SIEMENS | 5 | 0.738 | 71% | 63 | 0.6% | 4166.80x | -3975.08x | 10:00-10:30 | 10:00-10:30 | 155min | 15:15 |
| NBCC | 5 | 0.697 | 68% | 92 | -6.5% | 1209.33x | -1197.45x | 10:00-10:30 | 10:00-10:30 | 156min | 15:15 |
| FINCABLES | 5 | 1.163 | 83% | 75 | 0.9% | 1007.99x | -968.29x | 10:00-10:30 | 10:00-10:30 | 234min | 15:15 |
| INDHOTEL | 5 | 0.673 | 71% | 63 | 0.6% | 1451.65x | -1447.18x | 10:00-10:30 | 10:00-10:30 | 168min | 14:30 |
| VBL | 5 | 0.808 | 72% | 74 | -0.5% | 615.64x | -609.47x | 10:00-10:30 | 10:00-10:30 | 214min | 15:15 |
| AEROFLEX | 5 | 1.130 | 67% | 92 | 0.9% | 144.83x | -143.95x | 10:00-10:30 | 13:00-13:30 | 120min | 15:15 |
| SBIN | 4 | 0.538 | 62% | 80 | 0.6% | 13.56x | -12.23x | 11:00-11:30 | 10:00-10:30 | 154min | 15:15 |
| AXISBANK | 4 | 0.346 | 59% | 80 | 0.6% | 2.68x | -1.91x | 11:00-11:30 | 12:30-13:00 | 173min | 15:15 |
| CANBK | 4 | 0.586 | 64% | 63 | 0.5% | 92.47x | -84.84x | 10:30-11:00 | 10:00-10:30 | 152min | 15:15 |
| UNIONBANK | 4 | 0.556 | 56% | 80 | 0.9% | 0.40x | 0.91x | 10:30-11:00 | 14:00-14:30 | 160min | 14:30 |
| FEDERALBNK | 4 | 0.298 | 54% | 80 | 0.7% | 1.07x | 0.32x | 11:00-11:30 | 13:30-14:00 | 150min | 15:15 |
| IDFCFIRSTB | 4 | 0.551 | 62% | 80 | -6.2% | 429.73x | -427.89x | 10:00-10:30 | 10:00-10:30 | 142min | 15:15 |
| ABCAPITAL | 4 | 1.647 | 64% | 92 | 97.4% | 0.76x | 38.06x | 10:00-10:30 | 14:30-15:15 | 344min | 15:15 |
| BAJAJHFL | 4 | 0.384 | 61% | 80 | 0.5% | 336.27x | -335.59x | 10:00-10:30 | 14:00-14:30 | 160min | 15:15 |
| BSE | 4 | 0.372 | 54% | 92 | 19.4% | 0.00x | 0.00x | 10:00-10:30 | 10:00-10:30 | 156min | 15:15 |
| TATAPOWER | 4 | 0.280 | 51% | 92 | 0.5% | 0.30x | 0.85x | 11:00-11:30 | 14:30-15:15 | 153min | 15:15 |
| GAIL | 4 | 0.461 | 69% | 80 | 0.5% | 1224.38x | -1223.82x | 10:00-10:30 | 13:00-13:30 | 163min | 14:30 |
| ADANIENSOL | 4 | 0.395 | 50% | 80 | 2.5% | 0.00x | 0.00x | 10:00-10:30 | 10:00-10:30 | 189min | 15:15 |
| HINDZINC | 4 | 0.749 | 56% | 63 | -0.5% | 4.97x | -4.10x | 10:00-10:30 | 11:00-11:30 | 132min | 15:15 |
| BHEL | 4 | 0.632 | 54% | 92 | 151.3% | 0.42x | 528.77x | 10:30-11:00 | 14:30-15:15 | 200min | 15:15 |
| DATAPATTNS | 4 | 0.766 | 55% | 92 | 1.1% | 0.44x | 0.74x | 10:30-11:00 | 14:30-15:15 | 181min | 15:15 |
| SCI | 4 | 0.810 | 57% | 75 | 0.7% | 14.25x | -13.28x | 10:00-10:30 | 10:00-10:30 | 139min | 15:15 |
| CUMMINSIND | 4 | 1.420 | 54% | 92 | 256.6% | 29.62x | 15.08x | 10:30-11:00 | 11:30-12:00 | 297min | 15:15 |
| HAVELLS | 4 | 0.293 | 51% | 92 | 0.6% | 0.00x | 0.00x | 10:00-10:30 | 10:00-10:30 | 162min | 15:15 |
| AMBUJACEM | 4 | 0.407 | 52% | 80 | 0.4% | 70.57x | -68.12x | 10:00-10:30 | 10:00-10:30 | 149min | 15:15 |
| INFY | 4 | 0.448 | 72% | 80 | 0.5% | 59.78x | -58.71x | 10:00-10:30 | 13:00-13:30 | 156min | 15:15 |
| HAPPSTMNDS | 4 | 0.589 | 60% | 80 | 0.7% | 234.41x | -233.61x | 11:00-11:30 | 13:30-14:00 | 133min | 15:15 |
| TECHM | 4 | 0.467 | 51% | 80 | -0.0% | 0.00x | 0.00x | 10:00-10:30 | 10:00-10:30 | 180min | 15:15 |
| WIPRO | 4 | 0.471 | 72% | 80 | 0.3% | 2012.50x | -2010.13x | 10:00-10:30 | 13:00-13:30 | 188min | 15:15 |
| NAUKRI | 4 | 0.522 | 51% | 80 | 0.7% | 0.51x | 0.84x | 10:00-10:30 | 14:00-14:30 | 152min | 15:15 |
| ITC | 4 | 0.293 | 70% | 63 | 0.2% | 1197.66x | -1196.18x | 10:00-10:30 | 13:00-13:30 | 126min | 15:15 |
| EXIDEIND | 4 | 0.309 | 56% | 92 | 0.6% | 5.56x | 8.06x | 10:00-10:30 | 14:30-15:15 | 158min | 15:15 |
| NESTLEIND | 4 | 1.729 | 64% | 63 | -45.8% | 0.00x | 0.00x | 10:00-10:30 | 10:00-10:30 | 130min | 15:15 |
| PIDILITIND | 4 | 0.348 | 51% | 63 | 0.6% | 16.33x | -8.99x | 10:00-10:30 | 14:00-14:30 | 170min | 15:15 |
| UNITDSPR | 4 | 0.323 | 59% | 80 | 0.7% | 0.46x | 1.84x | 10:00-10:30 | 11:00-11:30 | 149min | 15:15 |
| ETERNAL | 4 | 0.412 | 58% | 80 | 0.8% | 0.17x | 1.04x | 11:00-11:30 | 14:30-15:15 | 135min | 15:15 |
| FIRSTCRY | 4 | 0.721 | 51% | 80 | 1.2% | 0.00x | 0.00x | 10:00-10:30 | 10:00-10:30 | 174min | 15:15 |
| LODHA | 4 | 0.612 | 59% | 80 | 0.7% | 0.88x | 0.13x | 10:00-10:30 | 13:30-14:00 | 212min | 15:15 |
| DRREDDY | 4 | 0.446 | 71% | 80 | 0.5% | 276.70x | -275.53x | 11:00-11:30 | 10:00-10:30 | 167min | 15:15 |
| GLENMARK | 4 | 0.608 | 63% | 92 | 0.8% | 0.00x | 0.00x | 10:00-10:30 | 10:00-10:30 | 164min | 15:15 |
| ZYDUSLIFE | 4 | 0.394 | 52% | 63 | 6.3% | 210.40x | -171.91x | 10:00-10:30 | 13:00-13:30 | 152min | 14:30 |
| GMDCLTD | 4 | 0.937 | 62% | 63 | 0.7% | 0.13x | 0.45x | 10:30-11:00 | 14:30-15:15 | 202min | 15:15 |
| AETHER | 4 | 0.481 | 51% | 80 | 0.8% | 37.24x | -29.63x | 11:00-11:30 | 13:30-14:00 | 145min | 15:15 |

### Open-Type Profiles

Predictability-scored profiles per opening type (drop -> recovery -> timing):

**PNB**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.79 | 10:00-10:30 | 0.00x | — | 0.00x | 165 | 33% |

**IDBI**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.64 | 10:00-10:30 | 0.00x | — | 0.00x | 206 | 100% |
| Gap Up Small | 5 | 0.68 | 10:00-10:30 | 5.14x | 14:30-15:15 | 0.88x | 158 | 20% |

**JSWENERGY**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 3 | 0.50 | 10:00-10:30 | 39.48x | — | -38.92x | 178 | 33% |
| Gap Up Large | 55 | 0.45 | 10:00-10:30 | 95.64x | 11:30-12:00 | -93.65x | 128 | 4% |

**GPIL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 5 | 0.48 | 11:00-11:30 | 0.08x | 13:00-13:30 | 0.15x | 158 | 40% |
| Gap Up Small | 3 | 0.43 | — | 0.34x | 14:30-15:15 | 0.10x | 175 | 67% |

**HAL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 13 | 0.41 | 10:00-10:30 | 0.00x | 10:30-11:00 | 0.00x | 128 | 62% |
| Gap Up Large | 60 | 0.52 | 10:00-10:30 | 10.42x | 11:30-12:00 | -0.59x | 152 | 2% |

**NBCC**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 3 | 0.45 | 10:00-10:30 | 16.49x | 10:00-10:30 | -1.64x | 120 | 67% |
| Gap Up Small | 5 | 0.62 | 10:00-10:30 | 3.42x | 14:30-15:15 | 7.46x | 192 | 40% |

**FINCABLES**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Large | 73 | 0.46 | 10:00-10:30 | 1019.68x | other | -979.09x | 238 | 0% |

**AEROFLEX**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 4 | 0.48 | 10:30-11:00 | 0.00x | — | 0.00x | 130 | 75% |
| Gap Up Small | 4 | 0.54 | 10:00-10:30 | 0.34x | 14:30-15:15 | 0.01x | 162 | 50% |

**SBIN**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 4 | 0.47 | 11:00-11:30 | 1.45x | 11:30-12:00 | 0.54x | 139 | 50% |
| Gap Down Small | 9 | 0.49 | 11:00-11:30 | 0.95x | 14:30-15:15 | 0.33x | 105 | 44% |
| Flat | 14 | 0.46 | 10:00-10:30 | 1.86x | 14:30-15:15 | -1.08x | 146 | 71% |
| Gap Up Small | 5 | 0.54 | 10:00-10:30 | 4.00x | 14:30-15:15 | -3.36x | 197 | 40% |

**AXISBANK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 10 | 0.43 | 10:00-10:30 | 0.00x | other | 0.00x | 212 | 80% |
| Gap Down Small | 5 | 0.43 | 10:00-10:30 | 0.21x | 14:30-15:15 | 0.12x | 213 | 40% |
| Flat | 6 | 0.55 | 10:00-10:30 | 0.01x | other | 0.31x | 194 | 50% |
| Gap Up Small | 6 | 0.45 | 10:00-10:30 | 0.12x | 14:30-15:15 | 0.06x | 201 | 50% |

**CANBK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 3 | 0.72 | 10:00-10:30 | 23.97x | — | -16.76x | 190 | 67% |

**UNIONBANK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 11 | 0.44 | 10:30-11:00 | 1.88x | other | 0.40x | 178 | 36% |
| Gap Down Small | 8 | 0.41 | 10:30-11:00 | 0.33x | 11:30-12:00 | 0.42x | 142 | 50% |
| Gap Up Large | 12 | 0.49 | 10:00-10:30 | 0.00x | other | 0.00x | 170 | 67% |

**FEDERALBNK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 31 | 0.44 | 10:00-10:30 | 0.45x | 14:30-15:15 | 0.62x | 166 | 52% |

**IDFCFIRSTB**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.62 | 10:30-11:00 | 219.65x | — | -218.29x | 206 | 67% |
| Gap Down Small | 5 | 0.56 | 10:00-10:30 | 0.17x | other | 0.15x | 157 | 40% |
| Flat | 4 | 0.59 | 10:00-10:30 | 0.04x | 14:30-15:15 | 0.44x | 168 | 100% |
| Gap Up Small | 3 | 0.50 | 10:00-10:30 | 142.33x | 11:00-11:30 | -141.86x | 136 | 33% |

**ABCAPITAL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 72 | 0.58 | 10:00-10:30 | 0.66x | other | 47.58x | 384 | 97% |
| Gap Up Small | 3 | 0.57 | 10:00-10:30 | 0.04x | — | 0.15x | 248 | 67% |

**BAJAJHFL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 7 | 0.42 | 10:00-10:30 | 0.12x | 14:30-15:15 | 0.15x | 138 | 43% |
| Gap Up Small | 3 | 0.74 | 10:00-10:30 | 0.10x | 14:30-15:15 | 0.26x | 255 | 100% |

**BSE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 12 | 0.59 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 170 | 50% |
| Gap Up Large | 8 | 0.72 | 10:00-10:30 | 2.68x | 12:00-12:30 | 5.77x | 140 | 88% |

**TATAPOWER**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.53 | 10:00-10:30 | 0.00x | 11:00-11:30 | 0.00x | 133 | 86% |
| Gap Down Small | 12 | 0.48 | 10:00-10:30 | 0.44x | 14:30-15:15 | 1.36x | 156 | 67% |
| Gap Up Small | 20 | 0.48 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 182 | 65% |

**GAIL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.59 | 10:30-11:00 | 0.00x | — | 0.00x | 165 | 100% |
| Flat | 7 | 0.60 | 10:00-10:30 | 0.00x | other | 0.00x | 127 | 57% |
| Gap Up Small | 3 | 0.83 | 10:00-10:30 | 0.18x | other | -0.08x | 284 | 67% |

**ADANIENSOL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 5 | 0.73 | 10:00-10:30 | 0.01x | other | 1.02x | 256 | 80% |

**HINDZINC**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 3 | 0.43 | 10:00-10:30 | 0.00x | 11:30-12:00 | 0.00x | 54 | 100% |

**BHEL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 7 | 0.49 | 10:00-10:30 | 0.27x | 10:30-11:00 | 0.45x | 182 | 57% |
| Gap Up Small | 26 | 0.42 | 10:00-10:30 | 0.40x | other | 0.84x | 204 | 65% |
| Gap Up Large | 6 | 0.55 | 10:00-10:30 | 0.46x | other | 0.37x | 224 | 17% |

**DATAPATTNS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 9 | 0.41 | 10:00-10:30 | 0.00x | other | 0.00x | 176 | 67% |

**SCI**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 3 | 0.43 | 10:00-10:30 | 15.02x | — | -14.65x | 194 | 0% |

**CUMMINSIND**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 72 | 0.49 | 10:00-10:30 | 37.46x | other | 19.12x | 332 | 96% |
| Gap Down Small | 3 | 0.43 | 10:30-11:00 | 0.11x | — | 0.12x | 145 | 67% |
| Flat | 6 | 0.51 | 10:00-10:30 | 0.06x | 14:30-15:15 | 0.17x | 140 | 67% |
| Gap Up Large | 9 | 0.49 | 10:00-10:30 | 2.96x | 14:30-15:15 | 1.00x | 197 | 78% |

**HAVELLS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 22 | 0.44 | 10:00-10:30 | 0.77x | 14:30-15:15 | 0.23x | 172 | 54% |
| Gap Up Large | 9 | 0.40 | 10:00-10:30 | 0.39x | 11:30-12:00 | 0.89x | 146 | 56% |

**AMBUJACEM**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.51 | 10:00-10:30 | 1.73x | 13:30-14:00 | 0.97x | 174 | 33% |
| Flat | 15 | 0.40 | 11:00-11:30 | 0.52x | 11:30-12:00 | 0.93x | 130 | 53% |
| Gap Up Small | 8 | 0.44 | 10:00-10:30 | 12.35x | 10:30-11:00 | -9.99x | 174 | 50% |

**INFY**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 10 | 0.51 | 10:00-10:30 | 16.43x | 14:30-15:15 | -15.47x | 219 | 30% |

**HAPPSTMNDS**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 3 | 0.43 | 10:00-10:30 | 0.17x | 10:30-11:00 | -0.05x | 52 | 0% |

**TECHM**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 9 | 0.52 | 10:00-10:30 | 0.00x | other | 0.00x | 157 | 89% |
| Flat | 10 | 0.44 | 10:00-10:30 | 3.79x | other | 0.78x | 180 | 50% |

**WIPRO**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 4 | 0.45 | 10:30-11:00 | 0.05x | 13:30-14:00 | 0.08x | 191 | 50% |
| Flat | 7 | 0.61 | 10:00-10:30 | 0.04x | 12:00-12:30 | 0.12x | 123 | 57% |

**NAUKRI**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Small | 14 | 0.40 | 10:00-10:30 | 0.19x | 14:30-15:15 | 0.30x | 170 | 64% |
| Gap Up Large | 9 | 0.43 | 10:00-10:30 | 2.07x | 14:30-15:15 | 1.94x | 193 | 44% |

**EXIDEIND**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 11 | 0.44 | 10:00-10:30 | 6.34x | 14:00-14:30 | 8.48x | 163 | 54% |
| Gap Up Small | 15 | 0.54 | 10:00-10:30 | 7.51x | other | 10.13x | 218 | 60% |
| Gap Up Large | 6 | 0.65 | 10:30-11:00 | 0.00x | 14:30-15:15 | 0.00x | 142 | 83% |

**NESTLEIND**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Flat | 17 | 0.43 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 117 | 53% |
| Gap Up Small | 7 | 0.60 | 10:00-10:30 | 0.00x | 11:00-11:30 | 0.00x | 112 | 43% |

**PIDILITIND**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 7 | 0.57 | 10:00-10:30 | 2.71x | 12:00-12:30 | 2.84x | 165 | 29% |
| Gap Down Small | 13 | 0.47 | 10:30-11:00 | 1.80x | 14:30-15:15 | 5.89x | 165 | 54% |
| Gap Up Small | 10 | 0.61 | 10:00-10:30 | 4.18x | 14:30-15:15 | 3.92x | 198 | 60% |
| Gap Up Large | 4 | 0.48 | — | 216.96x | 14:30-15:15 | -205.36x | 224 | 25% |

**UNITDSPR**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 12 | 0.52 | 10:00-10:30 | 0.17x | other | 0.61x | 157 | 50% |
| Flat | 19 | 0.50 | 10:00-10:30 | 0.50x | other | 0.32x | 187 | 68% |
| Gap Up Small | 15 | 0.43 | 10:00-10:30 | 1.67x | other | 2.80x | 158 | 47% |
| Gap Up Large | 6 | 0.50 | 10:00-10:30 | 3.70x | 12:30-13:00 | 3.70x | 116 | 50% |

**ETERNAL**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Small | 10 | 0.56 | 10:00-10:30 | 0.14x | 11:00-11:30 | 0.58x | 110 | 60% |
| Gap Up Small | 11 | 0.43 | 10:00-10:30 | 0.00x | other | 0.00x | 163 | 54% |
| Gap Up Large | 11 | 0.54 | 10:00-10:30 | 0.40x | 12:00-12:30 | 1.86x | 137 | 54% |

**FIRSTCRY**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 16 | 0.42 | 10:00-10:30 | 0.14x | other | 1.05x | 211 | 69% |
| Gap Down Small | 7 | 0.41 | 10:30-11:00 | 0.24x | 11:00-11:30 | 0.56x | 116 | 57% |
| Flat | 32 | 0.41 | 10:00-10:30 | 0.31x | other | 0.22x | 166 | 50% |

**LODHA**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Up Large | 14 | 0.45 | 10:00-10:30 | 0.85x | other | 0.16x | 232 | 43% |

**DRREDDY**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 5 | 0.56 | 10:00-10:30 | 0.00x | 11:30-12:00 | 0.00x | 211 | 100% |
| Flat | 4 | 0.54 | 10:00-10:30 | 0.00x | 12:00-12:30 | 0.00x | 78 | 50% |

**GLENMARK**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 15 | 0.56 | 10:00-10:30 | 0.00x | 14:30-15:15 | 0.00x | 176 | 93% |
| Gap Down Small | 6 | 0.63 | 10:00-10:30 | 0.77x | 14:30-15:15 | 0.68x | 195 | 100% |
| Flat | 7 | 0.64 | 10:00-10:30 | 0.76x | 10:30-11:00 | 0.64x | 58 | 29% |

**ZYDUSLIFE**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 6 | 0.45 | 10:00-10:30 | 1.79x | 14:00-14:30 | 3.62x | 206 | 50% |
| Flat | 14 | 0.46 | 10:00-10:30 | 1.86x | 14:30-15:15 | 1.29x | 134 | 57% |
| Gap Up Small | 6 | 0.48 | 10:00-10:30 | 1.82x | 11:00-11:30 | 2.44x | 142 | 67% |
| Gap Up Large | 25 | 0.44 | 10:00-10:30 | 527.29x | other | -436.30x | 165 | 4% |

**AETHER**:

| Open Type | n | Pred | Low Window | Drop(xATR) | Post-Low High Window | High(xATR) | Window(min) | Past Open% |
|-----------|---|------|------------|------------|----------------------|------------|-------------|------------|
| Gap Down Large | 10 | 0.48 | 10:00-10:30 | 7.55x | 12:00-12:30 | 1.81x | 170 | 80% |
| Flat | 20 | 0.41 | 10:00-10:30 | 1.16x | 14:30-15:15 | 7.69x | 145 | 55% |
| Gap Up Small | 11 | 0.57 | 10:30-11:00 | 6.02x | other | -0.38x | 154 | 0% |

## Disabled Tickers

- **ABB**: low EV/WR or OOS degraded
- **ADANIENT**: low EV/WR or OOS degraded
- **ADANIGREEN**: low EV/WR or OOS degraded
- **ADANIPORTS**: low EV/WR or OOS degraded
- **ANANTRAJ**: low EV/WR or OOS degraded
- **APOLLOHOSP**: low EV/WR or OOS degraded
- **ASIANPAINT**: low EV/WR or OOS degraded
- **AUBANK**: low EV/WR or OOS degraded
- **BAJAJ-AUTO**: low EV/WR or OOS degraded
- **BAJAJFINSV**: low EV/WR or OOS degraded
- **BAJFINANCE**: low EV/WR or OOS degraded
- **BEL**: low EV/WR or OOS degraded
- **BHARTIARTL**: low EV/WR or OOS degraded
- **BRITANNIA**: low EV/WR or OOS degraded
- **CAMS**: low EV/WR or OOS degraded
- **CGPOWER**: low EV/WR or OOS degraded
- **CIPLA**: low EV/WR or OOS degraded
- **COALINDIA**: low EV/WR or OOS degraded
- **COCHINSHIP**: low EV/WR or OOS degraded
- **DIVISLAB**: low EV/WR or OOS degraded
- **DLF**: low EV/WR or OOS degraded
- **DMART**: low EV/WR or OOS degraded
- **EICHERMOT**: low EV/WR or OOS degraded
- **GODREJCP**: low EV/WR or OOS degraded
- **GRAPHITE**: low EV/WR or OOS degraded
- **GRASIM**: low EV/WR or OOS degraded
- **GUJALKALI**: low EV/WR or OOS degraded
- **HCLTECH**: low EV/WR or OOS degraded
- **HDFCBANK**: low EV/WR or OOS degraded
- **HDFCLIFE**: low EV/WR or OOS degraded
- **HINDALCO**: low EV/WR or OOS degraded
- **HINDPETRO**: low EV/WR or OOS degraded
- **HINDUNILVR**: low EV/WR or OOS degraded
- **ICICIBANK**: low EV/WR or OOS degraded
- **INDIANB**: low EV/WR or OOS degraded
- **INDIGO**: low EV/WR or OOS degraded
- **INDUSINDBK**: low EV/WR or OOS degraded
- **IOC**: low EV/WR or OOS degraded
- **JINDALSTEL**: low EV/WR or OOS degraded
- **JIOFIN**: low EV/WR or OOS degraded
- **JSWSTEEL**: low EV/WR or OOS degraded
- **KOTAKBANK**: low EV/WR or OOS degraded
- **LICI**: low EV/WR or OOS degraded
- **LT**: low EV/WR or OOS degraded
- **M&M**: low EV/WR or OOS degraded
- **MAZDOCK**: low EV/WR or OOS degraded
- **MCX**: low EV/WR or OOS degraded
- **MOTHERSON**: low EV/WR or OOS degraded
- **MTARTECH**: low EV/WR or OOS degraded
- **NETWEB**: low EV/WR or OOS degraded
- **NTPC**: low EV/WR or OOS degraded
- **ONGC**: low EV/WR or OOS degraded
- **PFC**: low EV/WR or OOS degraded
- **POWERGRID**: low EV/WR or OOS degraded
- **RECLTD**: low EV/WR or OOS degraded
- **RELIANCE**: low EV/WR or OOS degraded
- **RVNL**: low EV/WR or OOS degraded
- **SAILIFE**: low EV/WR or OOS degraded
- **SBILIFE**: low EV/WR or OOS degraded
- **SHRIRAMFIN**: low EV/WR or OOS degraded
- **SUNPHARMA**: low EV/WR or OOS degraded
- **TATACONSUM**: low EV/WR or OOS degraded
- **TCS**: low EV/WR or OOS degraded
- **TITAN**: low EV/WR or OOS degraded
- **TMPV**: low EV/WR or OOS degraded
- **TORNTPHARM**: low EV/WR or OOS degraded
- **TRENT**: low EV/WR or OOS degraded
- **TVSMOTOR**: low EV/WR or OOS degraded
- **VEDL**: low EV/WR or OOS degraded

## Key Parameters

- Minimum sample: 15 trading days with morning low
- Minimum win rate: 50.0%
- Round-trip cost: 0.1%
- OOS train/test split: 70/30
- Monte Carlo iterations: 10,000

Generated: 2026-04-23 17:08