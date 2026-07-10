from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from shared.db import get_session
from shared.enums import ReminderType
from shared.generators import _REGISTRY, rebuild_for_rule
from shared.models import FamilyMember, ReminderRule
from web.auth import verify_admin
from web.form_utils import parse_int_default, require_range, validate_iso_datetime

router = APIRouter(prefix="/rules", dependencies=[Depends(verify_admin)])
templates = Jinja2Templates(directory="web/templates")


def _parse_int_list(raw: str) -> list[int]:
    # isdigit() 필터는 '--3'(lstrip 후 '3')이나 위첨자 '²'를 통과시켜 int()에서
    # ValueError→500을 냈다. 정수로 변환되는 토큰만 남기고 나머지는 무시한다 (audit #58).
    result: list[int] = []
    for x in raw.split(","):
        s = x.strip()
        if not s:
            continue
        try:
            result.append(int(s))
        except ValueError:
            continue
    return result


def _hour(form: dict[str, str], key: str) -> int:
    return require_range(parse_int_default(form.get(key), "알림 시각", 9), "알림 시각", 0, 23)


def _build_config_and_leads(
    rule_type: str,
    form: dict[str, str],
) -> tuple[dict[str, object], list[int]]:
    """폼 데이터 → (config dict, lead_times_days)."""
    config: dict[str, object] = {}
    leads: list[int] = []

    if rule_type == "birthday":
        config["member_id"] = parse_int_default(form.get("birthday_member_id"), "대상 구성원", 0)
        config["use_lunar"] = form.get("birthday_use_lunar") == "1"
        config["hour"] = _hour(form, "birthday_hour")
        leads = _parse_int_list(form.get("birthday_lead_times") or "14,7,3,1,0")

    elif rule_type == "holiday":
        config["name"] = form.get("holiday_name") or ""
        config["lunar_month"] = require_range(
            parse_int_default(form.get("holiday_lunar_month"), "음력 월", 1), "음력 월", 1, 12
        )
        config["lunar_day"] = require_range(
            parse_int_default(form.get("holiday_lunar_day"), "음력 일", 1), "음력 일", 1, 30
        )
        config["hour"] = _hour(form, "holiday_hour")
        leads = _parse_int_list(form.get("holiday_lead_times") or "30,7,2,0")

    elif rule_type == "custom":
        repeat = form.get("custom_repeat") or "once"
        config["hour"] = _hour(form, "custom_hour")
        config["message"] = form.get("custom_message") or ""
        if repeat == "yearly":
            config["repeat"] = "yearly"
            config["month"] = require_range(
                parse_int_default(form.get("custom_month"), "월", 1), "월", 1, 12
            )
            config["day"] = require_range(
                parse_int_default(form.get("custom_day"), "일", 1), "일", 1, 31
            )
            config["use_lunar"] = form.get("custom_use_lunar") == "1"
            leads = _parse_int_list(form.get("custom_lead_times") or "0")
        else:
            # run_at은 문자열로 저장하되 ISO 형식만 허용(생성기가 fromisoformat) (audit #18).
            config["run_at"] = validate_iso_datetime(form.get("custom_run_at"), "예약 일시")
            leads = [0]

    return config, leads


def _parse_rule_type(rule_type: str) -> ReminderType:
    """폼 type 값을 ReminderType으로 변환. 알 수 없는 값은 500 대신 400 (audit #59)."""
    try:
        parsed = ReminderType(rule_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="알 수 없는 규칙 유형입니다.") from None
    # '규칙 생성 가능 타입'의 단일 출처는 _REGISTRY(등록 생성기가 있는 타입)다.
    # 건강검진·다이어트 리포트는 _REGISTRY에 없고 전용 생성기
    # (rebuild_health_checks/rebuild_diet_reports)가 규칙과 무관하게 자동 발송하므로
    # 규칙으로 저장해도 조용히 무동작한다(유령 UI). 저장을 거부한다 (audit #10/#22).
    if parsed not in _REGISTRY:
        raise HTTPException(
            status_code=400,
            detail="건강검진·다이어트 리포트는 규칙으로 설정하지 않습니다(자동 발송됩니다).",
        )
    return parsed


def _validate_rule_target(rule_type: str, config: dict[str, object]) -> None:
    """생일 규칙은 대상 구성원(member_id)이 필수다.

    폼 '대상 구성원' select은 required가 아니라 미선택 시 member_id=0으로 저장되는데,
    birthday.generate가 member_id falsy면 조기 반환해 알림이 영원히 생성되지 않는
    좀비 규칙이 된다. 저장 단계에서 거부한다 (audit #60).
    """
    if rule_type == "birthday" and not config.get("member_id"):
        raise HTTPException(status_code=400, detail="대상 구성원을 선택해주세요.")


def _get_members() -> list[FamilyMember]:
    with get_session() as session:
        return list(
            session.scalars(
                select(FamilyMember)
                .where(FamilyMember.active.is_(True))
                .order_by(FamilyMember.name)
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
    reminder_type = _parse_rule_type(rule_type)
    title = str(form.get("title") or "").strip()
    config, leads = _build_config_and_leads(rule_type, {k: str(v) for k, v in form.items()})
    _validate_rule_target(rule_type, config)

    is_active = bool(active)
    with get_session() as session:
        rule = ReminderRule(
            type=reminder_type,
            title=title,
            lead_times_days=leads,
            config=config,
            active=is_active,
        )
        session.add(rule)
        session.flush()
        # 규칙 저장과 알림 재빌드를 같은 트랜잭션에서 수행해 부분 커밋을 막는다 (audit #63).
        if is_active:
            rebuild_for_rule(rule.id, session)
    return RedirectResponse("/rules", status_code=303)


@router.get("/{rule_id}/edit", response_class=HTMLResponse)
def edit_rule_form(rule_id: int, request: Request) -> HTMLResponse:
    with get_session() as session:
        rule = session.get(ReminderRule, rule_id)
        # 없는 id는 '추가' 폼을 렌더하지 않고 404 (audit #61).
        if rule is None:
            raise HTTPException(status_code=404, detail="규칙을 찾을 수 없습니다.")
    return templates.TemplateResponse(
        request, "rules/form.html", {"rule": rule, "members": _get_members()}
    )


@router.post("/{rule_id}/edit")
async def update_rule(rule_id: int, request: Request, active: str = Form("")) -> RedirectResponse:
    form_data = await request.form()
    form = dict(form_data)
    rule_type = str(form.get("type") or "")
    reminder_type = _parse_rule_type(rule_type)
    title = str(form.get("title") or "").strip()
    config, leads = _build_config_and_leads(rule_type, {k: str(v) for k, v in form.items()})
    _validate_rule_target(rule_type, config)

    is_active = bool(active)
    with get_session() as session:
        rule = session.get(ReminderRule, rule_id)
        # 없는 id에 대한 조용한 no-op 대신 404로 알린다 (audit #61).
        if rule is None:
            raise HTTPException(status_code=404, detail="규칙을 찾을 수 없습니다.")
        rule.type = reminder_type
        rule.title = title
        rule.lead_times_days = leads
        rule.config = config
        rule.active = is_active
        # is_active 여부와 무관하게 항상 재빌드해야 비활성 전환 시에도 기존
        # pending 알림이 취소된다(내부에서 비활성이면 취소만 하고 반환) (audit #15).
        # 규칙 저장과 같은 트랜잭션에서 수행해 부분 커밋을 막는다 (audit #63).
        rebuild_for_rule(rule_id, session)
    return RedirectResponse("/rules", status_code=303)


@router.delete("/{rule_id}")
def delete_rule(rule_id: int) -> Response:
    with get_session() as session:
        rule = session.get(ReminderRule, rule_id)
        if rule:
            session.delete(rule)
    return Response(status_code=200)
