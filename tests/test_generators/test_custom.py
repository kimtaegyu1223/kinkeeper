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


def test_yearly_custom_lunar_year_carryover(db_session, monkeypatch):
    """음력 12월 기일이 이듬해 양력 1월에 걸릴 때 연초 재생성에서 누락되면 안 된다 (audit #4)."""
    monkeypatch.setattr(custom, "_today_local", lambda: date(2027, 1, 1))
    rule = ReminderRule(
        type=ReminderType.custom,
        title="섣달 기일",
        lead_times_days=[3, 0],
        config={
            "repeat": "yearly",
            "month": 12,
            "day": 15,
            "hour": 9,
            "use_lunar": True,
            "message": "섣달 기일",
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
    # 음력 2026-12-15 → 양력 2027-01-22
    assert date(2027, 1, 22) in scheduled_dates  # 당일
    assert date(2027, 1, 19) in scheduled_dates  # D-3


def test_once_custom_escapes_message(db_session):
    """1회성 custom 메시지도 escape되어야 한다 — yearly만 escape하던 비대칭 버그 (audit #13)."""
    from datetime import UTC, datetime, timedelta

    run_at = datetime.now(UTC) + timedelta(days=3)
    rule = ReminderRule(
        type=ReminderType.custom,
        title="병원 예약",
        lead_times_days=[0],
        config={"message": "병원 예약 <오후 3시> & 검사", "run_at": run_at.isoformat()},
        active=True,
    )
    db_session.add(rule)
    db_session.flush()

    generate(rule, db_session, horizon_days=60)
    db_session.flush()

    messages = [
        n.message
        for n in db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.rule_id == rule.id)
        .all()
    ]
    assert messages
    for msg in messages:
        assert msg == "병원 예약 &lt;오후 3시&gt; &amp; 검사"


def test_once_custom_naive_run_at_interpreted_as_local_tz(db_session):
    """datetime-local이 보내는 naive 벽시계는 settings.tz(KST)로 해석해야 한다 (audit #6).

    naive를 UTC로 간주하면 KST 기준 9시간 늦게 예약된다.
    """
    from datetime import date, datetime, timedelta

    # 폼이 보내는 형식(타임존 없는 로컬 문자열). 충분히 미래로 잡아 과거 컷을 피한다.
    run_at_local = datetime.combine(date.today() + timedelta(days=3), datetime.min.time()).replace(
        hour=9
    )
    rule = ReminderRule(
        type=ReminderType.custom,
        title="병원 예약",
        lead_times_days=[0],
        config={"message": "병원 예약", "run_at": run_at_local.isoformat()},
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
    assert notifications
    for n in notifications:
        local = n.scheduled_at.astimezone(ZoneInfo(settings.tz))
        # 입력한 KST 벽시계(9시)와 정확히 일치해야 한다 (UTC 오해석이면 18시가 됨).
        assert local.hour == 9
        assert local.replace(tzinfo=None) == run_at_local


def test_yearly_custom_escapes_message_once(db_session):
    """yearly 경로는 escape를 한 번만 적용해야 한다 (이중 escape 회귀)."""
    event_date = date.today() + timedelta(days=20)
    rule = ReminderRule(
        type=ReminderType.custom,
        title="이벤트",
        lead_times_days=[0],
        config={
            "repeat": "yearly",
            "month": event_date.month,
            "day": event_date.day,
            "hour": 9,
            "message": "행사 <A & B>",
        },
        active=True,
    )
    db_session.add(rule)
    db_session.flush()

    generate(rule, db_session, horizon_days=60)
    db_session.flush()

    messages = [
        n.message
        for n in db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.rule_id == rule.id)
        .all()
    ]
    assert messages
    for msg in messages:
        assert "행사 &lt;A &amp; B&gt;" in msg
        # 이중 escape(&amp;lt;) 되면 안 된다
        assert "&amp;lt;" not in msg
        assert "<b>" in msg
