"""구성원 수명주기 회귀 테스트 (audit #16, #43, #44).

- #44: 구성원 편집 시 생일 규칙의 커스텀 hour/lead_times가 보존돼야 한다.
- #43: 생일 정보를 모두 지우면 생일 규칙이 비활성화되고 pending이 삭제돼야 한다.
- #16: 구성원 삭제 시 연결된 생일 규칙과 pending 알림이 함께 정리돼야 한다.

라우트는 shared.db.get_session(전역 엔진)을 쓰므로 테스트 컨테이너 엔진으로
monkeypatch한다(test_broadcast.py 패턴 재사용). 헬퍼 단위 테스트는 db_session을
직접 사용한다.
"""

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import web.routes.members as members_route
from shared.config import settings
from shared.enums import NotificationStatus, ReminderType
from shared.generators import rebuild_for_rule
from shared.models import FamilyMember, ReminderRule, ScheduledNotification
from web.auth import verify_admin
from web.main import app
from web.routes.members import _ensure_birthday_rule


def test_ensure_birthday_rule_preserves_custom_settings(db_session) -> None:
    """생일과 무관한 편집이 커스텀 hour/lead_times를 기본값으로 덮으면 안 된다 (audit #44)."""
    member = FamilyMember(name="아빠", birthday_solar=date(1970, 7, 15))
    db_session.add(member)
    db_session.flush()

    # 관리자가 /rules에서 20시·[14,7,3,1,0]으로 커스텀한 규칙
    rule = ReminderRule(
        type=ReminderType.birthday,
        title="아빠 생일 알림",
        lead_times_days=[14, 7, 3, 1, 0],
        config={"member_id": member.id, "use_lunar": False, "hour": 20},
        active=True,
    )
    db_session.add(rule)
    db_session.flush()

    # 생일과 무관한 편집(성별만 수정) 시뮬레이션
    member.gender = "M"
    result = _ensure_birthday_rule(db_session, member)
    db_session.flush()

    assert result is rule
    assert result.config["hour"] == 20, "커스텀 hour가 기본값으로 리셋됨"
    assert result.lead_times_days == [14, 7, 3, 1, 0], "커스텀 lead_times가 리셋됨"
    assert result.config["member_id"] == member.id
    assert result.active is True


def test_ensure_birthday_rule_deactivates_and_cancels_pending_when_cleared(db_session) -> None:
    """생일 정보를 모두 지우면 규칙이 비활성화되고 rebuild가 pending을 삭제한다 (audit #43)."""
    member = FamilyMember(name="엄마", birthday_solar=date(1975, 3, 20))
    db_session.add(member)
    db_session.flush()

    rule = ReminderRule(
        type=ReminderType.birthday,
        title="엄마 생일 알림",
        lead_times_days=[7, 3, 0],
        config={"member_id": member.id, "use_lunar": False, "hour": 9},
        active=True,
    )
    db_session.add(rule)
    db_session.flush()

    # 활성 시절 생성된 pending 알림
    db_session.add(
        ScheduledNotification(
            rule_id=rule.id,
            scheduled_at=datetime.now(UTC) + timedelta(hours=1),
            target_telegram_id=-1001234567890,
            message="🎂 오늘은 엄마님의 생일입니다!",
            status=NotificationStatus.pending,
        )
    )
    db_session.flush()

    # 생일 삭제
    member.birthday_solar = None
    member.birthday_lunar = None
    result = _ensure_birthday_rule(db_session, member)
    db_session.flush()

    assert result is rule
    assert result.active is False, "생일 삭제 후에도 규칙이 활성으로 남음(좀비 규칙)"

    # update_member가 하는 후속 rebuild — 비활성 규칙이라 pending만 삭제하고 반환
    rebuild_for_rule(rule.id, db_session)
    db_session.flush()

    pending = (
        db_session.query(ScheduledNotification)
        .filter(
            ScheduledNotification.rule_id == rule.id,
            ScheduledNotification.status == NotificationStatus.pending,
        )
        .count()
    )
    assert pending == 0, "생일 삭제 후에도 pending 알림이 남음"


@pytest.fixture
def client(db_engine, monkeypatch):
    Session = sessionmaker(bind=db_engine, expire_on_commit=False)

    @contextmanager
    def _get_session():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(members_route, "get_session", _get_session)
    monkeypatch.setattr(settings, "group_chat_id", -1001234567890)
    app.dependency_overrides[verify_admin] = lambda: "admin"

    yield TestClient(app), Session

    app.dependency_overrides.clear()
    with _get_session() as s:
        s.query(ScheduledNotification).delete()
        s.query(ReminderRule).delete()
        s.query(FamilyMember).delete()


def test_delete_member_removes_birthday_rule_and_pending(client) -> None:
    """구성원 삭제 시 생일 규칙과 그 pending 알림이 함께 삭제돼야 한다 (audit #16)."""
    test_client, Session = client
    with Session() as s:
        member = FamilyMember(name="삭제될사람", birthday_solar=date(1980, 1, 1))
        s.add(member)
        s.flush()
        rule = ReminderRule(
            type=ReminderType.birthday,
            title="삭제될사람 생일 알림",
            lead_times_days=[0],
            config={"member_id": member.id, "use_lunar": False, "hour": 9},
            active=True,
        )
        s.add(rule)
        s.flush()
        s.add(
            ScheduledNotification(
                rule_id=rule.id,
                scheduled_at=datetime.now(UTC) + timedelta(hours=1),
                target_telegram_id=-1001234567890,
                message="🎂 오늘은 삭제될사람님의 생일입니다!",
                status=NotificationStatus.pending,
            )
        )
        s.commit()
        member_id = member.id
        rule_id = rule.id

    resp = test_client.delete(f"/members/{member_id}")
    assert resp.status_code == 200

    with Session() as s:
        assert s.get(FamilyMember, member_id) is None
        assert s.get(ReminderRule, rule_id) is None, "삭제된 구성원의 좀비 생일 규칙이 남음"
        remaining = (
            s.query(ScheduledNotification).filter(ScheduledNotification.rule_id == rule_id).count()
        )
        assert remaining == 0, "삭제된 구성원의 pending 알림이 남음"
