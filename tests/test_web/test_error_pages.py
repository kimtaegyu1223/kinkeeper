"""오류 페이지 통일 렌더 회귀 테스트 (revamp 마감).

HTTPException(폼 400·404·인증 401 등)을 JSON/평문이 아니라 디자인 시스템
오류 카드(HTML)로 통일해 노출한다. 상태코드와 인증 헤더는 보존한다. DB에
접근하지 않는 경로(인증 실패는 라우트 본문 이전에 발생)만 사용한다.
"""

from fastapi.testclient import TestClient

from web.main import app


def test_unauthorized_renders_styled_html_and_keeps_header() -> None:
    resp = TestClient(app, raise_server_exceptions=False).get("/members/1/edit")
    assert resp.status_code == 401
    # 브라우저 basic-auth 프롬프트를 위해 WWW-Authenticate 헤더가 보존돼야 한다.
    assert resp.headers.get("www-authenticate") == "Basic"
    # JSON이 아니라 스타일드 HTML 오류 카드로 노출된다.
    assert "text/html" in resp.headers["content-type"]
    assert "돌아가기" in resp.text
