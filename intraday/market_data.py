"""
Market Data Report — fetch all market data needed for the trade plan.

Fetches global indices, India markets, sector indices, commodities/FX,
FII flow proxy, and universe movers. Computes conditional search triggers
and backtest date range. Outputs structured markdown (or JSON).

Usage:
    python -m intraday.market_data          # full run, markdown to stdout
    python -m intraday.market_data --json   # output JSON instead of markdown
"""

import argparse
import json
import sys
import warnings
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from common.data import fetch_yf, TICKERS, INTRADAY_REPORT_DIR
from common.market import fetch_india_vix, estimate_institutional_flow

warnings.filterwarnings("ignore")

IST = ZoneInfo("Asia/Kolkata")

# ── Ticker definitions ───────────────────────────────────────────────────

GLOBAL_INDICES = {
    "^GSPC":  "S&P 500",
    "^IXIC":  "NASDAQ",
    "^DJI":   "Dow Jones",
    "^N225":  "Nikkei 225",
    "^HSI":   "Hang Seng",
    "^FTSE":  "FTSE 100",
    "^GDAXI": "DAX",
}

INDIA_MARKETS = {
    "^NSEI":     "Nifty 50",
    "^BSESN":    "Sensex",
    "^INDIAVIX": "India VIX",
    "^NSEBANK":  "Bank Nifty",
}

SECTOR_INDICES = {
    "^CNXIT":      "IT",
    "^CNXFIN":     "FIN",
    "^CNXENERGY":  "ENERGY",
    "^CNXMETAL":   "METAL",
    "^CNXPHARMA":  "PHARMA",
    "^CNXAUTO":    "AUTO",
    "^CNXFMCG":    "FMCG",
    "^CNXPSE":     "PSE",
    "^CNXREALTY":  "REALTY",
    "^CNXINFRA":   "INFRA",
}

COMMODITIES_FX = {
    "BZ=F":  "Brent Crude",
    "CL=F":  "WTI Crude",
    "GC=F":  "Gold",
    "INR=X": "USD/INR",
}

FII_PROXY_TICKER = "0P0000XVSO.BO"

# Sector ticker → friendly name (for universe movers table)
SECTOR_FRIENDLY = {
    "^CNXFIN": "FIN", "^CNXENERGY": "ENERGY", "^CNXMETAL": "METAL",
    "^CNXPSE": "PSE", "^CNXINFRA": "INFRA", "^CNXIT": "IT",
    "^CNXAUTO": "AUTO", "^CNXFMCG": "FMCG", "^CNXREALTY": "REALTY",
    "^CNXPHARMA": "PHARMA",
}


# ── Helpers ──────────────────────────────────────────────────────────────

def _pct_change(series, periods):
    """Compute % change over N periods. Returns None if insufficient data."""
    if series is None or len(series) < periods + 1:
        return None
    old = float(series.iloc[-(periods + 1)])
    new = float(series.iloc[-1])
    if old == 0:
        return None
    return (new / old - 1) * 100


def _fmt_pct(val):
    """Format % value with sign, or N/A."""
    if val is None:
        return "N/A"
    return f"{val:+.2f}%"


def _fmt_price(val):
    """Format price with commas, or N/A."""
    if val is None:
        return "N/A"
    return f"{val:,.2f}"


def _stderr(msg):
    """Print progress to stderr so stdout stays clean."""
    print(msg, file=sys.stderr)


# ── Batch fetch ──────────────────────────────────────────────────────────

def _batch_fetch(ticker_map, period="5d", interval="1d"):
    """Batch-fetch a group of tickers using yf.download.

    Returns dict: {ticker: DataFrame} with single-level columns.
    """
    tickers = list(ticker_map.keys())
    if not tickers:
        return {}

    try:
        raw = yf.download(tickers, period=period, interval=interval, progress=False)
    except Exception as e:
        _stderr(f"  [WARN] Batch download failed: {e}")
        return {}

    if raw.empty:
        return {}

    result = {}
    # Single ticker: columns are flat (Open, High, ...). Multi-ticker: MultiIndex.
    if len(tickers) == 1:
        sym = tickers[0]
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.droplevel("Ticker")
        result[sym] = raw
    else:
        for sym in tickers:
            try:
                df = raw.xs(sym, level="Ticker", axis=1)
                if not df.dropna(how="all").empty:
                    result[sym] = df.dropna(how="all")
            except (KeyError, TypeError):
                pass

    return result


def _extract_table_rows(data_dict, name_map):
    """Build list of row dicts (name, close, 1d%, 5d%) from fetched data."""
    rows = []
    for sym, name in name_map.items():
        df = data_dict.get(sym)
        if df is None or df.empty:
            rows.append({"symbol": sym, "name": name, "close": None, "chg_1d": None, "chg_5d": None})
            continue
        close = df["Close"].dropna()
        last = float(close.iloc[-1]) if len(close) > 0 else None
        chg_1d = _pct_change(close, 1)
        chg_5d = _pct_change(close, min(4, len(close) - 1)) if len(close) >= 2 else None
        rows.append({"symbol": sym, "name": name, "close": last, "chg_1d": chg_1d, "chg_5d": chg_5d})
    return rows


# ── FII Flow Proxy ───────────────────────────────────────────────────────

def _fetch_fii_proxy():
    """Fetch Nifty BeES 5-day data and compute flow estimate."""
    try:
        df = fetch_yf(FII_PROXY_TICKER, period="5d", interval="1d")
    except Exception:
        df = pd.DataFrame()

    if df.empty or len(df) < 2:
        return [], "neutral"

    median_vol = float(df["Volume"].iloc[:-1].median()) if len(df) > 1 else 0
    rows = []
    for i in range(len(df)):
        date_str = str(df.index[i].date()) if hasattr(df.index[i], "date") else str(df.index[i])[:10]
        close = float(df["Close"].iloc[i])
        vol = int(df["Volume"].iloc[i])
        vol_vs_med = f"{vol / median_vol:.2f}x" if median_vol > 0 else "N/A"
        rows.append({"date": date_str, "close": close, "volume": vol, "vol_vs_median": vol_vs_med})

    flow = estimate_institutional_flow(df)
    return rows, flow


# ── Universe Movers ──────────────────────────────────────────────────────

def _fetch_universe_movers():
    """Fetch all TICKERS, compute 1d/5d % change, return sorted list."""
    _stderr(f"  Fetching {len(TICKERS)} universe stocks...")
    movers = []
    for sym, meta in TICKERS.items():
        df = fetch_yf(sym, period="5d", interval="1d")
        if df.empty or len(df) < 2:
            continue
        close = df["Close"].dropna()
        if len(close) < 2:
            continue
        last = float(close.iloc[-1])
        chg_1d = _pct_change(close, 1)
        chg_5d = _pct_change(close, min(4, len(close) - 1)) if len(close) >= 2 else None
        sector = SECTOR_FRIENDLY.get(meta["sector"], meta["sector"])
        movers.append({
            "symbol": sym.replace(".NS", ""),
            "name": meta["name"],
            "sector": sector,
            "close": last,
            "chg_1d": chg_1d,
            "chg_5d": chg_5d,
        })

    movers.sort(key=lambda x: x["chg_1d"] if x["chg_1d"] is not None else 0, reverse=True)
    return movers


# ── Conditional Search Triggers ──────────────────────────────────────────

def _compute_triggers(india_data, sector_data, commodities_data, movers):
    """Compute boolean conditional search triggers."""
    now = datetime.now(IST)
    month_year = now.strftime("%B %Y")
    triggers = {}

    # VIX elevated
    vix_val, vix_regime = fetch_india_vix()
    triggers["vix_elevated"] = {
        "active": vix_val is not None and vix_val > 18,
        "detail": f"{vix_val} > 18" if vix_val else "N/A",
        "search": f'"India VIX {month_year} market volatility reason"',
    }

    # VIX spike (>15% 1-day change)
    vix_df = india_data.get("^INDIAVIX")
    vix_spike = False
    vix_spike_detail = "N/A"
    if vix_df is not None and not vix_df.empty and len(vix_df) >= 2:
        vix_close = vix_df["Close"].dropna()
        if len(vix_close) >= 2:
            vix_chg = abs(_pct_change(vix_close, 1) or 0)
            vix_spike = vix_chg > 15
            vix_spike_detail = f"{vix_chg:.1f}% {'>' if vix_spike else '<'} 15%"
    triggers["vix_spike"] = {
        "active": vix_spike,
        "detail": vix_spike_detail,
        "search": f'"India VIX spike {month_year} reason"',
    }

    # Brent move (>3% in 5 days)
    brent_df = commodities_data.get("BZ=F")
    brent_move = False
    brent_detail = "N/A"
    if brent_df is not None and not brent_df.empty:
        brent_close = brent_df["Close"].dropna()
        brent_chg = _pct_change(brent_close, min(4, len(brent_close) - 1)) if len(brent_close) >= 2 else None
        if brent_chg is not None:
            brent_move = abs(brent_chg) > 3
            brent_detail = f"{brent_chg:+.1f}% {'>' if brent_move else '<'} 3%"
    triggers["brent_move"] = {
        "active": brent_move,
        "detail": brent_detail,
        "search": f'"Brent crude oil price {month_year} movement reason"',
    }

    # USD/INR move (>1% in 5 days)
    inr_df = commodities_data.get("INR=X")
    inr_move = False
    inr_detail = "N/A"
    if inr_df is not None and not inr_df.empty:
        inr_close = inr_df["Close"].dropna()
        inr_chg = _pct_change(inr_close, min(4, len(inr_close) - 1)) if len(inr_close) >= 2 else None
        if inr_chg is not None:
            inr_move = abs(inr_chg) > 1
            inr_detail = f"{inr_chg:+.2f}% {'>' if inr_move else '<'} 1%"
    triggers["usdinr_move"] = {
        "active": inr_move,
        "detail": inr_detail,
        "search": f'"USD INR rupee {month_year} movement reason"',
    }

    # Gold move (>3% in 5 days)
    gold_df = commodities_data.get("GC=F")
    gold_move = False
    gold_detail = "N/A"
    if gold_df is not None and not gold_df.empty:
        gold_close = gold_df["Close"].dropna()
        gold_chg = _pct_change(gold_close, min(4, len(gold_close) - 1)) if len(gold_close) >= 2 else None
        if gold_chg is not None:
            gold_move = abs(gold_chg) > 3
            gold_detail = f"{gold_chg:+.1f}% {'>' if gold_move else '<'} 3%"
    triggers["gold_move"] = {
        "active": gold_move,
        "detail": gold_detail,
        "search": f'"Gold price {month_year} movement reason"',
    }

    # Nifty drawdown (>5% below 52-week high)
    nifty_drawdown = False
    nifty_dd_detail = "N/A"
    try:
        nifty_1y = fetch_yf("^NSEI", period="1y", interval="1d")
        if not nifty_1y.empty:
            high_52w = float(nifty_1y["High"].max())
            last_close = float(nifty_1y["Close"].iloc[-1])
            dd_pct = (1 - last_close / high_52w) * 100
            nifty_drawdown = dd_pct > 5
            nifty_dd_detail = f"{dd_pct:.1f}% below 52w high {'>' if nifty_drawdown else '<'} 5%"
    except Exception:
        pass
    triggers["nifty_drawdown"] = {
        "active": nifty_drawdown,
        "detail": nifty_dd_detail,
        "search": f'"Nifty correction {month_year} reason analysis"',
    }

    # Sector spike (>3% 1-day move in any sector)
    spiked_sectors = []
    for sym, name in SECTOR_INDICES.items():
        df = sector_data.get(sym)
        if df is None or df.empty or len(df) < 2:
            continue
        close = df["Close"].dropna()
        chg = _pct_change(close, 1)
        if chg is not None and abs(chg) > 3:
            spiked_sectors.append(f"{name} ({chg:+.1f}%)")
    triggers["sector_spike"] = {
        "active": len(spiked_sectors) > 0,
        "detail": ", ".join(spiked_sectors) if spiked_sectors else "no sector >3%",
        "search": f'"NSE sector movement {month_year} reason"',
    }

    # Big movers (stocks >5% in either direction)
    big_movers = []
    for m in movers:
        if m["chg_1d"] is not None and abs(m["chg_1d"]) > 5:
            big_movers.append(f"{m['symbol']} ({m['chg_1d']:+.1f}%)")
    triggers["big_movers"] = {
        "active": len(big_movers) > 0,
        "detail": ", ".join(big_movers) if big_movers else "no stock >5%",
        "search": "need news verification for each stock",
    }

    return triggers


# ── Backtest Date Range ──────────────────────────────────────────────────

def _compute_backtest_range():
    """Compute start/end dates for last 5 trading days backtest.

    End = last completed trading day. Start = 7 calendar days before end.
    """
    now = datetime.now(IST)
    today = now.date()

    # End date: last completed trading day
    # If it's a weekday and market hours are done (after 15:30), use today
    if today.weekday() < 5 and now.hour >= 16:
        end = today
    else:
        # Go back to most recent completed weekday
        d = today - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        end = d

    start = end - timedelta(days=7)
    return start, end


# ── Markdown Renderer ────────────────────────────────────────────────────

def _render_table(headers, rows, alignments=None):
    """Render a markdown table from headers and list-of-lists rows."""
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    sep = "|"
    for i, h in enumerate(headers):
        if alignments and i < len(alignments) and alignments[i] == "right":
            sep += "------:|"
        else:
            sep += "-------|"
    lines.append(sep)
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def _build_markdown(data):
    """Build full markdown report from collected data dict."""
    now = datetime.now(IST)
    lines = []

    lines.append(f"# Market Data Report — {now.strftime('%Y-%m-%d %H:%M')} IST")
    lines.append("")

    # Global Indices
    lines.append("## Global Indices")
    rows = []
    for r in data["global_indices"]:
        rows.append([r["name"], _fmt_price(r["close"]), _fmt_pct(r["chg_1d"]), _fmt_pct(r["chg_5d"])])
    lines.append(_render_table(["Index", "Close", "1D %", "5D %"], rows))
    lines.append("")

    # India Markets
    lines.append("## India Markets")
    rows = []
    for r in data["india_markets"]:
        rows.append([r["name"], _fmt_price(r["close"]), _fmt_pct(r["chg_1d"]), _fmt_pct(r["chg_5d"])])
    lines.append(_render_table(["Index", "Close", "1D %", "5D %"], rows))
    lines.append("")

    # Sector Indices
    lines.append("## Sector Indices")
    rows = []
    for r in data["sector_indices"]:
        rows.append([r["name"], _fmt_price(r["close"]), _fmt_pct(r["chg_1d"]), _fmt_pct(r["chg_5d"])])
    lines.append(_render_table(["Sector", "Close", "1D %", "5D %"], rows))
    lines.append("")

    # Commodities & FX
    lines.append("## Commodities & FX")
    rows = []
    for r in data["commodities_fx"]:
        rows.append([r["name"], _fmt_price(r["close"]), _fmt_pct(r["chg_1d"]), _fmt_pct(r["chg_5d"])])
    lines.append(_render_table(["Instrument", "Close", "1D %", "5D %"], rows))
    lines.append("")

    # FII Flow Proxy
    lines.append("## FII Flow Proxy (Nifty BeES)")
    if data["fii_proxy_rows"]:
        rows = []
        for r in data["fii_proxy_rows"]:
            rows.append([r["date"], _fmt_price(r["close"]), f"{r['volume']:,}", r["vol_vs_median"]])
        lines.append(_render_table(["Date", "Close", "Volume", "Vol vs Median"], rows))
    else:
        lines.append("*Data unavailable*")
    lines.append("")
    lines.append(f"Flow estimate: **{data['fii_flow']}**")
    lines.append("")

    # Universe Movers
    lines.append("## Universe Movers")
    movers = data["movers"]

    lines.append("### Top 5 Gainers (1D)")
    gainers = [m for m in movers if m["chg_1d"] is not None and m["chg_1d"] > 0][:5]
    if gainers:
        rows = []
        for m in gainers:
            rows.append([m["symbol"], m["sector"], _fmt_price(m["close"]),
                         _fmt_pct(m["chg_1d"]), _fmt_pct(m["chg_5d"])])
        lines.append(_render_table(["Stock", "Sector", "Close", "1D %", "5D %"], rows))
    else:
        lines.append("*No gainers*")
    lines.append("")

    lines.append("### Top 5 Losers (1D)")
    losers = [m for m in reversed(movers) if m["chg_1d"] is not None and m["chg_1d"] < 0][:5]
    if losers:
        rows = []
        for m in losers:
            rows.append([m["symbol"], m["sector"], _fmt_price(m["close"]),
                         _fmt_pct(m["chg_1d"]), _fmt_pct(m["chg_5d"])])
        lines.append(_render_table(["Stock", "Sector", "Close", "1D %", "5D %"], rows))
    else:
        lines.append("*No losers*")
    lines.append("")

    # Stocks requiring news verification
    big = [m for m in movers if m["chg_1d"] is not None and abs(m["chg_1d"]) > 5]
    lines.append("### Stocks Requiring News Verification (>5% move)")
    if big:
        for m in big:
            lines.append(f"- **{m['symbol']}**: {_fmt_pct(m['chg_1d'])} — CHECK NEWS BEFORE TRADING")
    else:
        lines.append("*No stocks with >5% move*")
    lines.append("")

    # Conditional Search Triggers
    lines.append("## Conditional Search Triggers")
    trigger_names = {
        "vix_elevated": "VIX elevated",
        "vix_spike": "VIX spike",
        "brent_move": "Brent crude move",
        "usdinr_move": "USD/INR move",
        "gold_move": "Gold move",
        "nifty_drawdown": "Nifty drawdown",
        "sector_spike": "Sector spike",
        "big_movers": "Big movers",
    }
    for key, label in trigger_names.items():
        t = data["triggers"].get(key, {})
        active = t.get("active", False)
        check = "[x]" if active else "[ ]"
        detail = t.get("detail", "N/A")
        search = t.get("search", "")
        if active:
            lines.append(f"- {check} **{label}** ({detail}) — search: {search}")
        else:
            lines.append(f"- {check} {label} ({detail}) — no search needed")
    lines.append("")

    # Backtest Date Range
    start, end = data["backtest_range"]
    lines.append("## Backtest Date Range")
    lines.append("```")
    lines.append(f"python -m intraday.backtest --start {start} --end {end}")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Market Data Report for trade plan")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of markdown")
    args = parser.parse_args()

    _stderr("\n  Market Data Report")
    _stderr("  " + "=" * 40)

    # 1. Global indices
    _stderr("  [1/6] Fetching global indices...")
    global_data = _batch_fetch(GLOBAL_INDICES, period="5d", interval="1d")
    global_rows = _extract_table_rows(global_data, GLOBAL_INDICES)

    # 2. India markets
    _stderr("  [2/6] Fetching India markets...")
    india_data = _batch_fetch(INDIA_MARKETS, period="5d", interval="1d")
    india_rows = _extract_table_rows(india_data, INDIA_MARKETS)

    # 3. Sector indices
    _stderr("  [3/6] Fetching sector indices...")
    sector_data = _batch_fetch(SECTOR_INDICES, period="5d", interval="1d")
    sector_rows = _extract_table_rows(sector_data, SECTOR_INDICES)

    # 4. Commodities & FX
    _stderr("  [4/6] Fetching commodities & FX...")
    commodities_data = _batch_fetch(COMMODITIES_FX, period="5d", interval="1d")
    commodities_rows = _extract_table_rows(commodities_data, COMMODITIES_FX)

    # 5. FII flow proxy
    _stderr("  [5/6] Fetching FII flow proxy...")
    fii_rows, fii_flow = _fetch_fii_proxy()

    # 6. Universe movers
    _stderr("  [6/6] Fetching universe movers...")
    movers = _fetch_universe_movers()

    # Computed: triggers
    _stderr("  Computing conditional triggers...")
    triggers = _compute_triggers(india_data, sector_data, commodities_data, movers)

    # Computed: backtest range
    backtest_range = _compute_backtest_range()

    # Assemble all data
    report_data = {
        "timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
        "global_indices": global_rows,
        "india_markets": india_rows,
        "sector_indices": sector_rows,
        "commodities_fx": commodities_rows,
        "fii_proxy_rows": fii_rows,
        "fii_flow": fii_flow,
        "movers": movers,
        "triggers": triggers,
        "backtest_range": (str(backtest_range[0]), str(backtest_range[1])),
    }

    # Output
    if args.json:
        output = json.dumps(report_data, indent=2, default=str)
    else:
        output = _build_markdown(report_data)

    print(output)

    # Save to file
    INTRADAY_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(IST)
    fname = f"market_data_{now.strftime('%Y-%m-%d_%H%M')}.md"
    path = INTRADAY_REPORT_DIR / fname
    # Always save as markdown regardless of stdout format
    md_output = _build_markdown(report_data) if args.json else output
    path.write_text(md_output)
    _stderr(f"\n  Report saved: {path}")
    _stderr("  Done.\n")


if __name__ == "__main__":
    main()
