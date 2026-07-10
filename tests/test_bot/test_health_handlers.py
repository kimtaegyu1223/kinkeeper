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
