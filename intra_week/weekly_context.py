"""
Weekly context engine for IntraWeek scanner.

Provides day-of-week awareness, holiday proximity, F&O expiry detection,
and remaining trading days computation for the current week.
"""

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# ── NSE Holidays 2025-2026 ────────────────────────────────────────────────
# Source: NSE circular — update annually.

NSE_HOLIDAYS = {
    # 2025
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr
    date(2025, 4, 10),   # Shri Mahavir Jayanti
    date(2025, 4, 14),   # Dr. Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 6, 7),    # Bakri Id
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 16),   # Janmashtami
    date(2025, 10, 2),   # Mahatma Gandhi Jayanti
    date(2025, 10, 21),  # Diwali (Laxmi Pujan)
    date(2025, 10, 22),  # Diwali Balipratipada
    date(2025, 11, 5),   # Guru Nanak Jayanti
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1, 26),   # Republic Day
    date(2026, 2, 17),   # Mahashivratri
    date(2026, 3, 4),    # Holi
    date(2026, 3, 20),   # Id-Ul-Fitr
    date(2026, 3, 25),   # Shri Mahavir Jayanti
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 5, 28),   # Bakri Id
    date(2026, 8, 15),   # Independence Day
    date(2026, 8, 24),   # Janmashtami
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 9),   # Diwali (Laxmi Pujan)
    date(2026, 10, 10),  # Diwali Balipratipada
    date(2026, 10, 26),  # Guru Nanak Jayanti
    date(2026, 12, 25),  # Christmas
}


def _is_trading_day(d: date) -> bool:
    """Check if a date is a trading day (weekday + not NSE holiday)."""
    return d.weekday() < 5 and d not in NSE_HOLIDAYS


def _week_bounds(ref: date):
    """Return (Monday, Friday) of the week containing ref."""
    monday = ref - timedelta(days=ref.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday


def _last_thursday_of_month(year: int, month: int) -> date:
    """Return last Thursday of given month (F&O monthly expiry)."""
    # Find last day of month, walk back to Thursday
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last_day.weekday() - 3) % 7  # 3 = Thursday
    return last_day - timedelta(days=offset)


def is_expiry_week(ref_date: date | None = None) -> bool:
    """Check if current week contains monthly F&O expiry (last Thursday)."""
    ref = ref_date or datetime.now(IST).date()
    monday, friday = _week_bounds(ref)
    expiry = _last_thursday_of_month(ref.year, ref.month)
    # Also check if expiry shifted to Wednesday due to holiday
    if expiry in NSE_HOLIDAYS:
        expiry = expiry - timedelta(days=1)
    return monday <= expiry <= friday


def get_expiry_date(ref_date: date | None = None) -> date | None:
    """Return the F&O expiry date if this is an expiry week, else None."""
    ref = ref_date or datetime.now(IST).date()
    monday, friday = _week_bounds(ref)
    expiry = _last_thursday_of_month(ref.year, ref.month)
    if expiry in NSE_HOLIDAYS:
        expiry = expiry - timedelta(days=1)
    return expiry if monday <= expiry <= friday else None


def get_remaining_trading_days(ref_date: date | None = None) -> int:
    """Count trading days remaining in current week (including ref_date)."""
    ref = ref_date or datetime.now(IST).date()
    _, friday = _week_bounds(ref)
    count = 0
    d = ref
    while d <= friday:
        if _is_trading_day(d):
            count += 1
        d += timedelta(days=1)
    return count


def check_holiday_proximity(ref_date: date | None = None, days_ahead: int = 5):
    """Check if a market holiday is within days_ahead trading days.

    Returns (is_near_holiday, holiday_date, days_until).
    """
    ref = ref_date or datetime.now(IST).date()
    trading_days_checked = 0
    d = ref + timedelta(days=1)
    while trading_days_checked < days_ahead:
        if d.weekday() < 5:  # weekday
            trading_days_checked += 1
            if d in NSE_HOLIDAYS:
                return True, d, trading_days_checked
        d += timedelta(days=1)
    return False, None, 0


def get_weekly_context(ref_date: date | None = None) -> dict:
    """Build complete weekly context for the scanner.

    Returns dict with day_of_week, remaining_trading_days, holiday/expiry info, etc.
    """
    ref = ref_date or datetime.now(IST).date()
    monday, friday = _week_bounds(ref)

    # Holidays this week
    holidays_this_week = [d for d in NSE_HOLIDAYS if monday <= d <= friday]

    # Trading days in this week
    total_trading_days = sum(1 for i in range(5) if _is_trading_day(monday + timedelta(days=i)))
    remaining = get_remaining_trading_days(ref)

    # Holiday proximity
    near_holiday, holiday_date, days_until = check_holiday_proximity(ref)

    # Expiry
    expiry_date = get_expiry_date(ref)

    return {
        "ref_date": ref,
        "day_of_week": ref.weekday(),
        "day_name": calendar.day_name[ref.weekday()],
        "remaining_trading_days": remaining,
        "total_trading_days": total_trading_days,
        "is_holiday_week": len(holidays_this_week) > 0,
        "holidays_this_week": holidays_this_week,
        "is_expiry_week": expiry_date is not None,
        "expiry_date": expiry_date,
        "is_truncated_week": total_trading_days < 5,
        "week_start": monday,
        "week_end": friday,
        "near_holiday": near_holiday,
        "nearest_holiday": holiday_date,
        "days_until_holiday": days_until,
    }
