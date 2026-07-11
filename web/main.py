"""관리자 웹 앱 진입점 — 라우터 등록·전역 미들웨어.

FastAPI 앱을 만들고 라우터(구성원/규칙/건강검진/공지)를 묶는다. CSRF 최소 방어·
request_id 로깅·무결성 오류의 400 변환·시작 시 설정 검증·/healthz를 여기서 정의한다.
"""

import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from shared.config import settings
from shared.db import check_db_connection
from web.routes.broadcast import router as broadcast_router
from web.routes.dashboard import router as dashboard_router
from web.routes.health_checks import router as health_router
from web.routes.members import router as members_router
from web.routes.rules import router as rules_router
from web.templating import templates

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # 필수 설정(토큰/그룹ID/tz) 검증 — 누락·오류면 즉시 중단 (audit #19, #75).
    settings.validate_runtime()
    yield


app = FastAPI(title="KinKeeper 관리자", docs_url=None, redoc_url=None, lifespan=lifespan)

app.mount("/static", StaticFiles(directory="web/static"), name="static")

app.include_router(dashboard_router)
app.include_router(members_router)
app.include_router(rules_router)
app.include_router(health_router)
app.include_router(broadcast_router)


_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# 상태코드별 안내 이모지. 미정의는 ⚠️로 폴백한다.
_ERROR_EMOJI = {400: "✏️", 401: "🔒", 403: "🚫", 404: "🔍"}


def _error_page(
    request: Request, status: int, detail: str, headers: Mapping[str, str] | None = None
) -> Response:
    """오류를 디자인 시스템 카드로 통일 렌더한다 (폼 400 detail 노출 방식 통일).

    HTTPException detail(기존 JSON)과 IntegrityError(기존 평문)를 하나의 스타일드
    페이지로 합쳐, 사용자가 안내문과 '돌아가기'를 일관되게 보게 한다. 상태코드와
    헤더(401의 WWW-Authenticate 등)는 보존한다.
    """
    resp = templates.TemplateResponse(
        request,
        "error.html",
        {"status": status, "detail": detail, "emoji": _ERROR_EMOJI.get(status, "⚠️")},
        status_code=status,
    )
    if headers:
        resp.headers.update(headers)
    return resp


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """HTTPException(400/401/403/404 등)을 스타일드 오류 페이지로 통일한다.

    폼 검증 실패(form_utils의 400)·미존재 리소스(404)·인증 실패(401)가 모두 같은
    형태로 노출된다.
    """
    detail = (
        exc.detail if isinstance(exc.detail, str) and exc.detail else "요청을 처리할 수 없습니다."
    )
    return _error_page(request, exc.status_code, detail, exc.headers)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: Exception) -> Response:
    """DB 무결성 제약 위반(중복 등록 등)을 500 대신 400으로 안내한다 (audit #46, #47).

    예: telegram_user_id/검진 항목명 중복, 같은 검진 기록 중복 제출.
    """
    log.warning("무결성 제약 위반", method=request.method, path=request.url.path)
    return _error_page(
        request,
        400,
        "이미 등록된 값이거나 중복된 데이터입니다. 입력값을 확인해주세요.",
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """IntegrityError 외 미처리 예외(DataError 등)를 스타일드 500 페이지로 렌더한다.

    기존엔 이 경로가 맨 500(스택 비노출)이었다. HTTPException·IntegrityError는 더
    구체적 핸들러가 먼저 잡으므로(Starlette가 Exception 핸들러만 ServerErrorMiddleware로
    분리) 여기 도달하지 않는다. 로그는 경로·예외 타입명만 남기고 폼 값·쿼리 파라미터 등
    사용자 입력은 남기지 않는다 (PII 방지).
    """
    log.error(
        "미처리 예외",
        method=request.method,
        path=request.url.path,
        error_type=type(exc).__name__,
    )
    return _error_page(
        request,
        500,
        "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
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


# /healthz DB 프로브 결과를 몇 초 캐시해 tailnet 노출 인스턴스 연타 시 커넥션
# 풀(15개)이 마르지 않게 한다 (audit #69 후속). 스테일로 인한 경보 지연 몇 초는 허용.
_HEALTHZ_PROBE_TTL_SECONDS = 5.0
_healthz_probe_lock = threading.Lock()
# (만료 monotonic 시각, db_ok). None이면 아직 프로브 전.
_healthz_probe_cache: tuple[float, bool] | None = None


def _probe_db_cached() -> bool:
    """DB 프로브 결과를 TTL 동안 캐시하고, 만료 시 한 스레드만 재프로브한다.

    healthz는 sync 엔드포인트라 스레드풀에서 병렬 실행된다. 락 밖 1차 확인으로 캐시
    적중 시 락 경합 자체를 피하고, 만료 시엔 락 뒤 2차 확인으로 동시 도착 스레드들의
    중복 프로브(thundering herd)를 막아 재프로브 1회·커넥션 1개만 쓰게 한다.
    """
    global _healthz_probe_cache
    cache = _healthz_probe_cache
    if cache is not None and time.monotonic() < cache[0]:
        return cache[1]
    with _healthz_probe_lock:
        # 락 대기 중 다른 스레드가 이미 갱신했으면 그 결과를 재사용한다.
        cache = _healthz_probe_cache
        if cache is not None and time.monotonic() < cache[0]:
            return cache[1]
        db_ok = check_db_connection()
        _healthz_probe_cache = (time.monotonic() + _HEALTHZ_PROBE_TTL_SECONDS, db_ok)
        return db_ok


@app.get("/healthz")
def healthz() -> JSONResponse:
    # DB 장애 시 body만 바꾸고 200을 주면 상태코드 기반 모니터가 장애를 놓친다 (audit #69).
    # 프로브 결과는 몇 초 캐시된다(_probe_db_cached) — 연타 시 커넥션 풀 고갈 방지.
    db_ok = _probe_db_cached()
    body = {"status": "ok" if db_ok else "db_error", "db": "ok" if db_ok else "error"}
    return JSONResponse(body, status_code=200 if db_ok else 503)
