from datetime import date, timedelta

from shared.enums import NotificationStatus, ReminderType
from shared.generators import _REGISTRY, rebuild_upcoming
from shared.models import ReminderRule, ScheduledNotification


def test_rebuild_isolates_failing_rule(db_session, monkeypatch) -> None:
    """규칙 하나의 예외가 전체 재생성을 중단시키면 안 된다 (audit #0)."""

    def boom(rule, session, horizon_days):
        raise ValueError("의도적 실패")

    monkeypatch.setitem(_REGISTRY, ReminderType.birthday, boom)

    # 항상 실패하는 birthday 규칙
    bad_rule = ReminderRule(
        type=ReminderType.birthday,
        title="깨지는 규칙",
        lead_times_days=[0],
        config={"member_id": 1},
        active=True,
    )
    # 정상 생성되어야 하는 custom 규칙
    event_date = date.today() + timedelta(days=20)
    good_rule = ReminderRule(
        type=ReminderType.custom,
        title="정상 기일",
        lead_times_days=[0],
        config={
            "repeat": "yearly",
            "month": event_date.month,
            "day": event_date.day,
            "hour": 9,
            "message": "정상 기일",
        },
        active=True,
    )
    db_session.add_all([bad_rule, good_rule])
    db_session.flush()

    # 예외가 전파되지 않아야 한다.
    rebuild_upcoming(db_session, horizon_days=60)
    db_session.flush()

    good_count = (
        db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.rule_id == good_rule.id)
        .count()
    )
    assert good_count > 0


def test_rebuild_does_not_accumulate_cancelled_rows(db_session) -> None:
    """반복 rebuild가 cancelled 행을 누적시키지 않고 pending만 물리 재생성한다 (audit #30)."""
    event_date = date.today() + timedelta(days=20)
    rule = ReminderRule(
        type=ReminderType.custom,
        title="기일",
        lead_times_days=[0],
        config={
            "repeat": "yearly",
            "month": event_date.month,
            "day": event_date.day,
            "hour": 9,
            "message": "기일",
        },
        active=True,
    )
    db_session.add(rule)
    db_session.flush()

    rebuild_upcoming(db_session, horizon_days=60)
    db_session.flush()
    first_total = db_session.query(ScheduledNotification).count()

    # 여러 번 rebuild해도 총 행 수가 늘지 않아야 한다 (cancelled 누적 없음).
    for _ in range(3):
        rebuild_upcoming(db_session, horizon_days=60)
        db_session.flush()

    total = db_session.query(ScheduledNotification).count()
    cancelled = (
        db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.status == NotificationStatus.cancelled)
        .count()
    )
    assert total == first_total, "rebuild 반복 시 행이 증식함"
    assert cancelled == 0, "rebuild가 cancelled 행을 남김"
