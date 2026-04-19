import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from shared.db import check_db_connection
from web.routes.broadcast import router as broadcast_router
from web.routes.diet import router as diet_router
from web.routes.health_checks import router as health_router
from web.routes.members import router as members_router
from web.routes.rules import router as rules_router

log = structlog.get_logger()

app = FastAPI(title="KinKeeper 관리자", docs_url=None, redoc_url=None)

app.include_router(members_router)
app.include_router(rules_router)
app.include_router(health_router)
app.include_router(broadcast_router)
app.include_router(diet_router)


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
def healthz() -> dict[str, str]:
    db_ok = check_db_connection()
    return {"status": "ok" if db_ok else "db_error", "db": "ok" if db_ok else "error"}
