from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from shared.db import get_session
from shared.models import FamilyMember, HealthCheckRecord, HealthCheckType, MemberHealthCheckConfig
from web.auth import verify_admin

router = APIRouter(prefix="/health", dependencies=[Depends(verify_admin)])
templates = Jinja2Templates(directory="web/templates")


# ── 검진 항목 관리 ─────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
def list_check_types(request: Request) -> HTMLResponse:
    with get_session() as session:
        types = session.scalars(select(HealthCheckType).order_by(HealthCheckType.name)).all()
        members = session.scalars(
            select(FamilyMember).where(FamilyMember.active.is_(True)).order_by(FamilyMember.name)
        ).all()
    return templates.TemplateResponse(
        request, "health/list.html", {"types": types, "members": members}
    )


@router.get("/types/new", response_class=HTMLResponse)
def new_type_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "health/type_form.html", {"ct": None})


@router.post("/types/new")
def create_type(
    name: str = Form(...),
    period_years: int = Form(2),
    gender: str = Form(""),
    active: str = Form(""),
) -> RedirectResponse:
    with get_session() as session:
        session.add(
            HealthCheckType(
                name=name.strip(),
                period_years=period_years,
                gender=gender if gender in ("M", "F") else None,
                active=bool(active),
            )
        )
    return RedirectResponse("/health", status_code=303)


@router.get("/types/{type_id}/edit", response_class=HTMLResponse)
def edit_type_form(type_id: int, request: Request) -> HTMLResponse:
    with get_session() as session:
        ct = session.get(HealthCheckType, type_id)
    return templates.TemplateResponse(request, "health/type_form.html", {"ct": ct})


@router.post("/types/{type_id}/edit")
def update_type(
    type_id: int,
    name: str = Form(...),
    period_years: int = Form(2),
    gender: str = Form(""),
    active: str = Form(""),
) -> RedirectResponse:
    with get_session() as session:
        ct = session.get(HealthCheckType, type_id)
        if ct:
            ct.name = name.strip()
            ct.period_years = period_years
            ct.gender = gender if gender in ("M", "F") else None
            ct.active = bool(active)
    return RedirectResponse("/health", status_code=303)


@router.delete("/types/{type_id}")
def delete_type(type_id: int) -> Response:
    with get_session() as session:
        ct = session.get(HealthCheckType, type_id)
        if ct:
            session.delete(ct)
    return Response(status_code=200)


# ── 구성원별 검진 기록 ──────────────────────────────────────


@router.get("/records/{member_id}", response_class=HTMLResponse)
def member_records(member_id: int, request: Request) -> HTMLResponse:
    with get_session() as session:
        member = session.get(FamilyMember, member_id)
        check_types = session.scalars(
            select(HealthCheckType)
            .where(HealthCheckType.active.is_(True))
            .order_by(HealthCheckType.name)
        ).all()
        records = session.scalars(
            select(HealthCheckRecord)
            .where(HealthCheckRecord.member_id == member_id)
            .order_by(HealthCheckRecord.check_type_id, HealthCheckRecord.checked_at.desc())
        ).all()
        # check_type_id → 최근 기록 매핑
        latest_by_type: dict[int, HealthCheckRecord] = {}
        for r in records:
            if r.check_type_id not in latest_by_type:
                latest_by_type[r.check_type_id] = r
        # check_type_id → MemberHealthCheckConfig 매핑
        configs = session.scalars(
            select(MemberHealthCheckConfig).where(MemberHealthCheckConfig.member_id == member_id)
        ).all()
        config_by_type: dict[int, MemberHealthCheckConfig] = {c.check_type_id: c for c in configs}

    return templates.TemplateResponse(
        request,
        "health/records.html",
        {
            "member": member,
            "check_types": check_types,
            "latest_by_type": latest_by_type,
            "all_records": records,
            "config_by_type": config_by_type,
            "today": datetime.now(UTC).date(),
        },
    )


@router.post("/records/{member_id}/add")
def add_record(
    member_id: int,
    check_type_id: int = Form(...),
    checked_at: str = Form(...),
    note: str = Form(""),
) -> RedirectResponse:
    with get_session() as session:
        session.add(
            HealthCheckRecord(
                member_id=member_id,
                check_type_id=check_type_id,
                checked_at=date.fromisoformat(checked_at),
                note=note.strip() or None,
            )
        )
    return RedirectResponse(f"/health/records/{member_id}", status_code=303)


@router.post("/members/{member_id}/config/{check_type_id}")
def upsert_member_config(
    member_id: int,
    check_type_id: int,
    period_years: str = Form(""),
    active: str = Form(""),
) -> RedirectResponse:
    with get_session() as session:
        config = session.scalar(
            select(MemberHealthCheckConfig).where(
                MemberHealthCheckConfig.member_id == member_id,
                MemberHealthCheckConfig.check_type_id == check_type_id,
            )
        )
        period: int | None = int(period_years) if period_years.strip() else None
        is_active = bool(active)
        if config is None:
            session.add(
                MemberHealthCheckConfig(
                    member_id=member_id,
                    check_type_id=check_type_id,
                    period_years=period,
                    active=is_active,
                )
            )
        else:
            config.period_years = period
            config.active = is_active
    return RedirectResponse(f"/health/records/{member_id}", status_code=303)


@router.delete("/records/{record_id}")
def delete_record(record_id: int) -> Response:
    with get_session() as session:
        r = session.get(HealthCheckRecord, record_id)
        if r:
            session.delete(r)
    return Response(status_code=200)
