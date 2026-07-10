"""몸무게 기록 핸들러 회귀 테스트 (audit #32).

nudge 취소 주간 범위를 KST로 계산하는지 검증한다. now()를 KST 월요일 새벽으로
고정해, 이번 KST 주의 nudge는 취소되고 다음 주 nudge는 남는지 확인한다.
"""

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import sessionmaker

import bot.handlers.weight as weight
from shared.enums import NotificationStatus
from shared.models import FamilyMember, ScheduledNotification, WeightLog

# KST 월요일 05:00 = UTC 일요일 20:00. audit #32의 트리거 구간(월 00:00~08:59 KST).
_FROZEN = datetime(2026, 7, 6, 5, 0, tzinfo=ZoneInfo("Asia/Seoul"))


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return _FROZEN.astimezone(tz) if tz is not None else _FROZEN.replace(tzinfo=None)


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, parse_mode: str | None = None) -> None:
        self.replies.append(text)


class _FakeUpdate:
    def __init__(self, telegram_user_id: int) -> None:
        self.message = _FakeMessage()
        self.effective_user = SimpleNamespace(id=telegram_user_id)


class _FakeContext:
    def __init__(self, args: list[str]) -> None:
        self.args = args


@pytest.fixture
def weight_db(db_engine, monkeypatch):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)

    @contextmanager
    def _get_session():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(weight, "get_session", _get_session)
    monkeypatch.setattr(weight, "datetime", _FrozenDatetime)
    yield Session
    with _get_session() as s:
        s.query(ScheduledNotification).delete()
        s.query(WeightLog).delete()
        s.query(FamilyMember).delete()


async def test_weight_command_cancels_kst_week_nudges(weight_db) -> None:
    """월요일 아침(KST) 기록 시 이번 KST 주 nudge가 취소돼야 한다 (audit #32)."""
    with weight_db() as s:
        member = FamilyMember(
            name="아침측정",
            telegram_user_id=8001,
            height_cm=170,
            diet_active=True,
            active=True,
        )
        s.add(member)
        s.commit()
        mid = member.id

        # 이번 KST 주 화요일 nudge: KST 07-07 09:00 = UTC 07-07 00:00
        in_week = ScheduledNotification(
            source_key=f"diet:nudge:{mid}:2026-07-07",
            scheduled_at=datetime(2026, 7, 7, 0, 0, tzinfo=UTC),
            target_telegram_id=8001,
            message="nudge",
            status=NotificationStatus.pending,
        )
        # 다음 주 화요일 nudge: UTC 07-14 00:00 — 이번 KST 주 밖이라 남아야 한다.
        next_week = ScheduledNotification(
            source_key=f"diet:nudge:{mid}:2026-07-14",
            scheduled_at=datetime(2026, 7, 14, 0, 0, tzinfo=UTC),
            target_telegram_id=8001,
            message="nudge",
            status=NotificationStatus.pending,
        )
        s.add_all([in_week, next_week])
        s.commit()
        in_week_id, next_week_id = in_week.id, next_week.id

    update = _FakeUpdate(8001)
    await weight.weight_command(update, _FakeContext(["67.2"]))

    assert update.message.replies, "기록 완료 응답이 없음"

    with weight_db() as s:
        assert s.get(ScheduledNotification, in_week_id).status == NotificationStatus.cancelled
        assert s.get(ScheduledNotification, next_week_id).status == NotificationStatus.pending
