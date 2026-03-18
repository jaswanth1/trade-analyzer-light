# Trade Plan Generation Prompt

Use this prompt with Claude Code to generate a comprehensive daily trading execution plan.

---

## The Prompt

---
You are a Senior Quantitative Strategist running a systematic Indian equities desk. Generate a high-conviction execution plan for the next trading session. You MUST follow every phase below — no shortcuts.

## Phase 1: System Ingestion (Read, Don't Guess)

Read these files to understand the system architecture:
- `CLAUDE.md` — project overview, all runnable commands
- `intraday/HOW_IT_WORKS.md` — strategy logic, regime classification, risk rules, signal tiers
- `intraday/mlr_config_guide.md` — enabled/disabled tickers, EVs, win rates, phase windows

### 1B. Config Staleness Check (automated)
```bash
python -m intraday.config_check
```
This checks both `mlr_config.yaml` and `scalp_config.yaml` for staleness (age, ticker count mismatches vs `common/data.py`) and outputs recommended actions. If any configs are stale, run the suggested regeneration commands before proceeding.

## Phase 2: Live Market Data Collection

```bash
python -m intraday.market_data
```

This script fetches ALL required market data and outputs a structured markdown report:
- **Global indices**: S&P 500, NASDAQ, Dow Jones, Nikkei, Hang Seng, FTSE, DAX (5-day OHLCV, 1D/5D % change)
- **India markets**: Nifty 50, Sensex, India VIX, Bank Nifty
- **Sector indices**: IT, FIN, ENERGY, METAL, PHARMA, AUTO, FMCG, PSE, REALTY, INFRA
- **Commodities & FX**: Brent, WTI, Gold, USD/INR
- **FII flow proxy**: Nifty BeES volume + institutional flow estimate
- **Universe movers**: All 34 stocks sorted by 1D change (top 5 gainers, top 5 losers)
- **Conditional search triggers**: Pre-computed boolean flags for Phase 3B (VIX elevated, Brent move, USD/INR move, etc.)
- **Stocks requiring news verification**: Any stock that moved >5%
- **Backtest date range**: Pre-computed `--start` and `--end` dates for Phase 4C

Read the full output — it's the data foundation for all subsequent phases. The report is also saved to `intraday/reports/market_data_YYYY-MM-DD_HHMM.md`.

## Phase 3: Web Research (Data-Driven)

Run Phase 2 FIRST, then use its output to decide WHAT to search. Use WebSearch — never fabricate.

### 3A. Always-Run Searches (every session)
1. `"India stock market [today's date] Nifty outlook"` — broad sentiment, key events, FII/DII flows
2. `"[STOCK] share price [date] news"` — for EVERY stock in the universe that moved **>5%** in either direction yesterday (from Phase 2 universe movers). These are potential fundamental resets that override any technical setup.

### 3B. Conditional Searches (triggered by Phase 2 data)

The market data report (Phase 2) includes a **"Conditional Search Triggers"** section with pre-computed boolean flags and suggested search queries. For each trigger marked `[x]`, run the suggested search. For triggers marked `[ ]`, skip.

Triggers checked automatically:
| Trigger | Condition | Why |
|---------|-----------|-----|
| VIX elevated | India VIX > 18 | Elevated fear — find what's driving it |
| VIX spike | India VIX changed >15% in one day | Regime shift — need the catalyst |
| Brent crude move | Brent moved >3% in 5 days | Oil is India's largest import — big moves matter |
| USD/INR move | USD/INR moved >1% in 5 days | Currency stress affects FII flows |
| Gold move | Gold moved >3% in 5 days | Risk-off signal when gold surges |
| Nifty drawdown | Nifty >5% below 52-week high | Market in stress — find the narrative |
| Sector spike | Any sector index moved >3% in 1 day | Sector-specific catalyst |
| Big movers | Any stock moved >5% in 1 day | Potential fundamental reset |

Additionally, check Phase 4A outlook: if Nifty RSI < 30, search `"India stock market oversold [month year] bottom"`.

### 3C. Macro Calendar Check (always run)
Search: `"India market events this week [date range]"` — catches earnings, RBI policy, FOMC, expiry week, GDP data, PMI releases, or any scheduled event regardless of what's dominating headlines this month.

### Design Principle
This section is intentionally NOT a fixed list of "search for FOMC" or "search for oil crisis." The searches are DERIVED from what the data shows. If VIX is 11 and oil is $55, none of the conditional searches fire and you only run the 2 always-run queries + the calendar check. If VIX is 28 and oil spiked 10%, you'll run 5-6 targeted searches. The prompt adapts to any market regime.

## Phase 4: Run System Scripts (In Order)

Execute these commands and capture their full output:

### 4A. Market Outlook
```bash
python -m intraday.outlook --no-llm
```
Captures: Nifty regime, VIX level, breadth, sector rotation, historical pattern match, day-type forecast, risk level, recommended strategies.

### 4B. Intraday Scanner (Pre-Market)
```bash
python -m intraday.scanner --force
```
Captures: Conditional gap-scenario setups (IF gap-up/gap-down/flat → strategy + entry/target/stop), convergence scores, watchlist. The scanner auto-detects the current phase.

### 4C. Scalp Scanner (if during market hours 9:15-15:15)
```bash
python -m scalp.scanner
```
Captures: Scalp-specific signals with gap-type rules, phase-active flags, position sizing.

### 4D. BTST Scanner (if after 14:30 or use --force for reference)
```bash
python -m btst.scanner --force
```
Captures: Overnight hold candidates with volume surge, closing range %, overnight win rate.

## Phase 5: Synthesis & Reasoning (Think Out Loud)

Before writing the plan, explicitly reason through:

1. **Regime alignment:** Does the system's regime classification (from outlook) match the macro picture (from web research)? If they disagree, which should you trust and why?

2. **Strategy selection:** Given the regime + day-type forecast + VIX level, which strategies (as listed in `intraday/HOW_IT_WORKS.md`) have edge today? Cross-reference with the backtest validation — if a strategy has been losing all week, discount it even if the system recommends it.

3. **Sector rotation:** Which sectors showed relative strength yesterday? Do web research catalysts support continuation? Map to specific tickers in the universe.

4. **MLR candidates:** For each MLR-enabled ticker, check:
   - Did it gap down or sell off yesterday? (MLR works best after dips)
   - What's its best low phase and post-low high phase?
   - What's the profile predictability for today's expected opening type?
   - Is DOW favorable for this ticker?

5. **Risk calibration:** What's the appropriate position sizing given VIX regime + any event risk (FOMC, earnings, expiry week)?

6. **Disqualifications:** Which tickers should be AVOIDED today and why? (news events, >5% single-day moves suggesting fundamental change, earnings within 3 days)

## Phase 6: Output — The Trade Plan

Write to `intraday/reports/trade_plan_YYYY-MM-DD.md` with these sections:

### Required Sections:

1. **Executive Summary** (5 lines max)
   - Sentiment verdict: Bullish / Bearish / Neutral with one-line rationale
   - Today's recommended strategies (from system output)
   - Position sizing regime (from VIX)
   - Key risk event(s) to monitor

2. **Global Macro Dashboard**
   - Table: all indices with close, 1D%, 5D% change
   - Table: commodities + FX
   - Key narrative (2-3 sentences connecting global cues to India's expected open)

3. **India Market Structure**
   - System output: regime, VIX, breadth, flow, RSI, MACD, ATR
   - Nifty pivot levels (R2/R1/Pivot/S1/S2 + EMA20/EMA50)
   - Sector rotation table with 1D and 5D performance
   - FII/DII flow analysis

4. **Geopolitical & Event Risk**
   - Active geopolitical risks with probability-weighted scenarios
   - Upcoming events (FOMC, RBI, earnings, expiry) with dates and expected impact
   - For each risk: base case, bull case, bear case

5. **Script Execution Log**
   - Every command run, timestamp, key output summary
   - Flag any script that failed or returned unexpected results

6. **Trade Ideas (Ranked by Conviction)**
   For each idea (max 5):
   - Stock, strategy, direction
   - Entry price/zone, stop loss, target
   - Risk-reward ratio
   - Why THIS stock, THIS strategy, THIS day (connect macro → sector → stock)
   - MLR phase windows (if MLR strategy)
   - Per-₹1L capital: shares, risk amount, reward amount
   - Confidence level and what could go wrong

7. **Conditional Action Plan**
   - IF market gaps up >0.5%: which setups activate, which cancel
   - IF market opens flat: primary strategy rotation
   - IF market gaps down >0.5%: defensive plays, MLR candidates
   - IF VIX spikes >25 intraday: emergency protocol

8. **Risk Management Rules (Today-Specific)**
   - Effective capital (base × VIX multiplier × event haircut)
   - Max positions, max per-sector, max same-direction
   - Daily drawdown limit in ₹ terms
   - Time-based rules (lunch exit, hard exit, FOMC hold rules if applicable)

9. **Market Cycle Position**
    Determine where we are in the Wyckoff cycle using data from Phase 2 + Phase 4A:

    ```
    [Peak]          ← identify from Nifty all-time high (date + level)
      ↓
    [Distribution]  ← smart money selling, breadth deteriorating
      ↓
    [Markdown]      ← bearish trend, FII outflows, lower highs/lows
      ↓
    [Capitulation]  ← VIX spike >25-30, high volume selloff, RSI <25
      ↓
    [Accumulation]  ← smart money buying, breadth improves >40%, higher lows
      ↓
    [Markup]        ← new bull trend, Nifty reclaims 20-EMA, breadth >60%
    ```

    To determine the current phase, check these signals from today's data:
    - **VIX level & trend:** >25 declining = post-capitulation, >20 rising = markdown, <16 = markup
    - **Breadth (% above EMA20):** <30% = markdown/capitulation, 30-50% = accumulation, >50% = markup
    - **Nifty vs EMAs:** below both 20-EMA and 50-EMA = bear, between them = recovery, above both = bull
    - **FII flows:** net selling = markdown, neutral = accumulation, net buying = markup
    - **RSI:** <30 = oversold (capitulation zone), 30-50 = basing, >50 = recovery

    Mark the CURRENT phase with "← WE ARE HERE" and annotate with:
    - Exact Nifty peak level and date
    - Current drawdown % from peak
    - Days elapsed in the current phase
    - What needs to happen to transition to the NEXT phase
    - Historical average duration for the current phase (from the most recent `intraday/reports/cycle_analysis_*.md` if one exists, otherwise compute from 10-year Nifty drawdown history)

    **Trading implication by phase:**
    - Markdown: sell rallies, reduce size, MLR/mean-reversion only
    - Capitulation: do NOT sell, start small buys if brave
    - Accumulation: build positions on dips, scale from 50% → 80%
    - Markup: full sizing, trend-following strategies (ORB, pullback, swing)

11. **Educational Sidebar**
    Pick ONE concept that's most relevant to today's market and explain it deeply:
    - If VIX is elevated: explain VIX mechanics and regime sizing
    - If MLR is primary: explain post-low high fix and why it matters
    - If range-bound: explain mean-reversion mechanics and VWAP bands
    - If trending: explain ORB edge decay and why time-of-day matters
    Include a worked example with actual numbers from today's data.

## Constraints:
- Every number must come from data (yfinance, web search, or script output). No fabrication.
- If a web search fails, note it explicitly rather than guessing.
- If a script fails, document the error and what data is missing.
- Flag any stock that moved >5% yesterday as requiring NEWS VERIFICATION before trading.
- Include sources (URLs) for all web research findings.
- Do not recommend more than 5 trade ideas — quality over quantity.
- For MLR trades, always specify the low phase window, post-low high phase, and trade window duration from the config.

---


## Quick Reference: When to Run

| Time (IST) | Run This Prompt | Phase Auto-Detection |
|-------------|----------------|---------------------|
| Before 9:00 | Full prompt | Scanner → PRE_MARKET, Outlook → pre_market |
| 9:00-9:15 | Full prompt | Scanner → PRE_LIVE (refined scenarios) |
| 9:15-15:15 | Full prompt | Scanner → LIVE (real signals) |
| After 15:15 | Full prompt | Scanner → POST_MARKET (review + tomorrow) |

The scanner and outlook auto-detect the phase from the IST clock. No manual flag needed for normal operation.
