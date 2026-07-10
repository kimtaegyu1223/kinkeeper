from datetime import date, timedelta
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
