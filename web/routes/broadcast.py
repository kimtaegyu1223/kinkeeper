from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from shared.config import settings
from shared.db import get_session
from shared.enums import NotificationStatus
from shared.models import AdminBroadcast, FamilyMember, ScheduledNotification
from web.auth import verify_admin
from web.templating import templates

router = APIRouter(prefix="/broadcast", dependencies=[Depends(verify_admin)])


@router.get("", response_class=HTMLResponse)
def broadcast_form(request: Request) -> HTMLResponse:
    with get_session() as session:
        members = session.scalars(
            select(FamilyMember).where(FamilyMember.active.is_(True)).order_by(FamilyMember.name)
        ).all()
    return templates.TemplateResponse(
        request, "broadcast.html", {"members": members, "result": None}
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

        all_members = session.scalars(
            select(FamilyMember).where(FamilyMember.active.is_(True)).order_by(FamilyMember.name)
        ).all()

    result = {"ok": True, "msg": "그룹채널에 발송 예약됐습니다. (1분 내 전송)"}
    return templates.TemplateResponse(
        request, "broadcast.html", {"members": all_members, "result": result}
    )
