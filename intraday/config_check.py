"""CLI script to check staleness of trading configs."""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from common.data import TICKERS, PROJECT_ROOT, SCALP_CONFIG_PATH, INTRADAY_DIR

IST = ZoneInfo("Asia/Kolkata")

MLR_CONFIG_PATH = INTRADAY_DIR / "mlr_config.yaml"
MLR_STALE_DAYS = 3
SCALP_STALE_DAYS = 7


def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M IST")


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


# ── MLR config ───────────────────────────────────────────────────────────────

def check_mlr_config() -> dict:
    """Return dict with keys: exists, generated, age_days, ticker_count, stale."""
    result = {"exists": False, "generated": None, "age_days": None, "ticker_count": 0, "stale": False}

    if not MLR_CONFIG_PATH.exists():
        return result

    result["exists"] = True
    try:
        with open(MLR_CONFIG_PATH) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"  ⚠️  Failed to parse {MLR_CONFIG_PATH}: {e}")
        return result

    # Parse generated timestamp
    raw = data.get("generated")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            result["generated"] = dt
            result["age_days"] = (_now_ist() - dt).days
            result["stale"] = result["age_days"] >= MLR_STALE_DAYS
        except ValueError:
            pass

    # Count tickers
    tickers = data.get("tickers", {})
    if isinstance(tickers, dict):
        result["ticker_count"] = len(tickers)

    return result


# ── Scalp config ─────────────────────────────────────────────────────────────

def check_scalp_config() -> dict:
    """Return dict with keys: exists, generated, age_days, ticker_count, stale."""
    result = {"exists": False, "generated": None, "age_days": None, "ticker_count": 0, "stale": False}

    if not SCALP_CONFIG_PATH.exists():
        return result

    result["exists"] = True

    # Use file modification time as generation date (no metadata timestamp in file)
    mtime = os.path.getmtime(SCALP_CONFIG_PATH)
    dt = datetime.fromtimestamp(mtime, tz=IST)
    result["generated"] = dt
    result["age_days"] = (_now_ist() - dt).days
    result["stale"] = result["age_days"] >= SCALP_STALE_DAYS

    try:
        with open(SCALP_CONFIG_PATH) as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"  ⚠️  Failed to parse {SCALP_CONFIG_PATH}: {e}")
        return result

    tickers = data.get("tickers", [])
    if isinstance(tickers, list):
        result["ticker_count"] = len(tickers)
    elif isinstance(tickers, dict):
        result["ticker_count"] = len(tickers)

    return result


# ── Report───────────────────────────────────────────────────────────────────

def _age_label(age_days: int | None, threshold: int) -> str:
    if age_days is None:
        return "unknown age"
    unit = "day" if age_days == 1 else "days"
    if age_days >= threshold:
        return f"{age_days} {unit} — ⚠️ STALE (>= {threshold} days)"
    return f"{age_days} {unit} — ✅ FRESH (< {threshold} days)"


def _ticker_label(count: int, expected: int) -> str:
    if count == expected:
        return f"{count} — ✅ MATCHES common/data.py ({expected})"
    return f"{count} — ⚠️ MISMATCH (expected {expected} from common/data.py)"


def main() -> None:
    now = _now_ist()
    expected_tickers = len(TICKERS)

    mlr = check_mlr_config()
    scalp = check_scalp_config()

    issues_stale = 0
    issues_mismatch = 0
    actions: list[str] = []

    lines = [f"# Config Staleness Check — {_fmt_dt(now)}", ""]

    # ── MLR section ──
    lines.append(f"## MLR Config ({MLR_CONFIG_PATH.relative_to(PROJECT_ROOT)})")
    if not mlr["exists"]:
        lines.append(f"- ⚠️  File not found: {MLR_CONFIG_PATH}")
        lines.append("  → Run: `python -m intraday.mlr_config -v`")
        issues_stale += 1
        actions.append("`python -m intraday.mlr_config -v`")
    else:
        gen_str = _fmt_dt(mlr["generated"]) if mlr["generated"] else "unknown"
        lines.append(f"- Generated: {gen_str}")
        lines.append(f"- Age: {_age_label(mlr['age_days'], MLR_STALE_DAYS)}")
        if mlr["stale"]:
            lines.append("  → Run: `python -m intraday.mlr_config -v`")
            issues_stale += 1
            actions.append("`python -m intraday.mlr_config -v`")
        lines.append(f"- Tickers: {_ticker_label(mlr['ticker_count'], expected_tickers)}")
        if mlr["ticker_count"] != expected_tickers:
            lines.append("  → Run: `python -m intraday.mlr_config -v`")
            issues_mismatch += 1
            if "`python -m intraday.mlr_config -v`" not in actions:
                actions.append("`python -m intraday.mlr_config -v`")

    lines.append("")

    # ── Scalp section ──
    lines.append(f"## Scalp Config ({SCALP_CONFIG_PATH.relative_to(PROJECT_ROOT)})")
    if not scalp["exists"]:
        lines.append(f"- ⚠️  File not found: {SCALP_CONFIG_PATH}")
        lines.append("  → Run: `python -m scalp.config`")
        issues_stale += 1
        actions.append("`python -m scalp.config`")
    else:
        gen_str = _fmt_dt(scalp["generated"]) if scalp["generated"] else "unknown"
        lines.append(f"- Generated: {gen_str}")
        lines.append(f"- Age: {_age_label(scalp['age_days'], SCALP_STALE_DAYS)}")
        if scalp["stale"]:
            lines.append("  → Run: `python -m scalp.config`")
            issues_stale += 1
            if "`python -m scalp.config`" not in actions:
                actions.append("`python -m scalp.config`")
        lines.append(f"- Tickers: {_ticker_label(scalp['ticker_count'], expected_tickers)}")
        if scalp["ticker_count"] != expected_tickers:
            lines.append("  → Run: `python -m scalp.config`")
            issues_mismatch += 1
            if "`python -m scalp.config`" not in actions:
                actions.append("`python -m scalp.config`")

    lines.append("")

    # ── Summary ──
    lines.append("## Summary")
    if issues_stale == 0 and issues_mismatch == 0:
        lines.append("- All configs are fresh ✅")
        lines.append("- No ticker mismatches ✅")
    else:
        if issues_stale:
            unit = "config is" if issues_stale == 1 else "configs are"
            lines.append(f"- {issues_stale} {unit} stale")
        if issues_mismatch:
            unit = "ticker mismatch" if issues_mismatch == 1 else "ticker mismatches"
            lines.append(f"- {issues_mismatch} {unit} detected")
        if actions:
            lines.append("- Recommended actions:")
            for i, a in enumerate(actions, 1):
                lines.append(f"  {i}. {a}")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
