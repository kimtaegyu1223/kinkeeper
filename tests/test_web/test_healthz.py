"""/healthz 상태코드 회귀 테스트 (audit #69).

DB 장애 시 body만 바꾸고 200을 주면 상태코드 기반 모니터가 장애를 놓친다.
DB 정상이면 200, 장애면 503을 반환해야 한다.
"""

from fastapi.testclient import TestClient

import web.main as web_main
from web.main import app


def test_healthz_ok_returns_200(monkeypatch) -> None:
    monkeypatch.setattr(web_main, "check_db_connection", lambda: True)
    resp = TestClient(app).get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}


def test_healthz_db_down_returns_503(monkeypatch) -> None:
    monkeypatch.setattr(web_main, "check_db_connection", lambda: False)
    resp = TestClient(app).get("/healthz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "db_error", "db": "error"}
