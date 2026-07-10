from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from shared.config import settings
from shared.enums import ReminderType
from shared.generators import birthday as birthday_module
from shared.generators.birthday import generate
from shared.models import FamilyMember, ReminderRule, ScheduledNotification

# 실행일에 종속되지 않도록 today를 고정한다 (test_custom 스타일).
FIXED_TODAY = date(2026, 6, 15)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(birthday_module, "_today_local", lambda: FIXED_TODAY)


@pytest.fixture
def member(db_session):
    m = FamilyMember(
        name="테스트",
        telegram_user_id=99999,
        birthday_solar=FIXED_TODAY + timedelta(days=5),  # 5일 후 생일
        timezone="Asia/Seoul",
    )
    db_session.add(m)
    db_session.flush()
    return m


@pytest.fixture
def rule(member, db_session):
    r = ReminderRule(
        type=ReminderType.birthday,
        title="테스트 생일",
        lead_times_days=[7, 3, 1, 0],
        config={"member_id": member.id},
        active=True,
    )
    db_session.add(r)
    db_session.flush()
    return r


def test_birthday_generates_notifications(rule, member, db_session) -> None:
    generate(rule, db_session, horizon_days=60)
    db_session.flush()

    notifications = (
        db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.rule_id == rule.id)
        .all()
    )
    # 5일 후 생일이므로 lead_times [7,3,1,0] 중 3,1,0일 전만 생성
    scheduled_leads = []
    bday = member.birthday_solar
    for n in notifications:
        delta = (bday - n.scheduled_at.date()).days
        scheduled_leads.append(delta)

    assert 3 in scheduled_leads
    assert 1 in scheduled_leads
    assert 0 in scheduled_leads
    assert 7 not in scheduled_leads  # 7일 전은 오늘보다 이전이므로 생성 안 됨
    assert all(n.scheduled_at.astimezone(ZoneInfo(settings.tz)).hour == 9 for n in notifications)


def test_birthday_idempotent(rule, member, db_session) -> None:
    generate(rule, db_session, horizon_days=60)
    generate(rule, db_session, horizon_days=60)
    db_session.flush()

    count = (
        db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.rule_id == rule.id)
        .count()
    )
    # 중복 생성 없음
    assert count == 3


def test_birthday_feb29_does_not_crash_on_common_year(db_session, monkeypatch) -> None:
    """양력 2/29 생일: 평년 재생성 시 ValueError 없이 2/28로 폴백해야 한다 (audit #5)."""
    monkeypatch.setattr(birthday_module, "_today_local", lambda: date(2026, 2, 1))
    member = FamilyMember(
        name="윤일",
        telegram_user_id=42,
        birthday_solar=date(1996, 2, 29),
        timezone="Asia/Seoul",
    )
    db_session.add(member)
    db_session.flush()
    rule = ReminderRule(
        type=ReminderType.birthday,
        title="윤일 생일",
        lead_times_days=[0],
        config={"member_id": member.id},
        active=True,
    )
    db_session.add(rule)
    db_session.flush()

    # 2026, 2027 모두 평년 — 크래시 없이 완료되어야 한다.
    generate(rule, db_session, horizon_days=60)
    db_session.flush()

    scheduled_dates = {
        n.scheduled_at.astimezone(ZoneInfo(settings.tz)).date()
        for n in db_session.query(ScheduledNotification)
        .filter(ScheduledNotification.rule_id == rule.id)
        .all()
    }
    # 평년에는 2/28로 폴백
    assert date(2026, 2, 28) in scheduled_dates


def test_birthday_skips_past_slot_today(db_session, monkeypatch) -> None:
    """오늘 생일(lead 0)이라도 발송 시각이 이미 지났으면 재생성하지 않는다 (audit #1)."""
    today = date(2026, 6, 15)
    monkeypatch.setattr(birthday_module, "_today_local", lambda: today)
    # now = 오늘 12:00 KST → 09:00 KST slot은 이미 과거
    now = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")).astimezone(UTC)
    monkeypatch.setattr(birthday_module, "_now_utc", lambda: now)

    member = FamilyMember(
        name="오늘생일",
        telegram_user_id=555,
        birthday_solar=today,
        timezone="Asia/Seoul",
    )
    db_session.add(member)
    db_session.flush()
    rule = ReminderRule(
        type=ReminderType.birthday,
        title="오늘 생일",
        lead_times_days=[0],
        config={"member_id": member.id, "hour": 9},
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
    assert count == 0, "이미 지난 오늘 slot이 재생성됨"


def test_birthday_keeps_future_slot_today(db_session, monkeypatch) -> None:
    """오늘 생일이고 발송 시각이 아직 안 지났으면 생성한다 (audit #1 회귀)."""
    today = date(2026, 6, 15)
    monkeypatch.setattr(birthday_module, "_today_local", lambda: today)
    # now = 오늘 06:00 KST → 09:00 KST slot은 아직 미래
    now = datetime(2026, 6, 15, 6, 0, tzinfo=ZoneInfo("Asia/Seoul")).astimezone(UTC)
    monkeypatch.setattr(birthday_module, "_now_utc", lambda: now)

    member = FamilyMember(
        name="오늘생일",
        telegram_user_id=556,
        birthday_solar=today,
        timezone="Asia/Seoul",
    )
    db_session.add(member)
    db_session.flush()
    rule = ReminderRule(
        type=ReminderType.birthday,
        title="오늘 생일",
        lead_times_days=[0],
        config={"member_id": member.id, "hour": 9},
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
    assert count == 1


def test_birthday_lunar_year_carryover(db_session, monkeypatch) -> None:
    """음력 12월 생일이 이듬해 양력 1월에 걸릴 때, 연초 재생성에서 누락되면 안 된다 (audit #2)."""
    monkeypatch.setattr(birthday_module, "_today_local", lambda: date(2027, 1, 1))
    member = FamilyMember(
        name="음력12월",
        telegram_user_id=77,
        birthday_lunar=date(2000, 12, 15),  # 음력 12/15
        timezone="Asia/Seoul",
    )
    db_session.add(member)
    db_session.flush()
    rule = ReminderRule(
        type=ReminderType.birthday,
        title="음력 생일",
        lead_times_days=[7, 1, 0],
        config={"member_id": member.id, "use_lunar": True},
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
    # 음력 2026-12-15 → 양력 2027-01-22 (today.year-1 후보를 시도해야만 잡힌다)
    assert date(2027, 1, 22) in scheduled_dates  # 당일
    assert date(2027, 1, 21) in scheduled_dates  # D-1
    assert date(2027, 1, 15) in scheduled_dates  # D-7


def test_birthday_escapes_name_with_html_chars(rule, member, db_session) -> None:
    """이름에 '<' 등 HTML 특수문자가 있어도 escape되어야 텔레그램 파싱 실패를 막는다 (audit #11)."""
    member.name = "엄마<3 & 아빠"
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
        assert "엄마&lt;3 &amp; 아빠" in msg
        # 원문 '<3'가 그대로 남으면 안 된다 (의도된 <b> 마크업은 유지)
        assert "엄마<3" not in msg
        assert "<b>" in msg
