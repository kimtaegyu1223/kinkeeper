"""관리자 웹 앱 진입점 — 라우터 등록·전역 미들웨어.

FastAPI 앱을 만들고 라우터(구성원/규칙/건강검진/공지/다이어트)를 묶는다. CSRF 최소 방어·
request_id 로깅·무결성 오류의 400 변환·시작 시 설정 검증·/healthz를 여기서 정의한다.
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError

from shared.config import settings
from shared.db import check_db_connection
from web.routes.broadcast import router as broadcast_router
from web.routes.diet import router as diet_router
from web.routes.health_checks import router as health_router
from web.routes.members import router as members_router
from web.routes.rules import router as rules_router

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # 필수 설정(토큰/그룹ID/tz) 검증 — 누락·오류면 즉시 중단 (audit #19, #75).
    settings.validate_runtime()
    yield


app = FastAPI(title="KinKeeper 관리자", docs_url=None, redoc_url=None, lifespan=lifespan)

app.include_router(members_router)
app.include_router(rules_router)
app.include_router(health_router)
app.include_router(broadcast_router)
app.include_router(diet_router)


_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: Exception) -> Response:
    """DB 무결성 제약 위반(중복 등록 등)을 500 대신 400으로 안내한다 (audit #46, #47).

    예: telegram_user_id/검진 항목명 중복, 같은 검진 기록 중복 제출.
    """
    log.warning("무결성 제약 위반", method=request.method, path=request.url.path)
    return HTMLResponse(
        "이미 등록된 값이거나 중복된 데이터입니다. 입력값을 확인해주세요.",
        status_code=400,
    )


@app.middleware("http")
async def csrf_protect_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """상태 변경 요청의 크로스사이트 위조(CSRF)를 최소 방어한다 (audit #37).

    브라우저가 붙이는 Sec-Fetch-Site 헤더가 cross-site/same-site면 거부하고
    same-origin/none(주소창 직접 입력·북마크)만 허용한다. 헤더가 없는 요청
    (구형 클라이언트·curl 등)은 그대로 통과시킨다.
    """
    if request.method not in _SAFE_METHODS:
        site = request.headers.get("sec-fetch-site")
        if site is not None and site not in ("same-origin", "none"):
            return Response("교차 사이트 요청이 차단되었습니다.", status_code=403)
    return await call_next(request)


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = str(uuid.uuid4())[:8]
    with structlog.contextvars.bound_contextvars(request_id=request_id):
        response = await call_next(request)
        log.info(
            "요청 처리",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
        )
        return response


@app.get("/", response_class=HTMLResponse)
def root() -> RedirectResponse:
    return RedirectResponse("/members")


@app.get("/healthz")
def healthz() -> JSONResponse:
    # DB 장애 시 body만 바꾸고 200을 주면 상태코드 기반 모니터가 장애를 놓친다 (audit #69).
    db_ok = check_db_connection()
    body = {"status": "ok" if db_ok else "db_error", "db": "ok" if db_ok else "error"}
    return JSONResponse(body, status_code=200 if db_ok else 503)
