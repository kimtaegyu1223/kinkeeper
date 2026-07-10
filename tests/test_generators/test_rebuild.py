from datetime import date, timedelta

from shared.enums import ReminderType
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
