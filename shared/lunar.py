from korean_lunar_calendar import KoreanLunarCalendar


def lunar_to_solar(year: int, lunar_month: int, lunar_day: int) -> tuple[int, int, int] | None:
    """음력 날짜 → 양력 날짜 변환. 실패 시 None."""
    cal = KoreanLunarCalendar()
    # 존재하지 않는 음력 날짜는 setLunarDate가 False를 반환한다.
    # (이때 SolarIsoFormat()은 truthy 문자열 '0000-00-00'을 돌려주므로 반드시 확인)
    if not cal.setLunarDate(year, lunar_month, lunar_day, False):
        return None
    solar = cal.SolarIsoFormat()
    if not solar or solar == "0000-00-00":
        return None
    y, m, d = solar.split("-")
    return int(y), int(m), int(d)
