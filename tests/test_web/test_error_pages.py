"""오류 페이지 통일 렌더 회귀 테스트 (revamp 마감 및 하드닝 후속).

HTTPException(폼 400·404·인증 401 등)을 JSON/평문이 아니라 디자인 시스템
오류 카드(HTML)로 통일해 노출한다. 상태코드와 인증 헤더는 보존한다. DB에
접근하지 않는 경로(인증 실패는 라우트 본문 이전에 발생)만 사용한다.

catch-all 핸들러: IntegrityError 외 미처리 예외(DataError 등)도 맨 500이 아니라
같은 스타일드 카드로 렌더한다. HTTPException은 여전히 자신의 핸들러로 흐른다.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Route

from web.main import app

_BOOM_PATH = "/__boom__"


@pytest.fixture
def boom_client() -> Iterator[TestClient]:
    """미등록 예외(RuntimeError)를 던지는 임시 라우트를 실 app에 붙여, 실제
    ServerErrorMiddleware→Exception 핸들러 배선을 통과하는 클라이언트를 준다.

    ServerErrorMiddleware는 핸들러가 응답을 만든 뒤에도 예외를 재-raise하므로
    (서버 로깅용) raise_server_exceptions=False로 응답을 받아본다. 다른 테스트에
    새지 않게 teardown에서 라우트를 제거한다.
    """

    async def _boom() -> None:
        raise RuntimeError("boom")  # HTTPException·IntegrityError 아님 → catch-all 경유

    app.add_api_route(_BOOM_PATH, _boom, methods=["GET"])
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.router.routes = [
            r for r in app.router.routes if not (isinstance(r, Route) and r.path == _BOOM_PATH)
        ]


def test_unauthorized_renders_styled_html_and_keeps_header() -> None:
    resp = TestClient(app, raise_server_exceptions=False).get("/members/1/edit")
    assert resp.status_code == 401
    # 브라우저 basic-auth 프롬프트를 위해 WWW-Authenticate 헤더가 보존돼야 한다.
    assert resp.headers.get("www-authenticate") == "Basic"
    # JSON이 아니라 스타일드 HTML 오류 카드로 노출된다.
    assert "text/html" in resp.headers["content-type"]
    assert "돌아가기" in resp.text


def test_unhandled_exception_renders_styled_500(boom_client) -> None:
    """미처리 예외가 맨 500(스택 노출)이 아니라 스타일드 HTML 카드로 렌더된다."""
    resp = boom_client.get(_BOOM_PATH)
    assert resp.status_code == 500
    assert "text/html" in resp.headers["content-type"]
    assert "돌아가기" in resp.text
    # 스택 트레이스가 응답으로 새지 않아야 한다.
    assert "Traceback" not in resp.text
    assert "RuntimeError" not in resp.text


def test_not_found_still_routed_to_http_handler_not_catchall() -> None:
    """HTTPException(404)은 catch-all이 아니라 자신의 핸들러로 흘러 404를 유지한다."""
    resp = TestClient(app, raise_server_exceptions=False).get("/__definitely_missing__")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    assert "돌아가기" in resp.text
