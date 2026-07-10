"""웹 규칙 라우트 회귀 테스트 (audit #15, #60, #63).

- #15: 규칙을 비활성으로 편집하면 기존 pending 알림이 취소돼야 한다.
- #60: 생일 규칙에서 대상 구성원 미선택(member_id=0)이면 거부돼야 한다.
- #63: 규칙 저장과 알림 rebuild가 같은 트랜잭션이라 rebuild 실패 시 규칙도 롤백된다.

라우트는 shared.db.get_session(전역 엔진)을 쓰므로 테스트 컨테이너 엔진으로
monkeypatch한다(test_broadcast.py 패턴 재사용).
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import web.routes.rules as rules_route
from shared.config import settings
from shared.enums import NotificationStatus, ReminderType
from shared.generators import _REGISTRY
from shared.models import ReminderRule, ScheduledNotification
from web.auth import verify_admin
from web.main import app


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

    monkeypatch.setattr(rules_route, "get_session", _get_session)
    monkeypatch.setattr(settings, "group_chat_id", -1001234567890)
    app.dependency_overrides[verify_admin] = lambda: "admin"

    # 500 응답을 예외로 재발생시키지 않고 그대로 받아 부분 커밋 여부를 검사한다.
    yield TestClient(app, raise_server_exceptions=False), Session

    app.dependency_overrides.clear()
    with _get_session() as s:
        s.query(ScheduledNotification).delete()
        s.query(ReminderRule).delete()


def test_deactivating_rule_cancels_pending(client) -> None:
    """활성 규칙을 비활성으로 편집하면 기존 pending 알림이 취소돼야 한다 (audit #15)."""
    test_client, Session = client
    with Session() as s:
        rule = ReminderRule(
            type=ReminderType.custom,
            title="공지",
            lead_times_days=[0],
            config={"repeat": "once", "run_at": "", "hour": 9, "message": "공지"},
            active=True,
        )
        s.add(rule)
        s.flush()
        rule_id = rule.id
        s.add(
            ScheduledNotification(
                rule_id=rule_id,
                scheduled_at=datetime.now(UTC) + timedelta(hours=2),
                target_telegram_id=-1001234567890,
                message="공지",
                status=NotificationStatus.pending,
            )
        )
        s.commit()

    # active 체크박스 미전송 = 활성 해제
    resp = test_client.post(
        f"/rules/{rule_id}/edit",
        data={
            "type": "custom",
            "title": "공지",
            "custom_repeat": "once",
            "custom_message": "공지",
            "custom_run_at": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    with Session() as s:
        rule = s.get(ReminderRule, rule_id)
        assert rule is not None
        assert rule.active is False
        pending = (
            s.query(ScheduledNotification)
            .filter(
                ScheduledNotification.rule_id == rule_id,
                ScheduledNotification.status == NotificationStatus.pending,
            )
            .count()
        )
        assert pending == 0, "비활성 전환 후에도 pending 알림이 남음"


def test_create_birthday_rule_without_member_rejected(client) -> None:
    """대상 구성원 미선택 생일 규칙은 4xx로 거부되고 저장되지 않아야 한다 (audit #60)."""
    test_client, Session = client
    resp = test_client.post(
        "/rules/new",
        data={
            "type": "birthday",
            "title": "이름없는 생일",
            "active": "on",
            "birthday_member_id": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400

    with Session() as s:
        count = s.query(ReminderRule).count()
    assert count == 0, "member_id 없는 좀비 생일 규칙이 저장됨"


def test_create_rule_rebuild_failure_rolls_back(client, monkeypatch) -> None:
    """rebuild가 실패하면 규칙 저장까지 롤백돼 부분 커밋이 발생하지 않아야 한다 (audit #63)."""
    test_client, Session = client

    def boom(rule, session, horizon_days):
        raise ValueError("의도적 실패")

    monkeypatch.setitem(_REGISTRY, ReminderType.custom, boom)

    resp = test_client.post(
        "/rules/new",
        data={
            "type": "custom",
            "title": "원자성 테스트",
            "active": "on",
            "custom_repeat": "once",
            "custom_message": "메시지",
            "custom_run_at": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 500

    with Session() as s:
        count = s.query(ReminderRule).filter(ReminderRule.title == "원자성 테스트").count()
    assert count == 0, "rebuild 실패 시 규칙이 부분 커밋됨"
