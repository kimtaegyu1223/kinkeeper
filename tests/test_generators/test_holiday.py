from datetime import date
from zoneinfo import ZoneInfo

from shared.config import settings
from shared.enums import ReminderType
from shared.generators import holiday as holiday_module
from shared.generators.holiday import generate
from shared.models import ReminderRule, ScheduledNotification


def test_holiday_lunar_generates(db_session, monkeypatch) -> None:
    monkeypatch.setattr(holiday_module, "_today_local", lambda: date(2026, 9, 1))
    rule = ReminderRule(
        type=ReminderType.holiday,
        title="추석",
        lead_times_days=[7, 0],
        config={"lunar_month": 8, "lunar_day": 15, "name": "추석", "hour": 9},
        active=True,
    )
    db_session.add(rule)
    db_session.flush()

    generate(rule, db_session, horizon_days=60)
    db_session.flush()

    count = (
        db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.rule_id == rule.id)
        .count()
    )
    assert count > 0


def test_holiday_lunar_year_carryover(db_session, monkeypatch) -> None:
    """음력 12월 명절이 이듬해 양력 1월에 걸릴 때 연초 재생성에서 누락되면 안 된다 (audit #3)."""
    monkeypatch.setattr(holiday_module, "_today_local", lambda: date(2027, 1, 1))
    rule = ReminderRule(
        type=ReminderType.holiday,
        title="섣달 기일",
        lead_times_days=[7, 2, 0],
        config={"lunar_month": 12, "lunar_day": 20, "name": "섣달 기일", "hour": 9},
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
    # 음력 2026-12-20 → 양력 2027-01-27
    assert date(2027, 1, 27) in scheduled_dates  # 당일
    assert date(2027, 1, 20) in scheduled_dates  # D-7
