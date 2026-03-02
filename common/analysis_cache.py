"""
Supabase-backed analysis cache for expensive computed metrics.

Persists VIX, regime classifications, hit rates, DOW/month stats, etc.
so multiple scanner runs (intraday, BTST, scalp) share results within
a trading day.

Table: analysis_cache
  (metric, symbol, params) PRIMARY KEY
  payload JSONB, computed_at TIMESTAMPTZ
"""

import json
from datetime import datetime, timezone

# ── TTL Constants ────────────────────────────────────────────────────────

TTL_MARKET = 1800       # 30 min — VIX, Nifty regime
TTL_FLOW = 3600         # 1 hour — institutional flow
TTL_DAILY = 86400       # 1 day — symbol regime, gap stats, DOW/month stats
TTL_EARNINGS = 86400    # 1 day — earnings proximity


# ── Core get/set ─────────────────────────────────────────────────────────

def get_cached(metric, symbol="", params="", max_age_seconds=TTL_MARKET):
    """Fetch a cached analysis result from Supabase.

    Returns deserialized payload (dict/list), or None if stale/missing.
    Silent fallback to None if Supabase is down.
    """
    try:
        from common.db import _get_cursor

        cur = _get_cursor()
        cur.execute(
            "SELECT payload, computed_at FROM analysis_cache "
            "WHERE metric = %s AND symbol = %s AND params = %s",
            [metric, symbol, params],
        )
        row = cur.fetchone()
        if not row:
            return None

        payload, computed_at = row

        # Check freshness
        if computed_at.tzinfo is None:
            computed_at = computed_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - computed_at).total_seconds()
        if age > max_age_seconds:
            return None

        # payload is already a dict/list from JSONB column
        if isinstance(payload, str):
            return json.loads(payload)
        return payload

    except Exception:
        return None


def _sanitize_for_json(obj):
    """Replace NaN/Infinity with None for valid JSON (PostgreSQL JSONB rejects NaN)."""
    import math

    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def set_cached(metric, payload, symbol="", params=""):
    """Upsert an analysis result into Supabase cache. Silent on failure."""
    try:
        from common.db import _get_cursor

        # Sanitize NaN/Infinity before JSON serialization
        if not isinstance(payload, str):
            sanitized = _sanitize_for_json(payload)
            payload_json = json.dumps(sanitized, default=str)
        else:
            payload_json = payload

        cur = _get_cursor()
        cur.execute(
            "INSERT INTO analysis_cache (metric, symbol, params, payload, computed_at) "
            "VALUES (%s, %s, %s, %s, NOW()) "
            "ON CONFLICT (metric, symbol, params) "
            "DO UPDATE SET payload = EXCLUDED.payload, computed_at = EXCLUDED.computed_at",
            [metric, symbol, params, payload_json],
        )
    except Exception:
        pass
