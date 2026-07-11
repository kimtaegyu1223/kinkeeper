"""대시보드("/") 라우트 테스트.

- 인증(verify_admin)이 적용된다.
- 향후 30일 내 pending 알림만 타임라인에 뜨고, 메시지의 HTML 태그가 제거된다.
- 범위 밖(30일 초과)·비pending 알림은 제외된다.
- 30일 내 생일 임박 구성원이 요약에 노출된다.
- 빈 상태에서도 500 없이 렌더된다.

라우트는 shared.db.get_session(전역 엔진)을 쓰므로 테스트 컨테이너 엔진으로
monkeypatch한다(test_members.py 패턴 재사용).
"""

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import web.routes.dashboard as dashboard_route
from shared.enums import NotificationStatus
from shared.generators._time import today_local
from shared.models import FamilyMember, ScheduledNotification
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

    monkeypatch.setattr(dashboard_route, "get_session", _get_session)
    app.dependency_overrides[verify_admin] = lambda: "admin"

    yield TestClient(app), Session

    app.dependency_overrides.clear()
    with _get_session() as s:
        s.query(ScheduledNotification).delete()
        s.query(FamilyMember).delete()


def test_dashboard_requires_auth() -> None:
    """verify_admin 미충족 시 401 (오버라이드 없이 호출)."""
    resp = TestClient(app).get("/")
    assert resp.status_code == 401


def test_dashboard_renders_pending_timeline_with_stripped_html(client) -> None:
    test_client, Session = client
    now = datetime.now(UTC)
    with Session() as s:
        # 30일 내 pending — HTML 태그가 요약에서 제거돼야 한다.
        s.add(
            ScheduledNotification(
                scheduled_at=now + timedelta(days=3, hours=1),
                target_telegram_id=-1001234567890,
                message="🎂 <b>홍길동</b>님의 생일이 <b>3일 후</b>입니다!",
                status=NotificationStatus.pending,
            )
        )
        # 범위 밖(40일) — 제외돼야 한다.
        s.add(
            ScheduledNotification(
                scheduled_at=now + timedelta(days=40),
                target_telegram_id=-1001234567890,
                message="범위밖알림마커",
                status=NotificationStatus.pending,
            )
        )
        # 이미 발송됨(sent) — 제외돼야 한다.
        s.add(
            ScheduledNotification(
                scheduled_at=now + timedelta(days=2),
                target_telegram_id=-1001234567890,
                message="발송된알림마커",
                status=NotificationStatus.sent,
            )
        )
        s.commit()

    resp = test_client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # 태그가 제거된 요약 텍스트는 보이고, 원본 태그는 보이지 않아야 한다.
    assert "홍길동님의 생일이 3일 후입니다!" in body
    assert "<b>홍길동</b>" not in body
    # 범위 밖·발송 완료 알림은 타임라인에 없어야 한다.
    assert "범위밖알림마커" not in body
    assert "발송된알림마커" not in body


def test_dashboard_shows_upcoming_birthday(client) -> None:
    test_client, Session = client
    today = today_local()
    upcoming = today + timedelta(days=5)
    with Session() as s:
        s.add(
            FamilyMember(
                name="생일임박이",
                birthday_solar=date(1990, upcoming.month, upcoming.day),
                active=True,
            )
        )
        # 30일 밖 생일은 요약에 뜨지 않아야 한다.
        far = today + timedelta(days=200)
        s.add(
            FamilyMember(
                name="생일먼사람",
                birthday_solar=date(1990, far.month, far.day),
                active=True,
            )
        )
        s.commit()

    resp = test_client.get("/")
    assert resp.status_code == 200
    assert "생일임박이" in resp.text
    assert "생일먼사람" not in resp.text


def test_dashboard_empty_state_renders(client) -> None:
    test_client, _ = client
    resp = test_client.get("/")
    assert resp.status_code == 200
    assert "향후 30일 내 예정된 알림이 없습니다." in resp.text
