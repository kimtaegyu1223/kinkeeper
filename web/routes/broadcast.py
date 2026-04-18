from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from shared.db import get_session
from shared.enums import NotificationStatus
from shared.models import AdminBroadcast, FamilyMember, ScheduledNotification
from web.auth import verify_admin

router = APIRouter(prefix="/broadcast", dependencies=[Depends(verify_admin)])
templates = Jinja2Templates(directory="web/templates")


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
    member_ids: list[int] = Form(...),
    message: str = Form(...),
    admin: str = Depends(verify_admin),
) -> HTMLResponse:
    now = datetime.now(UTC)
    sent_ids: list[int] = []

    with get_session() as session:
        members = session.scalars(
            select(FamilyMember).where(
                FamilyMember.id.in_(member_ids),
                FamilyMember.telegram_user_id.isnot(None),
            )
        ).all()

        for m in members:
            notif = ScheduledNotification(
                scheduled_at=now,
                target_telegram_id=m.telegram_user_id,
                message=message.strip(),
                status=NotificationStatus.pending,
            )
            session.add(notif)
            session.flush()
            sent_ids.append(notif.id)

        broadcast = AdminBroadcast(
            sent_by=admin,
            target_member_ids=member_ids,
            message=message.strip(),
            sent_at=now,
            notification_ids=sent_ids,
        )
        session.add(broadcast)

        all_members = session.scalars(
            select(FamilyMember).where(FamilyMember.active.is_(True)).order_by(FamilyMember.name)
        ).all()

    result = {"ok": True, "msg": f"{len(sent_ids)}명에게 발송 예약됐습니다. (1분 내 전송)"}
    return templates.TemplateResponse(
        request, "broadcast.html", {"members": all_members, "result": result}
    )
