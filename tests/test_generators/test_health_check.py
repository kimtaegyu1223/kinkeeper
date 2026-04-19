"""건강검진 generator 테스트.

핵심 전략:
- _today를 고정해서 "오늘 기준" 상황을 완전히 제어
- checked_at을 조작해 과거/미래 시나리오를 만듦
- 실제 PostgreSQL (testcontainers) 사용 — mock 없음
"""

from datetime import date, timedelta

import pytest

from shared.generators.health_check import _first_of_next_month, rebuild_health_checks
from shared.models import (
    FamilyMember,
    HealthCheckRecord,
    HealthCheckType,
    MemberHealthCheckConfig,
    ScheduledNotification,
)

TODAY = date(2024, 6, 15)
HORIZON = 60
GROUP_CHAT_ID = -1003823744754  # .env GROUP_CHAT_ID와 맞춤


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def member(db_session):
    m = FamilyMember(
        name="홍길동",
        telegram_user_id=11111,
        birthday_solar=date(1980, 3, 10),  # 44세
        active=True,
    )
    db_session.add(m)
    db_session.flush()
    return m


@pytest.fixture
def check_type(db_session):
    ct = HealthCheckType(name="일반건강검진", period_years=2, active=True)
    db_session.add(ct)
    db_session.flush()
    return ct


def _rebuild(session, **kwargs):
    """편의 래퍼 — _today 기본값 TODAY, flush 포함."""
    rebuild_health_checks(session, horizon_days=HORIZON, _today=TODAY, **kwargs)
    session.flush()


def _all_notifs(session) -> list[ScheduledNotification]:
    from sqlalchemy import select

    return list(session.scalars(select(ScheduledNotification)))


def _group(notifs):
    return [n for n in notifs if "group" in (n.source_key or "")]


def _dm(notifs):
    return [n for n in notifs if "dm" in (n.source_key or "")]


def _upcoming(notifs):
    return [n for n in notifs if "upcoming" in (n.source_key or "")]


# ---------------------------------------------------------------------------
# 1. 기록 없음 → 미수검 (overdue)
# ---------------------------------------------------------------------------


def test_overdue_no_record_schedules_group_and_dm(member, check_type, db_session):
    """검진 기록이 전혀 없으면 그룹 월간 + 개인 DM 주간 알림이 생성된다."""
    _rebuild(db_session)
    notifs = _all_notifs(db_session)

    assert len(_group(notifs)) >= 1, "그룹 채널 월간 알림이 없음"
    assert len(_dm(notifs)) >= 1, "개인 DM 주간 알림이 없음"
    assert len(_upcoming(notifs)) == 0, "overdue인데 upcoming이 생기면 안 됨"


# ---------------------------------------------------------------------------
# 2. 기록이 오래됨 → 미수검
# ---------------------------------------------------------------------------


def test_overdue_old_record(member, check_type, db_session):
    """마지막 검진이 period_years + 1년 전이면 overdue."""
    # period=2년, 3년 전에 받음 → 1년 전에 받았어야 했는데 안 받음
    old_date = TODAY - timedelta(days=365 * 3)
    db_session.add(
        HealthCheckRecord(member_id=member.id, check_type_id=check_type.id, checked_at=old_date)
    )
    db_session.flush()

    _rebuild(db_session)
    notifs = _all_notifs(db_session)

    assert len(_group(notifs)) >= 1
    assert len(_dm(notifs)) >= 1


# ---------------------------------------------------------------------------
# 3. 수검 예정 (upcoming) — horizon 안에 due_date 있음
# ---------------------------------------------------------------------------


def test_upcoming_within_horizon(member, check_type, db_session):
    """20일 후가 검진 예정일이면 upcoming 알림(14/7/0일 전)이 생성된다."""
    # period=2년, 검진일이 TODAY+20일이 되려면 2년 전 - 20일에 받았어야 함
    last_checked = TODAY - timedelta(days=365 * 2 - 20)
    db_session.add(
        HealthCheckRecord(
            member_id=member.id, check_type_id=check_type.id, checked_at=last_checked
        )
    )
    db_session.flush()

    _rebuild(db_session)
    notifs = _all_notifs(db_session)
    up = _upcoming(notifs)

    assert len(up) >= 1, "upcoming 알림이 없음"
    assert len(_group(notifs)) == 0, "upcoming인데 overdue 그룹 공지가 생기면 안 됨"


# ---------------------------------------------------------------------------
# 4. 최근 수검 → 아무 알림 없음
# ---------------------------------------------------------------------------


def test_no_notification_when_recently_checked(member, check_type, db_session):
    """1개월 전에 받았고 period=2년이면 horizon 60일 안에 아무것도 없다."""
    recent = TODAY - timedelta(days=30)
    db_session.add(
        HealthCheckRecord(member_id=member.id, check_type_id=check_type.id, checked_at=recent)
    )
    db_session.flush()

    _rebuild(db_session)
    assert _all_notifs(db_session) == []


# ---------------------------------------------------------------------------
# 5. 멱등성 — 2번 rebuild해도 중복 row 없음
# ---------------------------------------------------------------------------


def test_idempotent(member, check_type, db_session):
    """rebuild를 두 번 실행해도 알림 row가 중복 생성되지 않는다."""
    _rebuild(db_session)
    first_count = len(_all_notifs(db_session))

    _rebuild(db_session)
    second_count = len(_all_notifs(db_session))

    assert first_count == second_count, "2번 실행 시 중복 row 발생"


# ---------------------------------------------------------------------------
# 6. 성별 필터 — 다른 성별은 스킵
# ---------------------------------------------------------------------------


def test_gender_filter_skips_wrong_gender(db_session):
    """여성 전용 검진은 남성 구성원에게 알림을 보내지 않는다."""
    male = FamilyMember(name="남성", telegram_user_id=22222, gender="M", active=True)
    ct = HealthCheckType(name="유방초음파", period_years=2, gender="F", active=True)
    db_session.add_all([male, ct])
    db_session.flush()

    _rebuild(db_session)
    assert _all_notifs(db_session) == []


def test_gender_filter_allows_unset_gender(db_session):
    """성별 미설정 구성원은 여성 전용 검진도 알림을 받는다 (모르면 일단 보냄)."""
    unknown = FamilyMember(name="미설정", telegram_user_id=33333, gender=None, active=True)
    ct = HealthCheckType(name="유방초음파", period_years=2, gender="F", active=True)
    db_session.add_all([unknown, ct])
    db_session.flush()

    _rebuild(db_session)
    assert len(_all_notifs(db_session)) >= 1


# ---------------------------------------------------------------------------
# 7. 나이 제한
# ---------------------------------------------------------------------------


def test_min_age_skips_too_young(db_session):
    """50세 미만이면 50세 이상 전용 검진 알림을 보내지 않는다."""
    young = FamilyMember(
        name="젊은이",
        telegram_user_id=44444,
        birthday_solar=date(1990, 1, 1),  # TODAY 기준 34세
        active=True,
    )
    ct = HealthCheckType(name="대장내시경", period_years=5, min_age=50, active=True)
    db_session.add_all([young, ct])
    db_session.flush()

    _rebuild(db_session)
    assert _all_notifs(db_session) == []


def test_min_age_allows_old_enough(db_session):
    """50세 이상이면 min_age=50 검진 알림을 받는다."""
    senior = FamilyMember(
        name="어르신",
        telegram_user_id=55555,
        birthday_solar=date(1960, 1, 1),  # TODAY 기준 64세
        active=True,
    )
    ct = HealthCheckType(name="대장내시경", period_years=5, min_age=50, active=True)
    db_session.add_all([senior, ct])
    db_session.flush()

    _rebuild(db_session)
    assert len(_all_notifs(db_session)) >= 1


def test_min_age_skips_when_no_birthday(db_session):
    """나이 제한 있는 검진인데 생일이 없으면 스킵한다."""
    no_bday = FamilyMember(name="생일없음", telegram_user_id=66666, active=True)
    ct = HealthCheckType(name="대장내시경", period_years=5, min_age=50, active=True)
    db_session.add_all([no_bday, ct])
    db_session.flush()

    _rebuild(db_session)
    assert _all_notifs(db_session) == []


# ---------------------------------------------------------------------------
# 8. 구성원별 비활성 (MemberHealthCheckConfig.active=False)
# ---------------------------------------------------------------------------


def test_member_config_inactive_skips(member, check_type, db_session):
    """구성원이 해당 검진을 비활성화하면 알림이 생성되지 않는다."""
    cfg = MemberHealthCheckConfig(
        member_id=member.id, check_type_id=check_type.id, active=False
    )
    db_session.add(cfg)
    db_session.flush()

    _rebuild(db_session)
    assert _all_notifs(db_session) == []


# ---------------------------------------------------------------------------
# 9. 구성원별 주기 오버라이드
# ---------------------------------------------------------------------------


def test_member_config_period_override(member, check_type, db_session):
    """구성원 개인 설정으로 period_years를 오버라이드하면 그 주기로 계산한다.

    check_type.period_years=2, config.period_years=1
    마지막 수검: 13개월 전 → 기본 2년 기준으론 upcoming, 1년 기준으론 overdue.
    """
    last_checked = TODAY - timedelta(days=30 * 13)  # 13개월 전
    db_session.add(
        HealthCheckRecord(
            member_id=member.id, check_type_id=check_type.id, checked_at=last_checked
        )
    )
    cfg = MemberHealthCheckConfig(
        member_id=member.id, check_type_id=check_type.id, period_years=1, active=True
    )
    db_session.add(cfg)
    db_session.flush()

    _rebuild(db_session)
    notifs = _all_notifs(db_session)

    # 1년 기준 → overdue
    assert len(_group(notifs)) >= 1, "1년 오버라이드 기준 overdue 그룹 알림 없음"
    assert len(_upcoming(notifs)) == 0, "overdue인데 upcoming이 생기면 안 됨"


# ---------------------------------------------------------------------------
# 10. telegram_user_id 없으면 DM 없음, 그룹만
# ---------------------------------------------------------------------------


def test_no_telegram_id_no_dm(db_session):
    """telegram_user_id가 없으면 개인 DM은 생성되지 않고 그룹 알림만 생성된다."""
    no_tg = FamilyMember(name="텔레없음", telegram_user_id=None, active=True)
    ct = HealthCheckType(name="혈액검사", period_years=1, active=True)
    db_session.add_all([no_tg, ct])
    db_session.flush()

    _rebuild(db_session)
    notifs = _all_notifs(db_session)

    assert len(_dm(notifs)) == 0, "telegram_user_id 없는데 DM이 생성됨"
    assert len(_group(notifs)) >= 1, "그룹 알림은 있어야 함"


# ---------------------------------------------------------------------------
# 11. _first_of_next_month 단위 테스트
# ---------------------------------------------------------------------------


def test_first_of_next_month_normal():
    assert _first_of_next_month(date(2024, 6, 15)) == date(2024, 7, 1)


def test_first_of_next_month_december():
    assert _first_of_next_month(date(2024, 12, 1)) == date(2025, 1, 1)


def test_first_of_next_month_end_of_month():
    assert _first_of_next_month(date(2024, 1, 31)) == date(2024, 2, 1)
