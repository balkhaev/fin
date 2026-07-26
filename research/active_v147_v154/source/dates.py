from __future__ import annotations

import calendar
from datetime import date, timedelta

from config import MONTH_CODES


def third_friday(year: int, month: int) -> date:
    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    fridays = [
        value
        for week in cal.monthdatescalendar(year, month)
        for value in week
        if value.month == month and value.weekday() == calendar.FRIDAY
    ]
    return fridays[2]


def next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def nominal_vix_expiry(year: int, month: int) -> date:
    """Return the normal monthly VIX futures Wednesday.

    Cboe monthly VX settlement is normally 30 days before the third Friday of
    the following month. Holiday adjustments are resolved by probing adjacent
    official Cboe settlement-file dates rather than guessed locally.
    """

    following_year, following_month = next_month(year, month)
    return third_friday(following_year, following_month) - timedelta(days=30)


def expiry_url_candidates(year: int, month: int) -> tuple[date, ...]:
    nominal = nominal_vix_expiry(year, month)
    candidates: list[date] = []
    # Normal Wednesday first; Tuesday is the common holiday adjustment. The
    # remaining nearby dates make the collector robust to unusual calendars.
    for offset in (0, -1, 1, -2, 2):
        value = nominal + timedelta(days=offset)
        if value not in candidates:
            candidates.append(value)
    return tuple(candidates)


def archive_contract_code(year: int, month: int) -> str:
    return f"{MONTH_CODES[month - 1]}{year % 100:02d}"


def self_test() -> None:
    assert third_friday(2023, 2) == date(2023, 2, 17)
    assert nominal_vix_expiry(2023, 1) == date(2023, 1, 18)
    assert nominal_vix_expiry(2026, 8) == date(2026, 8, 19)
    assert archive_contract_code(2012, 12) == "Z12"


if __name__ == "__main__":
    self_test()
    print("date self-test passed")
