"""
Upstox API integration — auth, token management (Supabase-backed), REST data.

Token lifecycle:
  1. User visits auth URL → logs in → Upstox redirects to callback with code
  2. exchange_auth_code() trades code for access_token (valid ~22 hrs)
  3. Token stored in Supabase `upstox_tokens` table + local fallback file
  4. All API calls use get_access_token() which checks freshness
"""

import json
import os
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env.local")

TABLE = "upstox_tokens"
LOCAL_TOKEN_PATH = Path.home() / ".upstox_token.json"

IST = timezone(timedelta(hours=5, minutes=30))


# ── Environment ──────────────────────────────────────────────────────────

def _load_env() -> tuple[str, str, str]:
    """Read Upstox credentials from environment."""
    api_key = os.environ.get("UPSTOX_API_KEY", "")
    api_secret = os.environ.get("UPSTOX_API_SECRET", "")
    callback_url = os.environ.get("UPSTOX_CALL_BACK_URL", "")
    return api_key, api_secret, callback_url


# ── Auth URL ─────────────────────────────────────────────────────────────

def get_auth_url() -> str:
    """Build the Upstox OAuth authorization URL."""
    api_key, _, callback_url = _load_env()
    return (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?client_id={api_key}"
        f"&redirect_uri={callback_url}"
        f"&response_type=code"
    )


# ── Token Persistence (Supabase + local fallback) ───────────────────────

def _save_local(token: str):
    """Write token to local fallback file."""
    LOCAL_TOKEN_PATH.write_text(json.dumps({
        "access_token": token,
        "created_at": datetime.now(IST).isoformat(),
    }))


def _load_local() -> str | None:
    """Load token from local fallback file. Returns None if expired/missing."""
    if not LOCAL_TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(LOCAL_TOKEN_PATH.read_text())
        created = datetime.fromisoformat(data["created_at"])
        if datetime.now(IST) - created > timedelta(hours=22):
            return None
        return data["access_token"]
    except Exception:
        return None


def save_access_token(token: str):
    """Upsert token to Supabase + local fallback."""
    _save_local(token)
    try:
        from common.db import _get_cursor
        cur = _get_cursor()
        cur.execute(
            f"INSERT INTO {TABLE} (access_token) VALUES (%s) RETURNING id",
            [token],
        )
        cur.fetchone()
    except Exception as e:
        print(f"  [WARN] Supabase token save failed (local fallback OK): {e}")


def get_access_token() -> str | None:
    """Get valid access token. Tries Supabase first, falls back to local file.

    Returns None if no valid (< 22 hrs old) token exists.
    """
    # Try Supabase
    try:
        from common.db import _select
        rows = _select(
            TABLE, "access_token, created_at",
            order="created_at DESC", limit=1,
        )
        if rows:
            row = rows[0]
            created = row["created_at"]
            if isinstance(created, str):
                created = datetime.fromisoformat(created)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - created
            if age < timedelta(hours=22):
                return row["access_token"]
    except Exception:
        pass

    # Fall back to local file
    return _load_local()


# ── Code Exchange ────────────────────────────────────────────────────────

def exchange_auth_code(code: str) -> str | None:
    """Exchange OAuth auth code for access token. Saves token on success."""
    import upstox_client

    api_key, api_secret, callback_url = _load_env()

    try:
        api = upstox_client.LoginApi()
        response = api.token(
            api_version="2.0",
            code=code,
            client_id=api_key,
            client_secret=api_secret,
            redirect_uri=callback_url,
            grant_type="authorization_code",
        )
        token = response.access_token
        if token:
            save_access_token(token)
            return token
    except Exception as e:
        print(f"  [ERROR] Token exchange failed: {e}")
    return None


# ── API Client ───────────────────────────────────────────────────────────

def get_api_client():
    """Return a configured Upstox ApiClient, or None if no valid token."""
    import upstox_client

    token = get_access_token()
    if not token:
        return None

    config = upstox_client.Configuration()
    config.access_token = token
    return upstox_client.ApiClient(config)


def is_upstox_available() -> bool:
    """True if valid token + env vars present."""
    api_key, api_secret, _ = _load_env()
    if not api_key or not api_secret:
        return False
    return get_access_token() is not None


# ── REST Data Functions ──────────────────────────────────────────────────

def _candles_to_df(candles: list) -> pd.DataFrame:
    """Convert Upstox candle list [[ts, O, H, L, C, V, OI], ...] → DataFrame."""
    if not candles:
        return pd.DataFrame()

    rows = []
    for c in candles:
        ts = c[0]
        if isinstance(ts, str):
            dt = pd.to_datetime(ts)
        else:
            dt = pd.to_datetime(ts)
        rows.append({
            "Datetime": dt,
            "Open": float(c[1]),
            "High": float(c[2]),
            "Low": float(c[3]),
            "Close": float(c[4]),
            "Volume": int(c[5]),
        })

    df = pd.DataFrame(rows)
    df = df.set_index("Datetime").sort_index()
    # Localize to IST if naive
    if df.index.tz is None:
        df.index = df.index.tz_localize(IST)
    else:
        df.index = df.index.tz_convert(IST)
    return df


def fetch_upstox_intraday(instrument_key: str, interval_min: int = 5) -> pd.DataFrame:
    """Fetch today's intraday candles from Upstox REST API."""
    import upstox_client

    client = get_api_client()
    if not client:
        return pd.DataFrame()

    try:
        api = upstox_client.HistoryV3Api(client)
        response = api.get_intra_day_candle_data(
            instrument_key, "minutes", str(interval_min),
        )
        candles = response.data.candles if response.data else []
        return _candles_to_df(candles)
    except Exception as e:
        print(f"  [WARN] Upstox intraday fetch failed for {instrument_key}: {e}")
        return pd.DataFrame()


def fetch_upstox_historical(
    instrument_key: str,
    from_date: str,
    to_date: str,
    unit: str = "days",
    interval: int = 1,
) -> pd.DataFrame:
    """Fetch historical candles from Upstox REST API (V3).

    Args:
        instrument_key: Upstox instrument key (e.g. "NSE_EQ|INE002A01018")
        from_date: Start date "YYYY-MM-DD"
        to_date: End date "YYYY-MM-DD"
        unit: V3 units — "days", "weeks", "months", "hours", or "minutes"
        interval: 1 for daily/weekly/monthly, or minute/hour interval
    """
    import upstox_client

    client = get_api_client()
    if not client:
        return pd.DataFrame()

    try:
        api = upstox_client.HistoryV3Api(client)
        response = api.get_historical_candle_data1(
            instrument_key, unit, str(interval), to_date, from_date,
        )
        candles = response.data.candles if response.data else []
        return _candles_to_df(candles)
    except Exception as e:
        print(f"  [WARN] Upstox historical fetch failed for {instrument_key}: {e}")
        return pd.DataFrame()


def fetch_upstox_ltp(instrument_keys: list[str]) -> dict[str, float]:
    """Fetch last traded price for multiple instruments.

    Returns {instrument_key: ltp} dict.
    """
    import upstox_client

    client = get_api_client()
    if not client:
        return {}

    try:
        api = upstox_client.MarketQuoteV3Api(client)
        keys_str = ",".join(instrument_keys)
        response = api.get_ltp(keys_str)
        result = {}
        if response.data:
            for key, quote in response.data.items():
                if hasattr(quote, "last_price") and quote.last_price is not None:
                    result[key] = float(quote.last_price)
        return result
    except Exception as e:
        print(f"  [WARN] Upstox LTP fetch failed: {e}")
        return {}


# ── CLI for interactive token acquisition ────────────────────────────────

if __name__ == "__main__":
    url = get_auth_url()
    print(f"\nUpstox Auth URL:\n  {url}\n")

    try:
        webbrowser.open(url)
        print("Opening browser... (or paste URL manually)\n")
    except Exception:
        print("Could not open browser. Paste the URL above into your browser.\n")

    code = input("Paste the auth code from callback URL: ").strip()
    if not code:
        print("No code provided. Exiting.")
        raise SystemExit(1)

    token = exchange_auth_code(code)
    if token:
        print(f"\nToken saved! Valid until tomorrow morning.")
        print(f"  Available: {is_upstox_available()}")
    else:
        print("\nFailed to exchange code. Check credentials and try again.")
        raise SystemExit(1)
