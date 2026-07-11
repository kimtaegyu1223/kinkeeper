from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from shared.config import settings
from shared.db import get_session
from shared.models import FamilyMember, WeightLog
from web.auth import verify_admin
from web.templating import templates

router = APIRouter(prefix="/diet", dependencies=[Depends(verify_admin)])


@router.get("", response_class=HTMLResponse)
def diet_list(request: Request) -> HTMLResponse:
    # 봇 쪽 /몸무게 게이팅("몸무게 기능은 현재 꺼져 있습니다.")과 동작을 맞춘다.
    # 플래그가 꺼져 있으면 DB를 건드리지 않고 비활성 안내 페이지를 준다
    # (다이어트 기능 유지·기본 비활성 결정, 2026-07-11).
    if not settings.weight_feature_enabled:
        return templates.TemplateResponse(request, "diet/disabled.html", {}, status_code=404)

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
