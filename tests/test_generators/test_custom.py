from datetime import date, timedelta
from zoneinfo import ZoneInfo

from shared.config import settings
from shared.enums import ReminderType
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
