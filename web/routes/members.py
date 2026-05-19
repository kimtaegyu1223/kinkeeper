from datetime import date

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from shared.db import get_session
from shared.enums import ReminderType
from shared.models import FamilyMember, ReminderRule
from shared.scheduler_utils import rebuild_for_rule
from web.auth import verify_admin

router = APIRouter(prefix="/members", dependencies=[Depends(verify_admin)])
templates = Jinja2Templates(directory="web/templates")

_DEFAULT_BIRTHDAY_LEADS = [7, 3, 0]
_DEFAULT_BIRTHDAY_HOUR = 9


def _parse_lunar(month: str, day: str) -> date | None:
    m = int(month) if month.strip() else 0
    d = int(day) if day.strip() else 0
    if m and d:
        return date(2000, m, d)
    return None


def _parse_gender(gender: str) -> str | None:
    return gender if gender in ("M", "F") else None


def _ensure_birthday_rule(session: object, member: FamilyMember) -> ReminderRule | None:
    from sqlalchemy.orm import Session as SASession

    if not isinstance(session, SASession):
        return None
    if not member.birthday_solar and not member.birthday_lunar:
        return None

    existing = session.scalar(
        select(ReminderRule).where(
            ReminderRule.type == ReminderType.birthday,
            ReminderRule.config["member_id"].as_integer() == member.id,
        )
    )

    use_lunar = bool(member.birthday_lunar and not member.birthday_solar)
    config: dict[str, object] = {
        "member_id": member.id,
        "use_lunar": use_lunar,
        "hour": _DEFAULT_BIRTHDAY_HOUR,
    }

    if existing:
        existing.config = config
        existing.lead_times_days = _DEFAULT_BIRTHDAY_LEADS
        existing.active = True
        return existing
    else:
        rule = ReminderRule(
            type=ReminderType.birthday,
            title=f"{member.name} 생일 알림",
            lead_times_days=_DEFAULT_BIRTHDAY_LEADS,
            config=config,
            active=True,
        )
        session.add(rule)
        return rule


@router.get("", response_class=HTMLResponse)
def list_members(request: Request) -> HTMLResponse:
    with get_session() as session:
        members = session.scalars(select(FamilyMember).order_by(FamilyMember.name)).all()
    return templates.TemplateResponse(request, "members/list.html", {"members": members})


@router.get("/new", response_class=HTMLResponse)
def new_member_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "members/form.html", {"member": None})


@router.post("/new")
def create_member(
    name: str = Form(...),
    telegram_user_id: str = Form(""),
    birthday_solar: str = Form(""),
    birthday_lunar_month: str = Form(""),
    birthday_lunar_day: str = Form(""),
    gender: str = Form(""),
    active: str = Form(""),
    height_cm: str = Form(""),
    diet_active: str = Form(""),
) -> RedirectResponse:
    rule_id: int | None = None
    with get_session() as session:
        member = FamilyMember(
            name=name.strip(),
            telegram_user_id=int(telegram_user_id) if telegram_user_id.strip() else None,
            birthday_solar=date.fromisoformat(birthday_solar) if birthday_solar else None,
            birthday_lunar=_parse_lunar(birthday_lunar_month, birthday_lunar_day),
            gender=_parse_gender(gender),
            active=bool(active),
            height_cm=int(height_cm) if height_cm.strip() else None,
            diet_active=bool(diet_active),
        )
        session.add(member)
        session.flush()
        rule = _ensure_birthday_rule(session, member)
        if rule:
            session.flush()
            rule_id = rule.id

    if rule_id:
        rebuild_for_rule(rule_id)

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
    gender: str = Form(""),
    active: str = Form(""),
    height_cm: str = Form(""),
    diet_active: str = Form(""),
) -> RedirectResponse:
    rule_id: int | None = None
    with get_session() as session:
        member = session.get(FamilyMember, member_id)
        if member:
            member.name = name.strip()
            member.telegram_user_id = int(telegram_user_id) if telegram_user_id.strip() else None
            member.birthday_solar = date.fromisoformat(birthday_solar) if birthday_solar else None
            member.birthday_lunar = _parse_lunar(birthday_lunar_month, birthday_lunar_day)
            member.gender = _parse_gender(gender)
            member.active = bool(active)
            member.height_cm = int(height_cm) if height_cm.strip() else None
            member.diet_active = bool(diet_active)
            rule = _ensure_birthday_rule(session, member)
            if rule:
                session.flush()
                rule_id = rule.id

    if rule_id:
        rebuild_for_rule(rule_id)

    return RedirectResponse("/members", status_code=303)


@router.delete("/{member_id}")
def delete_member(member_id: int) -> Response:
    with get_session() as session:
        member = session.get(FamilyMember, member_id)
        if member:
            session.delete(member)
    return Response(status_code=200)
