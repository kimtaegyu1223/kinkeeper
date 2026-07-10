from datetime import date, timedelta
from zoneinfo import ZoneInfo

from shared.config import settings
from shared.enums import ReminderType
from shared.generators import custom
from shared.generators.custom import generate
from shared.models import ReminderRule, ScheduledNotification


def test_yearly_custom_includes_lead_days_and_uses_local_hour(db_session):
    event_date = date.today() + timedelta(days=20)
    rule = ReminderRule(
        type=ReminderType.custom,
        title="아빠 기일",
        lead_times_days=[14, 7, 0],
        config={
            "repeat": "yearly",
            "month": event_date.month,
            "day": event_date.day,
            "hour": 9,
            "message": "아빠 기일",
        },
        active=True,
    )
    db_session.add(rule)
    db_session.flush()

    generate(rule, db_session, horizon_days=60)
    db_session.flush()

    notifications = (
        db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.rule_id == rule.id)
        .all()
    )
    messages = [n.message for n in notifications]

    assert any("14일 전" in message for message in messages)
    assert any("7일 전" in message for message in messages)
    assert any("오늘은" in message for message in messages)
    assert all(n.scheduled_at.astimezone(ZoneInfo(settings.tz)).hour == 9 for n in notifications)


def test_yearly_custom_lunar_converts_to_solar(db_session, monkeypatch):
    """음력 기일: 저장된 월/일을 그대로 양력으로 쓰지 않고 음력→양력으로 변환해야 한다."""
    monkeypatch.setattr(custom, "_today_local", lambda: date(2026, 1, 1))
    rule = ReminderRule(
        type=ReminderType.custom,
        title="아빠 기일",
        lead_times_days=[0],
        config={
            "repeat": "yearly",
            "month": 8,
            "day": 6,
            "hour": 9,
            "use_lunar": True,
            "message": "아빠 기일",
        },
        active=True,
    )
    db_session.add(rule)
    db_session.flush()

    generate(rule, db_session, horizon_days=365)
    db_session.flush()

    scheduled_dates = {
        n.scheduled_at.astimezone(ZoneInfo(settings.tz)).date()
        for n in db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.rule_id == rule.id)
        .all()
    }
    # 음력 8/6 (2026) → 양력 2026-09-16 이어야 하고, 양력 8/6 이면 안 된다.
    assert date(2026, 9, 16) in scheduled_dates
    assert date(2026, 8, 6) not in scheduled_dates
