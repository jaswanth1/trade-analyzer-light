# MLR Config Guide

Auto-generated documentation for Morning Low Recovery configuration.

## What is MLR?

Morning Low Recovery buys stocks that form their daily low in the first
90 minutes of trading (9:15–11:00 AM IST) and show confirmed reversal.
Data shows ~57% of daily lows form in this window, with average +2.2%
recovery to close.

## How the Config Works

For each ticker, the generator:
1. Fetches 60 days of 5-minute OHLCV data (+ 1 year daily)
2. Identifies days where the session low formed before 11:00 AM
3. Computes recovery statistics (to close, to high)
4. Grid-searches optimal entry delay, stop, and target combinations
5. Validates with 70/30 walk-forward out-of-sample test
6. Runs Monte Carlo bootstrap for 95% confidence intervals
7. Computes DOW/month seasonality and time-bucket probabilities

## Enabled Tickers

| Ticker | Edge | EV | WR% | Sample | Avg Recovery |
|--------|------|----|-----|--------|-------------|
| CAMS | 5 | 0.872 | 69% | 58 | 1.1% |
| IDBI | 5 | 1.369 | 76% | 58 | 1.7% |
| BSE | 5 | 1.312 | 95% | 58 | 1.4% |
| PFC | 5 | 0.843 | 67% | 58 | 1.4% |
| ABCAPITAL | 5 | 1.048 | 79% | 58 | 1.4% |
| INDIANB | 5 | 0.990 | 76% | 58 | 1.6% |
| TATAPOWER | 5 | 0.590 | 74% | 58 | 0.9% |
| GRAPHITE | 5 | 1.748 | 76% | 58 | 2.0% |
| BEL | 5 | 0.776 | 90% | 58 | 1.2% |
| BHEL | 5 | 1.107 | 83% | 58 | 1.5% |
| DATAPATTNS | 5 | 1.888 | 81% | 58 | 2.2% |
| MTARTECH | 5 | 1.852 | 67% | 58 | 2.6% |
| RVNL | 5 | 1.217 | 69% | 58 | 1.6% |
| ADANIPORTS | 5 | 0.528 | 69% | 58 | 1.0% |
| CUMMINSIND | 5 | 0.902 | 71% | 58 | 1.4% |
| HAVELLS | 5 | 0.631 | 78% | 58 | 1.0% |
| NETWEB | 5 | 1.795 | 78% | 58 | 2.1% |
| EXIDEIND | 5 | 0.652 | 79% | 58 | 0.9% |
| TRENT | 5 | 0.902 | 71% | 58 | 1.3% |
| GLENMARK | 5 | 0.843 | 67% | 58 | 1.1% |
| AEROFLEX | 5 | 1.907 | 69% | 58 | 2.0% |
| SAILIFE | 4 | 1.190 | 55% | 58 | 1.7% |
| KFINTECH | 4 | 1.028 | 60% | 58 | 1.3% |
| ADANIPOWER | 4 | 0.755 | 62% | 58 | 1.4% |
| NTPC | 4 | 0.466 | 64% | 58 | 1.0% |
| COALINDIA | 4 | 0.609 | 53% | 58 | 1.2% |
| GPIL | 4 | 1.422 | 64% | 58 | 1.8% |
| ADANIENT | 4 | 0.726 | 60% | 58 | 1.1% |
| HAL | 4 | 0.755 | 62% | 58 | 1.2% |
| SCI | 4 | 1.376 | 62% | 58 | 1.7% |
| VBL | 4 | 0.838 | 52% | 58 | 1.3% |

## Disabled Tickers

- **ANANTRAJ**: low EV/WR or OOS degraded
- **FINCABLES**: low EV/WR or OOS degraded
- **NBCC**: low EV/WR or OOS degraded

## Key Parameters

- Minimum sample: 15 trading days with morning low
- Minimum win rate: 50.0%
- Round-trip cost: 0.1%
- OOS train/test split: 70/30
- Monte Carlo iterations: 10,000

Generated: 2026-03-02 11:08