# Scalp Configuration Guide
*Auto-generated on 2026-04-14 — 0 tickers (0 enabled)*

## Quick Reference

| Ticker | On | Edge | Best EV | Best Phase | Verdict |
|--------|:--:|:----:|--------:|------------|---------|

---

## Glossary

- **EV (Expected Value)**: The average profit/loss per trade if you repeated it many times. Positive EV means the strategy makes money over time.
- **Hit Rate**: How often the trade reaches target before stop — like a batting average for your setup.
- **ATR (Average True Range)**: How much the stock typically moves in a day. Higher ATR = more volatile = wider stops needed.
- **Kelly Fraction**: A formula that sizes your bet based on your edge. Half-Kelly (what we use) is more conservative to account for estimation error.
- **Edge Strength (1-5)**: Composite score combining EV, win rate, sample size, and trap safety. 5 = strongest statistical edge.
- **FDR (False Discovery Rate)**: Controls for lucky flukes when testing many combos. Benjamini-Hochberg ensures we're not fooled by random chance.
- **OOS (Out-of-Sample)**: Walk-forward validation: train on first 70% of data, test on last 30%. Checks if the edge holds on unseen data.
- **Gap Type**: How the stock opens relative to yesterday's close — gap_up, gap_down, or flat. Different gap types have different trading characteristics.
- **VWAP**: Volume-Weighted Average Price — the 'fair price' for the day. Reclaiming VWAP after a dip is a bullish signal.
- **Phase**: Time-of-day window (e.g., MORNING_SCALP 9:30-10:30). Different phases have different volatility and win-rate profiles.
