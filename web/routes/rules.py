import json

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from shared.db import get_session
from shared.enums import ReminderType
from shared.models import ReminderRule
from web.auth import verify_admin

router = APIRouter(prefix="/rules", dependencies=[Depends(verify_admin)])
templates = Jinja2Templates(directory="web/templates")


def _parse_int_list(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


@router.get("", response_class=HTMLResponse)
def list_rules(request: Request) -> HTMLResponse:
    with get_session() as session:
        rules = session.scalars(
            select(ReminderRule).order_by(ReminderRule.type, ReminderRule.title)
        ).all()
    return templates.TemplateResponse(request, "rules/list.html", {"rules": rules})


@router.get("/new", response_class=HTMLResponse)
def new_rule_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "rules/form.html", {"rule": None})


@router.post("/new")
def create_rule(
    type: str = Form(...),
    title: str = Form(...),
    lead_times_days: str = Form(""),
    config: str = Form("{}"),
    active: str = Form(""),
) -> RedirectResponse:
    with get_session() as session:
        rule = ReminderRule(
            type=ReminderType(type),
            title=title.strip(),
            lead_times_days=_parse_int_list(lead_times_days),
            config=json.loads(config or "{}"),
            active=bool(active),
        )
        session.add(rule)
    return RedirectResponse("/rules", status_code=303)


@router.get("/{rule_id}/edit", response_class=HTMLResponse)
def edit_rule_form(rule_id: int, request: Request) -> HTMLResponse:
    with get_session() as session:
        rule = session.get(ReminderRule, rule_id)
    return templates.TemplateResponse(request, "rules/form.html", {"rule": rule})


@router.post("/{rule_id}/edit")
def update_rule(
    rule_id: int,
    type: str = Form(...),
    title: str = Form(...),
    lead_times_days: str = Form(""),
    config: str = Form("{}"),
    active: str = Form(""),
) -> RedirectResponse:
    with get_session() as session:
        rule = session.get(ReminderRule, rule_id)
        if rule:
            rule.type = ReminderType(type)
            rule.title = title.strip()
            rule.lead_times_days = _parse_int_list(lead_times_days)
            rule.config = json.loads(config or "{}")
            rule.active = bool(active)
    return RedirectResponse("/rules", status_code=303)


@router.delete("/{rule_id}")
def delete_rule(rule_id: int) -> Response:
    with get_session() as session:
        rule = session.get(ReminderRule, rule_id)
        if rule:
            session.delete(rule)
    return Response(status_code=200)
