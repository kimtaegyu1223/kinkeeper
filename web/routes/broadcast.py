from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from shared.config import settings
from shared.db import get_session
from shared.enums import NotificationStatus
from shared.models import AdminBroadcast, ScheduledNotification
from web.auth import verify_admin
from web.templating import templates

router = APIRouter(prefix="/broadcast", dependencies=[Depends(verify_admin)])


def _recent_broadcasts(session: object) -> list[AdminBroadcast]:
    """최근 발송 이력(최신순 10건)."""
    from sqlalchemy.orm import Session as SASession

    if not isinstance(session, SASession):
        return []
    return list(
        session.scalars(
            select(AdminBroadcast).order_by(AdminBroadcast.sent_at.desc()).limit(10)
        ).all()
    )


@router.get("", response_class=HTMLResponse)
def broadcast_form(request: Request) -> HTMLResponse:
    with get_session() as session:
        history = _recent_broadcasts(session)
    return templates.TemplateResponse(
        request, "broadcast.html", {"result": None, "history": history}
    )


@router.post("", response_class=HTMLResponse)
def send_broadcast(
    request: Request,
    message: str = Form(...),
    admin: str = Depends(verify_admin),
) -> HTMLResponse:
    now = datetime.now(UTC)
    text = message.strip()

    with get_session() as session:
        # 그룹채널에만 발송. 관리자 자유 입력이므로 escape (parse_mode=HTML 발송)
        notif = ScheduledNotification(
            scheduled_at=now,
            target_telegram_id=settings.group_chat_id,
            message=escape(text),
            status=NotificationStatus.pending,
        )
        session.add(notif)
        session.flush()

        # AdminBroadcast는 감사 로그이므로 관리자가 입력한 원문을 그대로 보관
        broadcast = AdminBroadcast(
            sent_by=admin,
            message=text,
            sent_at=now,
        )
        session.add(broadcast)
        session.flush()

        history = _recent_broadcasts(session)

    result = {"ok": True, "msg": "그룹채널에 발송 예약됐습니다. (1분 내 전송)"}
    return templates.TemplateResponse(
        request, "broadcast.html", {"result": result, "history": history}
    )
