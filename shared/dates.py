from datetime import date


def replace_year(d: date, year: int) -> date:
    """날짜의 연도만 교체. 2월 29일을 평년으로 옮기면 2월 28일로 폴백."""
    try:
        return d.replace(year=year)
    except ValueError:
        # 2/29 → 평년: 2/28로 폴백
        return d.replace(year=year, day=28)
