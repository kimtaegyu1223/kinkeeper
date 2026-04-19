
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from shared.db import get_session
from shared.enums import ReminderType
from shared.models import FamilyMember, ReminderRule
from web.auth import verify_admin

router = APIRouter(prefix="/rules", dependencies=[Depends(verify_admin)])
templates = Jinja2Templates(directory="web/templates")


def _parse_int_list(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip().lstrip("-").isdigit()]


def _build_config_and_leads(  # noqa: PLR0912
    rule_type: str,
    form: dict[str, str],
) -> tuple[dict[str, object], list[int]]:
    """폼 데이터 → (config dict, lead_times_days)."""
    config: dict[str, object] = {}
    leads: list[int] = []

    if rule_type == "birthday":
        config["member_id"] = int(form.get("birthday_member_id") or 0)
        config["use_lunar"] = form.get("birthday_use_lunar") == "1"
        config["hour"] = int(form.get("birthday_hour") or 9)
        leads = _parse_int_list(form.get("birthday_lead_times") or "14,7,3,1,0")

    elif rule_type == "holiday":
        config["name"] = form.get("holiday_name") or ""
        config["lunar_month"] = int(form.get("holiday_lunar_month") or 1)
        config["lunar_day"] = int(form.get("holiday_lunar_day") or 1)
        config["hour"] = int(form.get("holiday_hour") or 9)
        leads = _parse_int_list(form.get("holiday_lead_times") or "30,7,2,0")

    elif rule_type == "health_check":
        config["member_id"] = int(form.get("health_member_id") or 0)
        config["period"] = form.get("health_period") or "yearly"
        config["anchor_date"] = form.get("health_anchor_date") or ""
        config["hour"] = int(form.get("health_hour") or 9)
        leads = _parse_int_list(form.get("health_lead_times") or "30,7")

    elif rule_type == "diet_report":
        config["cadence"] = form.get("diet_cadence") or "weekly"
        config["weekday"] = int(form.get("diet_weekday") or 0)
        config["hour"] = int(form.get("diet_hour") or 9)
        leads = []

    elif rule_type == "custom":
        repeat = form.get("custom_repeat") or "once"
        config["hour"] = int(form.get("custom_hour") or 9)
        config["message"] = form.get("custom_message") or ""
        if repeat == "yearly":
            config["repeat"] = "yearly"
            config["month"] = int(form.get("custom_month") or 1)
            config["day"] = int(form.get("custom_day") or 1)
            leads = _parse_int_list(form.get("custom_lead_times") or "0")
        else:
            config["run_at"] = form.get("custom_run_at") or ""
            leads = [0]

    return config, leads


def _get_members() -> list[FamilyMember]:
    with get_session() as session:
        return list(
            session.scalars(
                select(FamilyMember).where(FamilyMember.active.is_(True)).order_by(FamilyMember.name)
            ).all()
        )


@router.get("", response_class=HTMLResponse)
def list_rules(request: Request) -> HTMLResponse:
    with get_session() as session:
        rules = session.scalars(
            select(ReminderRule).order_by(ReminderRule.type, ReminderRule.title)
        ).all()
    return templates.TemplateResponse(request, "rules/list.html", {"rules": rules})


@router.get("/new", response_class=HTMLResponse)
def new_rule_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "rules/form.html", {"rule": None, "members": _get_members()}
    )


@router.post("/new")
async def create_rule(request: Request, active: str = Form("")) -> RedirectResponse:
    form_data = await request.form()
    form = dict(form_data)
    rule_type = str(form.get("type") or "")
    title = str(form.get("title") or "").strip()
    config, leads = _build_config_and_leads(rule_type, {k: str(v) for k, v in form.items()})

    with get_session() as session:
        rule = ReminderRule(
            type=ReminderType(rule_type),
            title=title,
            lead_times_days=leads,
            config=config,
            active=bool(active),
        )
        session.add(rule)
    return RedirectResponse("/rules", status_code=303)


@router.get("/{rule_id}/edit", response_class=HTMLResponse)
def edit_rule_form(rule_id: int, request: Request) -> HTMLResponse:
    with get_session() as session:
        rule = session.get(ReminderRule, rule_id)
    return templates.TemplateResponse(
        request, "rules/form.html", {"rule": rule, "members": _get_members()}
    )


@router.post("/{rule_id}/edit")
async def update_rule(
    rule_id: int, request: Request, active: str = Form("")
) -> RedirectResponse:
    form_data = await request.form()
    form = dict(form_data)
    rule_type = str(form.get("type") or "")
    title = str(form.get("title") or "").strip()
    config, leads = _build_config_and_leads(rule_type, {k: str(v) for k, v in form.items()})

    with get_session() as session:
        rule = session.get(ReminderRule, rule_id)
        if rule:
            rule.type = ReminderType(rule_type)
            rule.title = title
            rule.lead_times_days = leads
            rule.config = config
            rule.active = bool(active)
    return RedirectResponse("/rules", status_code=303)


@router.delete("/{rule_id}")
def delete_rule(rule_id: int) -> Response:
    with get_session() as session:
        rule = session.get(ReminderRule, rule_id)
        if rule:
            session.delete(rule)
    return Response(status_code=200)
