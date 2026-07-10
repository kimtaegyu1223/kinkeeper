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

        # 이름을 키로 쓰면 동명이인의 기록이 서로 덮어써 누락된다(name은 unique 아님).
        # 구성원별 항목 리스트로 담고 표시용 이름을 함께 넘긴다 (audit #62).
        member_logs: list[dict[str, object]] = []
        for m in members:
            logs = session.scalars(
                select(WeightLog)
                .where(WeightLog.member_id == m.id)
                .order_by(WeightLog.recorded_at.desc())
                .limit(20)
            ).all()
            member_logs.append({"name": m.name, "logs": list(logs)})

    return templates.TemplateResponse(request, "diet/list.html", {"member_logs": member_logs})
