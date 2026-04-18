from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from shared.db import get_session
from shared.models import FamilyMember, WeightLog
from web.auth import verify_admin

router = APIRouter(prefix="/diet", dependencies=[Depends(verify_admin)])
templates = Jinja2Templates(directory="web/templates")


@router.get("", response_class=HTMLResponse)
def diet_list(request: Request) -> HTMLResponse:
    with get_session() as session:
        members = session.scalars(select(FamilyMember).where(FamilyMember.active.is_(True))).all()

        by_member: dict[str, list[WeightLog]] = {}
        for m in members:
            logs = session.scalars(
                select(WeightLog)
                .where(WeightLog.member_id == m.id)
                .order_by(WeightLog.recorded_at.desc())
                .limit(20)
            ).all()
            by_member[m.name] = list(logs)

    return templates.TemplateResponse(request, "diet/list.html", {"by_member": by_member})
