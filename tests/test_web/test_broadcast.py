"""관리자 브로드캐스트 escape 회귀 테스트 (audit #14).

발송용 ScheduledNotification.message는 escape하고, 감사 로그(AdminBroadcast)는
원문을 보관한다. 라우트는 shared.db.get_session(전역 엔진)을 쓰므로 테스트
컨테이너 엔진으로 monkeypatch한다.
"""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import web.routes.broadcast as broadcast_route
from shared.config import settings
from shared.models import AdminBroadcast, ScheduledNotification
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

    monkeypatch.setattr(broadcast_route, "get_session", _get_session)
    monkeypatch.setattr(settings, "group_chat_id", -1001234567890)
    app.dependency_overrides[verify_admin] = lambda: "admin"

    yield TestClient(app), Session

    app.dependency_overrides.clear()
    with _get_session() as s:
        s.query(ScheduledNotification).delete()
        s.query(AdminBroadcast).delete()


def test_broadcast_escapes_notification_but_keeps_raw_log(client) -> None:
    test_client, Session = client
    raw = "내일 모임 3시 <장소 미정> & 준비물"

    resp = test_client.post("/broadcast", data={"message": raw})
    assert resp.status_code == 200

    with Session() as s:
        notif = s.query(ScheduledNotification).one()
        broadcast = s.query(AdminBroadcast).one()

    # 발송 메시지는 escape (parse_mode=HTML 발송이므로)
    assert notif.message == "내일 모임 3시 &lt;장소 미정&gt; &amp; 준비물"
    assert "<장소 미정>" not in notif.message
    # 감사 로그는 관리자 입력 원문 그대로
    assert broadcast.message == raw
