#!/usr/bin/env python3
"""
Trade Universe Builder — systematic stock selection from Upstox MTF instruments.

Pipeline:
  1. Download MTF instrument list (Upstox CDN, auth optional)
  2. Batch-fetch 1-month daily OHLCV via yfinance for all MTF equities
  3. Compute per-stock: last price, 20-day ADTV (₹ crore), 14-day ATR%
  4. Apply strategy-tier filters (scalp / intraday / btst)
  5. Fetch sector classification for qualifying stocks
  6. Apply sector cap (max 25% from any single sector per tier)
  7. Write common/universe.yaml + common/universe_guide.md

Run weekly to refresh:
    python -m common.universe
    python -m common.universe --force   # skip cache, re-download everything
    python -m common.universe --dry-run # show what would change without writing

Design principles (from research):
  - MTF eligibility = NSE Group 1 = traded ≥80% of days, impact cost ≤1%
  - ADTV (avg daily traded value) is the primary liquidity gate
  - ATR% normalizes volatility across price levels
  - Sector cap prevents concentration (max 25% per sector per tier)
  - Three tiers with different thresholds match scanner needs
"""

import argparse
import gzip
import json
import sys
import time
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml
import yfinance as yf

from common.data import PROJECT_ROOT

warnings.filterwarnings("ignore")

IST = ZoneInfo("Asia/Kolkata")

# ── URLs & Paths ──────────────────────────────────────────────────────────

MTF_URL = "https://assets.upstox.com/market-quote/instruments/exchange/MTF.json.gz"
MTF_LOCAL_CACHE = Path.home() / ".upstox_mtf.json"
UNIVERSE_PATH = PROJECT_ROOT / "common" / "universe.yaml"
GUIDE_PATH = PROJECT_ROOT / "common" / "universe_guide.md"
SECTOR_CACHE_PATH = Path.home() / ".universe_sectors.json"

# ── Strategy Tier Thresholds ──────────────────────────────────────────────
# Derived from research: institutional liquidity studies, NSE F&O criteria,
# ATR-based volatility filtering best practices.

TIERS = {
    "scalp": {
        "min_price": 100,
        "max_price": 5000,
        "min_adtv_cr": 15,       # ₹15 Cr ADTV — tight spreads needed
        "min_atr_pct": 1.5,
        "max_atr_pct": 5.0,
        "target_size": 80,       # aim for ~80 stocks
        "sector_cap_pct": 25,
    },
    "intraday": {
        "min_price": 50,
        "max_price": 10000,
        "min_adtv_cr": 8,        # ₹8 Cr ADTV — adequate for moderate positions
        "min_atr_pct": 1.5,
        "max_atr_pct": 7.0,
        "target_size": 120,
        "sector_cap_pct": 25,
    },
    "btst": {
        "min_price": 50,
        "max_price": 10000,
        "min_adtv_cr": 4,        # ₹4 Cr ADTV — longer hold tolerates lower liquidity
        "min_atr_pct": 1.0,
        "max_atr_pct": 6.0,
        "target_size": 150,
        "sector_cap_pct": 25,
    },
}

# NSE sector index mapping for sector classification
SECTOR_INDEX_MAP = {
    "Financial Services": "^CNXFIN",
    "Energy": "^CNXENERGY",
    "Basic Materials": "^CNXMETAL",
    "Industrials": "^CNXINFRA",
    "Technology": "^CNXIT",
    "Consumer Cyclical": "^CNXAUTO",
    "Consumer Defensive": "^CNXFMCG",
    "Healthcare": "^CNXPHARMA",
    "Real Estate": "^CNXREALTY",
    "Communication Services": "^CNXIT",
    "Utilities": "^CNXENERGY",
}

# Fallback: map common yfinance industry keywords to sectors
INDUSTRY_SECTOR_FALLBACK = {
    "bank": "^CNXFIN", "insurance": "^CNXFIN", "finance": "^CNXFIN",
    "capital market": "^CNXFIN", "asset management": "^CNXFIN",
    "oil": "^CNXENERGY", "gas": "^CNXENERGY", "power": "^CNXENERGY",
    "coal": "^CNXENERGY", "solar": "^CNXENERGY", "renewable": "^CNXENERGY",
    "steel": "^CNXMETAL", "mining": "^CNXMETAL", "metal": "^CNXMETAL",
    "aluminum": "^CNXMETAL", "copper": "^CNXMETAL", "cement": "^CNXMETAL",
    "defense": "^CNXPSE", "defence": "^CNXPSE", "aerospace": "^CNXPSE",
    "railway": "^CNXPSE", "shipbuilding": "^CNXPSE",
    "construction": "^CNXINFRA", "infrastructure": "^CNXINFRA",
    "engineering": "^CNXINFRA", "cable": "^CNXINFRA", "electrical": "^CNXINFRA",
    "port": "^CNXINFRA", "logistics": "^CNXINFRA",
    "software": "^CNXIT", "information technology": "^CNXIT", "it ": "^CNXIT",
    "consulting": "^CNXIT",
    "auto": "^CNXAUTO", "vehicle": "^CNXAUTO", "tyre": "^CNXAUTO",
    "battery": "^CNXAUTO",
    "food": "^CNXFMCG", "beverage": "^CNXFMCG", "personal": "^CNXFMCG",
    "household": "^CNXFMCG", "tobacco": "^CNXFMCG", "consumer": "^CNXFMCG",
    "apparel": "^CNXFMCG", "retail": "^CNXFMCG",
    "pharma": "^CNXPHARMA", "drug": "^CNXPHARMA", "healthcare": "^CNXPHARMA",
    "hospital": "^CNXPHARMA", "diagnostic": "^CNXPHARMA",
    "biotech": "^CNXPHARMA",
    "real estate": "^CNXREALTY", "housing": "^CNXREALTY", "property": "^CNXREALTY",
}


def _stderr(msg):
    print(msg, file=sys.stderr)


# ── Supabase Cache Layer ─────────────────────────────────────────────────
# Caches computed metrics (price, ADTV, ATR%) and sector info in Supabase.
# First run fetches everything from yfinance (~3-5 min for 1400 stocks).
# Subsequent runs load from cache and only re-fetch stale data.

def _supa_ok():
    """Check if Supabase is reachable."""
    try:
        from common.db import _supabase_available
        return _supabase_available()
    except Exception:
        return False


def _ensure_universe_tables():
    """Create universe cache tables if they don't exist."""
    if not _supa_ok():
        return
    try:
        from common.db import _get_cursor
        cur = _get_cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS universe_metrics (
                symbol TEXT PRIMARY KEY,
                last_price REAL,
                adtv_cr REAL,
                atr_pct REAL,
                avg_volume BIGINT,
                computed_date DATE DEFAULT CURRENT_DATE
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS universe_sectors (
                symbol TEXT PRIMARY KEY,
                sector TEXT,
                name TEXT,
                yf_sector TEXT,
                yf_industry TEXT,
                fetched_date DATE DEFAULT CURRENT_DATE
            )
        """)
    except Exception as e:
        _stderr(f"  [WARN] Could not create universe tables: {e}")


def _load_cached_metrics(max_age_days=1):
    """Load fresh metrics from Supabase. Returns {symbol: dict} or empty."""
    if not _supa_ok():
        return {}
    try:
        from common.db import _get_cursor
        cur = _get_cursor()
        cur.execute(
            "SELECT symbol, last_price, adtv_cr, atr_pct, avg_volume "
            "FROM universe_metrics WHERE computed_date >= CURRENT_DATE - %s",
            [max_age_days],
        )
        rows = cur.fetchall()
        if not rows:
            return {}
        return {
            r[0]: {
                "symbol": r[0], "last_price": r[1], "adtv_cr": r[2],
                "atr_pct": r[3], "avg_volume": r[4],
            }
            for r in rows
        }
    except Exception:
        return {}


def _save_metrics_to_cache(metrics):
    """Bulk upsert computed metrics into Supabase."""
    if not metrics or not _supa_ok():
        return
    try:
        from common.db import _get_cursor
        cur = _get_cursor()
        batch_size = 200
        for i in range(0, len(metrics), batch_size):
            batch = metrics[i:i + batch_size]
            args = ",".join(
                cur.mogrify(
                    "(%s, %s, %s, %s, %s)",
                    (m["symbol"], m["last_price"], m["adtv_cr"],
                     m["atr_pct"], m["avg_volume"]),
                ).decode()
                for m in batch if m.get("atr_pct") is not None
            )
            if args:
                cur.execute(
                    f"INSERT INTO universe_metrics "
                    f"(symbol, last_price, adtv_cr, atr_pct, avg_volume) "
                    f"VALUES {args} "
                    f"ON CONFLICT (symbol) DO UPDATE SET "
                    f"last_price=EXCLUDED.last_price, adtv_cr=EXCLUDED.adtv_cr, "
                    f"atr_pct=EXCLUDED.atr_pct, avg_volume=EXCLUDED.avg_volume, "
                    f"computed_date=CURRENT_DATE"
                )
        _stderr(f"  Cached {len(metrics)} metrics to Supabase")
    except Exception as e:
        _stderr(f"  [WARN] Failed to cache metrics: {e}")


def _load_cached_sectors():
    """Load sector info from Supabase (valid for 30 days)."""
    if not _supa_ok():
        return {}
    try:
        from common.db import _get_cursor
        cur = _get_cursor()
        cur.execute(
            "SELECT symbol, sector, name, yf_sector, yf_industry "
            "FROM universe_sectors WHERE fetched_date >= CURRENT_DATE - 30"
        )
        rows = cur.fetchall()
        return {
            r[0]: {"sector": r[1], "name": r[2], "yf_sector": r[3], "yf_industry": r[4]}
            for r in rows
        }
    except Exception:
        return {}


def _save_sectors_to_cache(sector_info):
    """Bulk upsert sector info into Supabase."""
    if not sector_info or not _supa_ok():
        return
    try:
        from common.db import _get_cursor
        cur = _get_cursor()
        items = list(sector_info.items())
        batch_size = 200
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            args = ",".join(
                cur.mogrify(
                    "(%s, %s, %s, %s, %s)",
                    (sym, info["sector"], info.get("name", ""),
                     info.get("yf_sector", ""), info.get("yf_industry", "")),
                ).decode()
                for sym, info in batch
            )
            if args:
                cur.execute(
                    f"INSERT INTO universe_sectors "
                    f"(symbol, sector, name, yf_sector, yf_industry) "
                    f"VALUES {args} "
                    f"ON CONFLICT (symbol) DO UPDATE SET "
                    f"sector=EXCLUDED.sector, name=EXCLUDED.name, "
                    f"yf_sector=EXCLUDED.yf_sector, yf_industry=EXCLUDED.yf_industry, "
                    f"fetched_date=CURRENT_DATE"
                )
    except Exception as e:
        _stderr(f"  [WARN] Failed to cache sectors: {e}")


# ── Step 1: Download MTF instruments ─────────────────────────────────────

def _download_mtf(force=False):
    """Download MTF instrument list. Uses cache if < 24h old and not forced."""
    # Check cache
    if not force and MTF_LOCAL_CACHE.exists():
        try:
            data = json.loads(MTF_LOCAL_CACHE.read_text())
            cached_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
            if (datetime.now().date() - cached_date).days < 1:
                _stderr(f"  Using cached MTF data ({len(data['instruments'])} instruments)")
                return data["instruments"]
        except Exception:
            pass

    _stderr("  Downloading MTF instruments from Upstox...")

    # Try with auth first (if available), fallback to public URL
    instruments = None
    try:
        from common.upstox import get_access_token
        token = get_access_token()
        if token:
            req = urllib.request.Request(MTF_URL)
            req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Accept-Encoding", "gzip")
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                try:
                    instruments = json.loads(gzip.decompress(raw))
                except Exception:
                    instruments = json.loads(raw)
    except Exception as e:
        _stderr(f"  Auth download failed ({e}), trying public URL...")

    if instruments is None:
        try:
            with urllib.request.urlopen(MTF_URL, timeout=30) as resp:
                raw = resp.read()
                try:
                    instruments = json.loads(gzip.decompress(raw))
                except Exception:
                    instruments = json.loads(raw)
        except Exception as e:
            _stderr(f"  [ERROR] Failed to download MTF list: {e}")
            # Try local downloaded file as last resort
            local_path = Path.home() / "Downloads" / "MTF.json"
            if local_path.exists():
                _stderr(f"  Falling back to {local_path}")
                instruments = json.loads(local_path.read_text())
            else:
                return []

    # Filter to NSE equities only
    equities = [
        item for item in instruments
        if item.get("segment") == "NSE_EQ"
        and item.get("instrument_type") == "EQ"
        and item.get("security_type") == "NORMAL"
        and item.get("mtf_enabled", False)
    ]

    # Cache
    MTF_LOCAL_CACHE.write_text(json.dumps({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "instruments": equities,
    }))

    _stderr(f"  Downloaded {len(equities)} NSE equities from MTF list")
    return equities


# ── Step 2: Batch fetch OHLCV ────────────────────────────────────────────

def _batch_fetch_ohlcv(symbols, period="1mo", interval="1d", batch_size=50):
    """Batch-fetch daily OHLCV for all symbols via yfinance.

    Returns {symbol: DataFrame} dict.
    """
    all_data = {}
    total = len(symbols)

    for i in range(0, total, batch_size):
        batch = symbols[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        _stderr(f"  Fetching OHLCV batch {batch_num}/{total_batches} ({len(batch)} tickers)...")

        try:
            raw = yf.download(
                batch, period=period, interval=interval,
                progress=False, threads=True,
            )
            if raw.empty:
                continue

            if len(batch) == 1:
                sym = batch[0]
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.droplevel("Ticker")
                all_data[sym] = raw
            else:
                for sym in batch:
                    try:
                        df = raw.xs(sym, level="Ticker", axis=1)
                        df = df.dropna(how="all")
                        if not df.empty:
                            all_data[sym] = df
                    except (KeyError, TypeError):
                        pass
        except Exception as e:
            _stderr(f"  [WARN] Batch {batch_num} failed: {e}")

        # Rate-limit courtesy delay between batches
        if i + batch_size < total:
            time.sleep(2)

    return all_data


# ── Step 3: Compute metrics ──────────────────────────────────────────────

def _compute_metrics(ohlcv_dict):
    """Compute per-stock: last_price, adtv_cr, atr_pct.

    Returns list of dicts with metrics.
    """
    results = []
    for sym, df in ohlcv_dict.items():
        if df.empty or len(df) < 10:
            continue

        close = df["Close"].dropna()
        high = df["High"].dropna()
        low = df["Low"].dropna()
        volume = df["Volume"].dropna()

        if len(close) < 10:
            continue

        last_price = float(close.iloc[-1])
        if last_price <= 0:
            continue

        # ADTV in crores (last 20 trading days)
        lookback = min(20, len(close))
        avg_price = float(close.tail(lookback).mean())
        avg_volume = float(volume.tail(lookback).mean())
        adtv_cr = (avg_price * avg_volume) / 1e7  # 1 crore = 1e7

        # ATR% (14-period ATR / price × 100)
        if len(df) >= 14:
            tr_data = pd.DataFrame({
                "hl": high - low,
                "hpc": abs(high - close.shift(1)),
                "lpc": abs(low - close.shift(1)),
            }).dropna()
            if len(tr_data) >= 14:
                tr = tr_data.max(axis=1)
                atr = float(tr.tail(14).mean())
                atr_pct = (atr / last_price) * 100
            else:
                atr_pct = None
        else:
            atr_pct = None

        # Average volume (for liquidity scoring later)
        avg_vol_20d = int(avg_volume)

        results.append({
            "symbol": sym,
            "last_price": round(last_price, 2),
            "adtv_cr": round(adtv_cr, 2),
            "atr_pct": round(atr_pct, 2) if atr_pct else None,
            "avg_volume": avg_vol_20d,
        })

    return results


# ── Step 4: Apply tier filters ───────────────────────────────────────────

def _apply_tier_filters(metrics, tier_name, thresholds):
    """Filter stocks by a strategy tier's thresholds.

    Returns list of qualifying stock dicts.
    """
    qualifying = []
    for m in metrics:
        price = m["last_price"]
        adtv = m["adtv_cr"]
        atr = m["atr_pct"]

        if price < thresholds["min_price"] or price > thresholds["max_price"]:
            continue
        if adtv < thresholds["min_adtv_cr"]:
            continue
        if atr is None:
            continue
        if atr < thresholds["min_atr_pct"] or atr > thresholds["max_atr_pct"]:
            continue

        qualifying.append(m)

    # Sort by ADTV descending (most liquid first)
    qualifying.sort(key=lambda x: x["adtv_cr"], reverse=True)
    return qualifying


# ── Step 5: Fetch sector classification ──────────────────────────────────

def _load_sector_cache():
    """Load cached sector mappings."""
    if SECTOR_CACHE_PATH.exists():
        try:
            data = json.loads(SECTOR_CACHE_PATH.read_text())
            cached_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
            # Sector cache valid for 30 days
            if (datetime.now().date() - cached_date).days < 30:
                return data["sectors"]
        except Exception:
            pass
    return {}


def _save_sector_cache(sectors):
    """Save sector mappings to cache."""
    SECTOR_CACHE_PATH.write_text(json.dumps({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "sectors": sectors,
    }, indent=2))


def _map_sector(yf_sector, yf_industry):
    """Map yfinance sector/industry to our NSE sector index."""
    if yf_sector and yf_sector in SECTOR_INDEX_MAP:
        return SECTOR_INDEX_MAP[yf_sector]

    # Fallback: keyword match on industry
    if yf_industry:
        industry_lower = yf_industry.lower()
        for keyword, sector in INDUSTRY_SECTOR_FALLBACK.items():
            if keyword in industry_lower:
                return sector

    return "^CNXINFRA"  # default bucket


def _fetch_sectors(symbols, max_workers=10):
    """Fetch sector for each symbol using yfinance. Uses cache + threading.

    Returns {yf_symbol: {"sector": "^CNX...", "name": "...", "industry": "..."}}
    """
    cache = _load_sector_cache()
    results = {}
    to_fetch = []

    for sym in symbols:
        if sym in cache:
            results[sym] = cache[sym]
        else:
            to_fetch.append(sym)

    if not to_fetch:
        return results

    _stderr(f"  Fetching sector info for {len(to_fetch)} stocks (cached: {len(results)})...")

    def _fetch_one(sym):
        # Retry with backoff — yfinance crumb/cookie can expire mid-session
        for attempt in range(3):
            try:
                # Throttle to avoid Yahoo rate limits
                time.sleep(0.3)
                info = yf.Ticker(sym).info
                if not info or info.get("trailingPegRatio") is None and not info.get("sector"):
                    if attempt < 2:
                        time.sleep(2 + attempt * 2)
                        continue
                sector = info.get("sector", "")
                industry = info.get("industry", "")
                name = info.get("shortName", "") or info.get("longName", sym.replace(".NS", ""))
                mapped = _map_sector(sector, industry)
                return sym, {
                    "sector": mapped,
                    "name": name,
                    "yf_sector": sector,
                    "yf_industry": industry,
                }
            except Exception:
                if attempt < 2:
                    time.sleep(2 + attempt * 2)
                    continue
        return sym, {
            "sector": "^CNXINFRA",
            "name": sym.replace(".NS", ""),
            "yf_sector": "",
            "yf_industry": "",
        }

    # Low concurrency (3 workers) + per-request throttle to stay under Yahoo rate limits
    effective_workers = min(max_workers, 3)
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {executor.submit(_fetch_one, sym): sym for sym in to_fetch}
        done = 0
        for future in as_completed(futures):
            sym, data = future.result()
            results[sym] = data
            cache[sym] = data
            done += 1
            if done % 50 == 0:
                _stderr(f"    ...{done}/{len(to_fetch)} sectors fetched")

    _save_sector_cache(cache)
    _stderr(f"  Sector info complete ({len(results)} stocks)")
    return results


# ── Step 6: Apply sector cap ─────────────────────────────────────────────

def _apply_sector_cap(stocks, sector_info, cap_pct=25):
    """Cap representation from any single sector.

    Keeps the most liquid stocks from each sector, drops excess.
    """
    max_per_sector = max(2, int(len(stocks) * cap_pct / 100))

    sector_counts = {}
    result = []

    for stock in stocks:
        sym = stock["symbol"]
        info = sector_info.get(sym, {})
        sector = info.get("sector", "^CNXINFRA")

        count = sector_counts.get(sector, 0)
        if count >= max_per_sector:
            continue

        sector_counts[sector] = count + 1
        result.append(stock)

    return result


# ── Step 7: Build universe YAML ──────────────────────────────────────────

SECTOR_FRIENDLY = {
    "^CNXFIN": "Financials", "^CNXENERGY": "Energy", "^CNXMETAL": "Metals",
    "^CNXPSE": "PSE/Defence", "^CNXINFRA": "Infrastructure", "^CNXIT": "IT",
    "^CNXAUTO": "Auto", "^CNXFMCG": "FMCG/Consumer", "^CNXREALTY": "Realty",
    "^CNXPHARMA": "Pharma",
}


def _build_universe_yaml(tier_results, sector_info, mtf_lookup, metrics_lookup):
    """Build the universe.yaml structure."""
    now = datetime.now(IST)

    # Collect all unique stocks across tiers
    all_symbols = set()
    for tier_stocks in tier_results.values():
        for s in tier_stocks:
            all_symbols.add(s["symbol"])

    stocks = {}
    for sym in sorted(all_symbols):
        info = sector_info.get(sym, {})
        mtf = mtf_lookup.get(sym.replace(".NS", ""), {})
        m = metrics_lookup.get(sym, {})

        eligible = {}
        for tier_name, tier_stocks in tier_results.items():
            eligible[tier_name] = any(s["symbol"] == sym for s in tier_stocks)

        stocks[sym] = {
            "name": info.get("name", sym.replace(".NS", "")),
            "sector": info.get("sector", "^CNXINFRA"),
            "instrument_key": mtf.get("instrument_key", ""),
            "isin": mtf.get("isin", ""),
            "adtv_cr": m.get("adtv_cr", 0),
            "atr_pct": m.get("atr_pct", 0),
            "price": m.get("last_price", 0),
            "avg_volume": m.get("avg_volume", 0),
            "eligible": eligible,
        }

    universe = {
        "generated": now.isoformat(),
        "source": "Upstox MTF + yfinance screening",
        "mtf_instruments_count": len(mtf_lookup),
        "total_stocks": len(stocks),
        "tier_counts": {tier: len(stocks_list) for tier, stocks_list in tier_results.items()},
        "tier_thresholds": TIERS,
        "stocks": stocks,
    }

    return universe


def _build_guide(universe, sector_info, tier_results):
    """Build human-readable universe_guide.md."""
    now = datetime.now(IST)
    lines = []
    lines.append(f"# Trade Universe Guide — {now.strftime('%Y-%m-%d %H:%M IST')}")
    lines.append("")
    lines.append(f"**Source:** Upstox MTF list ({universe['mtf_instruments_count']} instruments)")
    lines.append(f"**Total qualifying stocks:** {universe['total_stocks']}")
    lines.append(f"**Generated:** {now.strftime('%Y-%m-%d %H:%M IST')}")
    lines.append("")

    # Tier summary
    lines.append("## Tier Summary")
    lines.append("")
    lines.append("| Tier | Stocks | ADTV Min | Price Range | ATR% Range |")
    lines.append("|------|--------|----------|-------------|------------|")
    for tier_name, thresholds in TIERS.items():
        count = universe["tier_counts"].get(tier_name, 0)
        lines.append(
            f"| {tier_name.upper()} | {count} | "
            f"₹{thresholds['min_adtv_cr']} Cr | "
            f"₹{thresholds['min_price']}-{thresholds['max_price']} | "
            f"{thresholds['min_atr_pct']}-{thresholds['max_atr_pct']}% |"
        )
    lines.append("")

    # Per-tier stock lists
    for tier_name, tier_stocks in tier_results.items():
        lines.append(f"## {tier_name.upper()} Universe ({len(tier_stocks)} stocks)")
        lines.append("")

        # Group by sector
        by_sector = {}
        for s in tier_stocks:
            info = sector_info.get(s["symbol"], {})
            sector = info.get("sector", "^CNXINFRA")
            sector_name = SECTOR_FRIENDLY.get(sector, sector)
            by_sector.setdefault(sector_name, []).append(s)

        lines.append("| Sector | Count | Top Stocks (by ADTV) |")
        lines.append("|--------|-------|---------------------|")
        for sector_name in sorted(by_sector.keys()):
            stocks_in_sector = by_sector[sector_name]
            count = len(stocks_in_sector)
            top = stocks_in_sector[:3]
            top_str = ", ".join(
                f"{s['symbol'].replace('.NS', '')} (₹{s['adtv_cr']:.0f}Cr)"
                for s in top
            )
            lines.append(f"| {sector_name} | {count} | {top_str} |")
        lines.append("")

        # Full list
        lines.append("<details>")
        lines.append(f"<summary>Full {tier_name.upper()} list</summary>")
        lines.append("")
        lines.append("| Stock | Name | Sector | Price | ADTV (₹Cr) | ATR% |")
        lines.append("|-------|------|--------|-------|------------|------|")
        for s in tier_stocks:
            info = sector_info.get(s["symbol"], {})
            name = info.get("name", s["symbol"])
            sector = SECTOR_FRIENDLY.get(info.get("sector", ""), "Other")
            lines.append(
                f"| {s['symbol'].replace('.NS', '')} | {name} | {sector} | "
                f"₹{s['last_price']:,.0f} | {s['adtv_cr']:.1f} | "
                f"{s['atr_pct']:.1f}% |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Trade Universe Builder")
    parser.add_argument("--force", action="store_true",
                        help="Force re-download of MTF data and sector info")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show results without writing files")
    args = parser.parse_args()

    now = datetime.now(IST)
    _stderr(f"\n  Trade Universe Builder — {now.strftime('%Y-%m-%d %H:%M IST')}")
    _stderr("  " + "=" * 50)

    # Ensure Supabase tables exist
    _ensure_universe_tables()

    # Step 1: Download MTF instruments
    _stderr("\n[1/6] Loading MTF instruments...")
    mtf_data = _download_mtf(force=args.force)
    if not mtf_data:
        _stderr("  [FATAL] No MTF instruments available. Aborting.")
        sys.exit(1)

    # Build lookup tables
    mtf_lookup = {}  # trading_symbol → full mtf item
    yf_symbols = []
    for item in mtf_data:
        ts = item["trading_symbol"]
        mtf_lookup[ts] = item
        yf_symbols.append(f"{ts}.NS")

    _stderr(f"  {len(yf_symbols)} NSE equities to screen")

    # Step 2+3: Load cached metrics or fetch fresh
    _stderr("\n[2/6] Loading metrics (Supabase cache → yfinance fallback)...")
    cached_metrics = {} if args.force else _load_cached_metrics(max_age_days=1)

    if cached_metrics and len(cached_metrics) > len(yf_symbols) * 0.8:
        # Cache hit: most metrics are fresh
        _stderr(f"  Cache hit: {len(cached_metrics)} metrics from Supabase")
        metrics = list(cached_metrics.values())
        # Fetch only missing symbols
        cached_syms = set(cached_metrics.keys())
        missing = [s for s in yf_symbols if s not in cached_syms]
        if missing:
            _stderr(f"  Fetching {len(missing)} missing stocks...")
            ohlcv = _batch_fetch_ohlcv(missing, period="1mo", interval="1d", batch_size=50)
            new_metrics = _compute_metrics(ohlcv)
            metrics.extend(new_metrics)
            if new_metrics:
                _save_metrics_to_cache(new_metrics)
    else:
        # Cache miss: fetch everything
        _stderr("\n  Cache miss — fetching 1-month daily OHLCV for all stocks...")
        ohlcv = _batch_fetch_ohlcv(yf_symbols, period="1mo", interval="1d", batch_size=50)
        _stderr(f"  Got data for {len(ohlcv)}/{len(yf_symbols)} stocks")

        _stderr("\n[3/6] Computing metrics (price, ADTV, ATR%)...")
        metrics = _compute_metrics(ohlcv)
        _stderr(f"  {len(metrics)} stocks with valid metrics")

        # Save to cache
        _save_metrics_to_cache(metrics)

    # Build metrics lookup
    metrics_lookup = {m["symbol"]: m for m in metrics}

    # Step 4: Apply tier filters
    _stderr("\n[4/6] Applying tier filters...")
    tier_results = {}
    for tier_name, thresholds in TIERS.items():
        qualifying = _apply_tier_filters(metrics, tier_name, thresholds)
        _stderr(f"  {tier_name.upper()}: {len(qualifying)} stocks pass filters")
        tier_results[tier_name] = qualifying

    # Collect all unique symbols that pass any tier
    all_qualifying_symbols = set()
    for tier_stocks in tier_results.values():
        for s in tier_stocks:
            all_qualifying_symbols.add(s["symbol"])
    _stderr(f"  Total unique qualifying: {len(all_qualifying_symbols)}")

    # Step 5: Fetch sectors (Supabase cache → local cache → yfinance)
    _stderr("\n[5/6] Fetching sector classification...")
    if args.force:
        if SECTOR_CACHE_PATH.exists():
            SECTOR_CACHE_PATH.unlink()

    # Merge Supabase sector cache into local cache
    supa_sectors = {} if args.force else _load_cached_sectors()
    if supa_sectors:
        _stderr(f"  Loaded {len(supa_sectors)} sectors from Supabase cache")
        # Pre-populate local cache with Supabase data
        local_cache = _load_sector_cache()
        for sym, info in supa_sectors.items():
            if sym not in local_cache:
                local_cache[sym] = info
        _save_sector_cache(local_cache)

    sector_info = _fetch_sectors(sorted(all_qualifying_symbols), max_workers=10)

    # Save sectors to Supabase
    _save_sectors_to_cache(sector_info)

    # Apply sector cap per tier
    for tier_name in tier_results:
        before = len(tier_results[tier_name])
        tier_results[tier_name] = _apply_sector_cap(
            tier_results[tier_name], sector_info,
            TIERS[tier_name]["sector_cap_pct"],
        )
        after = len(tier_results[tier_name])
        if before != after:
            _stderr(f"  {tier_name.upper()}: {before} → {after} after sector cap")

    # Step 6: Build and write output
    _stderr("\n[6/6] Building universe files...")
    universe = _build_universe_yaml(tier_results, sector_info, mtf_lookup, metrics_lookup)
    guide = _build_guide(universe, sector_info, tier_results)

    if args.dry_run:
        _stderr("\n  [DRY RUN] Would write:")
        _stderr(f"    {UNIVERSE_PATH}")
        _stderr(f"    {GUIDE_PATH}")
        # Print summary to stdout
        print(guide)
    else:
        with open(UNIVERSE_PATH, "w") as f:
            yaml.dump(universe, f, default_flow_style=False, sort_keys=False,
                      allow_unicode=True, width=120)
        GUIDE_PATH.write_text(guide)
        _stderr(f"\n  Written: {UNIVERSE_PATH}")
        _stderr(f"  Written: {GUIDE_PATH}")

    # Print summary
    _stderr(f"\n  ── Summary ──")
    _stderr(f"  MTF instruments screened: {len(mtf_data)}")
    _stderr(f"  Valid OHLCV data: {len(ohlcv)}")
    _stderr(f"  Stocks with metrics: {len(metrics)}")
    for tier_name, tier_stocks in tier_results.items():
        _stderr(f"  {tier_name.upper()} universe: {len(tier_stocks)} stocks")

    # Sector distribution summary
    _stderr(f"\n  ── Sector Distribution (Intraday tier) ──")
    intraday_stocks = tier_results.get("intraday", [])
    by_sector = {}
    for s in intraday_stocks:
        info = sector_info.get(s["symbol"], {})
        sector = SECTOR_FRIENDLY.get(info.get("sector", ""), "Other")
        by_sector[sector] = by_sector.get(sector, 0) + 1
    for sector in sorted(by_sector, key=by_sector.get, reverse=True):
        _stderr(f"    {sector}: {by_sector[sector]}")

    _stderr("\n  Done.\n")


if __name__ == "__main__":
    main()
