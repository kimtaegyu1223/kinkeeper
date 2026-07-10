"""bot/handlers/health 헬퍼의 HTML escape 회귀 테스트 (audit #39, #40).

핸들러는 shared.db.get_session(전역 엔진)을 쓰므로 테스트 컨테이너 엔진에
바인딩한 세션 팩토리로 monkeypatch한다 (test_dispatch 스타일).
"""

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import sessionmaker

import bot.handlers.health as health
from shared.models import FamilyMember, HealthCheckType


@pytest.fixture
def health_db(db_engine, monkeypatch):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)

    @contextmanager
    def _get_session():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(health, "get_session", _get_session)
    yield Session
    with _get_session() as s:
        s.query(HealthCheckType).delete()
        s.query(FamilyMember).delete()


def _add_member(Session, name: str) -> int:
    with Session() as s:
        m = FamilyMember(name=name, telegram_user_id=1000, active=True)
        s.add(m)
        s.commit()
        return m.id


def test_health_status_escapes_member_and_check_names(health_db) -> None:
    """/내건강검진 현황에 member.name·ct.name이 escape되어야 한다 (audit #40)."""
    member_id = _add_member(health_db, "홍길동<b>")
    with health_db() as s:
        s.add(HealthCheckType(name="위내시경<수면>", period_years=2, active=True))
        s.commit()

    text = health._get_health_status(member_id)

    assert "홍길동&lt;b&gt;" in text
    assert "위내시경&lt;수면&gt;" in text
    assert "위내시경<수면>" not in text
    # 의도된 <b> 마크업은 유지
    assert "<b>" in text


def test_record_check_escapes_unknown_name(health_db) -> None:
    """미매칭 시 사용자 입력 check_name이 escape되어야 한다 (audit #39)."""
    member_id = _add_member(health_db, "홍길동")
    with health_db() as s:
        s.add(HealthCheckType(name="위내시경", period_years=2, active=True))
        s.commit()

    result = health._record_check(member_id, "<위내시경>", datetime.now(UTC).date())

    assert "&lt;위내시경&gt;" in result
    assert "<위내시경>" not in result


def test_record_check_escapes_matched_name(health_db) -> None:
    """매칭 성공 응답의 ct.name도 escape되어야 한다 (audit #39)."""
    member_id = _add_member(health_db, "홍길동")
    with health_db() as s:
        s.add(HealthCheckType(name="위내시경<A&B>", period_years=2, active=True))
        s.commit()

    result = health._record_check(member_id, "위내시경<A&B>", datetime.now(UTC).date())

    assert "위내시경&lt;A&amp;B&gt;" in result
    assert "위내시경<A&B>" not in result


def _add_member_full(Session, name: str, *, gender=None, birthday_solar=None) -> int:
    with Session() as s:
        m = FamilyMember(
            name=name,
            telegram_user_id=1000,
            active=True,
            gender=gender,
            birthday_solar=birthday_solar,
        )
        s.add(m)
        s.commit()
        return m.id


def test_record_check_rejects_future_date(health_db) -> None:
    """미래 날짜(연도 오타 등)는 거부하고 저장하지 않는다 (audit #64)."""
    from datetime import timedelta

    from shared.models import HealthCheckRecord

    member_id = _add_member(health_db, "홍길동")
    with health_db() as s:
        s.add(HealthCheckType(name="위내시경", period_years=2, active=True))
        s.commit()

    future = datetime.now(UTC).date() + timedelta(days=1)
    result = health._record_check(member_id, "위내시경", future)

    assert "미래 날짜" in result
    with health_db() as s:
        assert s.query(HealthCheckRecord).count() == 0


def test_health_status_includes_gendered_item_for_unset_gender(health_db) -> None:
    """gender 미설정(None) 구성원도 성별 지정 항목을 본다 (generator와 일치, audit #66)."""
    member_id = _add_member_full(health_db, "성별미상", gender=None)
    with health_db() as s:
        s.add(HealthCheckType(name="유방촬영", period_years=2, gender="F", active=True))
        s.commit()

    text = health._get_health_status(member_id)
    assert "유방촬영" in text


def test_health_status_hides_min_age_item_for_young(health_db) -> None:
    """min_age 미달 구성원에게는 해당 항목을 숨긴다 (generator와 일치, audit #66)."""
    from datetime import date

    member_id = _add_member_full(health_db, "젊은이", birthday_solar=date(2010, 1, 1))
    with health_db() as s:
        s.add(HealthCheckType(name="대장내시경", period_years=5, min_age=50, active=True))
        s.commit()

    text = health._get_health_status(member_id)
    assert "대장내시경" not in text
