"""생성기 공용 시간 헬퍼.

시간대 관련 로직을 한 곳에 모아 생성기별 복붙으로 인한 타임존 버그 재발을
막는다 (audit #6/#31). 모든 생성기가 이 모듈을 import해 사용한다.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from shared.config import settings


def today_local() -> date:
    """settings.tz 기준 오늘 날짜."""
    return datetime.now(ZoneInfo(settings.tz)).date()


def now_utc() -> datetime:
    """현재 UTC 시각."""
    return datetime.now(UTC)


def scheduled_at_local(day: date, hour: int = 9) -> datetime:
    """로컬(settings.tz) day/hour 벽시계를 UTC datetime으로 변환."""
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=ZoneInfo(settings.tz)).astimezone(
        UTC
    )
