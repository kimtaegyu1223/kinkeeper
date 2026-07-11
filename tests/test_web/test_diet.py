"""다이어트 진입점 게이팅 회귀 테스트 (다이어트 유지·기본 비활성 결정, 2026-07-11).

봇의 /몸무게 게이팅과 일관되게, 웹 /diet도 WEIGHT_FEATURE_ENABLED가 꺼져 있으면
DB를 건드리지 않고 비활성 안내를 주고 네비게이션에서 링크가 사라져야 한다.
라우트는 shared.db.get_session(전역 엔진)을 쓰므로 켜짐 경로는 테스트 컨테이너
엔진으로 monkeypatch한다(test_broadcast.py 패턴 재사용).
"""

from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import web.routes.diet as diet_route
from shared.config import settings
from web.auth import verify_admin
from web.main import app


def test_diet_disabled_when_flag_off(monkeypatch) -> None:
    """플래그 off면 /diet는 404 비활성 안내를 주고 nav에 다이어트 링크가 없어야 한다."""
    monkeypatch.setattr(settings, "weight_feature_enabled", False)
    app.dependency_overrides[verify_admin] = lambda: "admin"
    try:
        resp = TestClient(app).get("/diet")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404
    assert "꺼져" in resp.text
    assert 'href="/diet"' not in resp.text  # nav 링크도 숨겨져야 함


def test_diet_enabled_renders_and_shows_nav(db_engine, monkeypatch) -> None:
    """플래그 on이면 /diet는 200으로 목록을 렌더하고 nav에 다이어트 링크가 보여야 한다."""
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

    monkeypatch.setattr(settings, "weight_feature_enabled", True)
    monkeypatch.setattr(diet_route, "get_session", _get_session)
    app.dependency_overrides[verify_admin] = lambda: "admin"
    try:
        resp = TestClient(app).get("/diet")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert 'href="/diet"' in resp.text  # nav 링크 노출
