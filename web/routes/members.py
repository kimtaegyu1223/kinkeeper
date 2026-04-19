from datetime import date

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from shared.db import get_session
from shared.models import FamilyMember
from web.auth import verify_admin

router = APIRouter(prefix="/members", dependencies=[Depends(verify_admin)])
templates = Jinja2Templates(directory="web/templates")


@router.get("", response_class=HTMLResponse)
def list_members(request: Request) -> HTMLResponse:
    with get_session() as session:
        members = session.scalars(select(FamilyMember).order_by(FamilyMember.name)).all()
    return templates.TemplateResponse(request, "members/list.html", {"members": members})


@router.get("/new", response_class=HTMLResponse)
def new_member_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "members/form.html", {"member": None})


def _parse_lunar(month: str, day: str) -> date | None:
    """음력 월/일 → DATE (연도는 2000으로 고정, 월/일만 의미 있음)."""
    m = int(month) if month.strip() else 0
    d = int(day) if day.strip() else 0
    if m and d:
        return date(2000, m, d)
    return None


@router.post("/new")
def create_member(
    name: str = Form(...),
    telegram_user_id: str = Form(""),
    birthday_solar: str = Form(""),
    birthday_lunar_month: str = Form(""),
    birthday_lunar_day: str = Form(""),
    active: str = Form(""),
) -> RedirectResponse:
    with get_session() as session:
        member = FamilyMember(
            name=name.strip(),
            telegram_user_id=int(telegram_user_id) if telegram_user_id.strip() else None,
            birthday_solar=date.fromisoformat(birthday_solar) if birthday_solar else None,
            birthday_lunar=_parse_lunar(birthday_lunar_month, birthday_lunar_day),
            active=bool(active),
        )
        session.add(member)
    return RedirectResponse("/members", status_code=303)


@router.get("/{member_id}/edit", response_class=HTMLResponse)
def edit_member_form(member_id: int, request: Request) -> HTMLResponse:
    with get_session() as session:
        member = session.get(FamilyMember, member_id)
    return templates.TemplateResponse(request, "members/form.html", {"member": member})


@router.post("/{member_id}/edit")
def update_member(
    member_id: int,
    name: str = Form(...),
    telegram_user_id: str = Form(""),
    birthday_solar: str = Form(""),
    birthday_lunar_month: str = Form(""),
    birthday_lunar_day: str = Form(""),
    active: str = Form(""),
) -> RedirectResponse:
    with get_session() as session:
        member = session.get(FamilyMember, member_id)
        if member:
            member.name = name.strip()
            member.telegram_user_id = int(telegram_user_id) if telegram_user_id.strip() else None
            member.birthday_solar = date.fromisoformat(birthday_solar) if birthday_solar else None
            member.birthday_lunar = _parse_lunar(birthday_lunar_month, birthday_lunar_day)
            member.active = bool(active)
    return RedirectResponse("/members", status_code=303)


@router.delete("/{member_id}")
def delete_member(member_id: int) -> Response:
    with get_session() as session:
        member = session.get(FamilyMember, member_id)
        if member:
            session.delete(member)
    return Response(status_code=200)
