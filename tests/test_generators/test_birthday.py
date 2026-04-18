from datetime import date, timedelta

import pytest

from shared.enums import ReminderType
from shared.generators.birthday import generate
from shared.models import FamilyMember, ReminderRule, ScheduledNotification


@pytest.fixture
def member(db_session):
    m = FamilyMember(
        name="테스트",
        telegram_user_id=99999,
        birthday_solar=date.today() + timedelta(days=5),  # 5일 후 생일
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
        target_member_ids=[member.id],
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
