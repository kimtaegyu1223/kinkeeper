from korean_lunar_calendar import KoreanLunarCalendar


def lunar_to_solar(year: int, lunar_month: int, lunar_day: int) -> tuple[int, int, int] | None:
    """음력 날짜 → 양력 날짜 변환. 실패 시 None."""
    cal = KoreanLunarCalendar()
    cal.setLunarDate(year, lunar_month, lunar_day, False)
    solar = cal.SolarIsoFormat()
    if not solar:
        return None
    y, m, d = solar.split("-")
    return int(y), int(m), int(d)
