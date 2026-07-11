from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from shared.db import get_session
from shared.enums import ReminderType
from shared.generators import rebuild_for_rule
from shared.models import FamilyMember, ReminderRule
from web.auth import verify_admin
from web.form_utils import parse_int_default, parse_optional_date, parse_optional_int
from web.templating import templates

router = APIRouter(prefix="/members", dependencies=[Depends(verify_admin)])

_DEFAULT_BIRTHDAY_LEADS = [7, 3, 0]
_DEFAULT_BIRTHDAY_HOUR = 9


def _parse_lunar(month: str, day: str) -> date | None:
    # 음력 월/일은 폼 select 값이지만 curl 등으로 비정상 값이 올 수 있어 안전 파싱한다.
    m = parse_int_default(month, "음력 월", 0)
    d = parse_int_default(day, "음력 일", 0)
    if not (m and d):
        return None
    # 음력은 월 1~12, 일 1~30이 유효하다.
    if not (1 <= m <= 12 and 1 <= d <= 30):
        raise HTTPException(status_code=400, detail="음력 생일의 월/일이 올바르지 않습니다.")
    try:
        # 연도(2000)는 자리표시자로만 쓰이고 생일 알림은 월/일만 참조한다.
        return date(2000, m, d)
    except ValueError:
        # 음력 2월 30일은 실재하나 date(2000,2,30)로 표현할 수 없다. 500 대신 안내한다.
        # (윤달·2/30 무손실 저장은 컬럼 구조 변경이 필요해 별도 단계로 미룬다 — audit #45)
        raise HTTPException(
            status_code=400,
            detail="음력 2월 30일 생일은 현재 저장 방식으로는 등록할 수 없습니다.",
        ) from None


def _parse_gender(gender: str) -> str | None:
    return gender if gender in ("M", "F") else None


def _ensure_birthday_rule(session: object, member: FamilyMember) -> ReminderRule | None:
    from sqlalchemy.orm import Session as SASession

    if not isinstance(session, SASession):
        return None

    existing = session.scalar(
        select(ReminderRule).where(
            ReminderRule.type == ReminderType.birthday,
            ReminderRule.config["member_id"].as_integer() == member.id,
        )
    )

    # 비활성 구성원 또는 생일 정보가 모두 빈 경우 기존 생일 규칙을 비활성화한다. 반환된
    # 규칙을 caller가 rebuild_for_rule에 넘기면 내부에서 pending 알림까지 물리 삭제된다
    # (audit #43, #57).
    if not member.active or (not member.birthday_solar and not member.birthday_lunar):
        if existing and existing.active:
            existing.active = False
        return existing

    use_lunar = bool(member.birthday_lunar and not member.birthday_solar)

    if existing:
        # 관리자가 /rules에서 커스텀한 hour/lead_times_days는 보존하고, 구성원 필드에서
        # 파생되는 member_id/use_lunar만 갱신한다 (audit #44).
        # (JSONB 변경 감지를 위해 새 dict로 재할당)
        config = dict(existing.config or {})
        config["member_id"] = member.id
        config["use_lunar"] = use_lunar
        existing.config = config
        existing.active = True
        return existing
    else:
        config = {
            "member_id": member.id,
            "use_lunar": use_lunar,
            "hour": _DEFAULT_BIRTHDAY_HOUR,
        }
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
) -> RedirectResponse:
    with get_session() as session:
        member = FamilyMember(
            name=name.strip(),
            telegram_user_id=parse_optional_int(telegram_user_id, "텔레그램 사용자 ID"),
            birthday_solar=parse_optional_date(birthday_solar, "양력 생일"),
            birthday_lunar=_parse_lunar(birthday_lunar_month, birthday_lunar_day),
            gender=_parse_gender(gender),
            active=bool(active),
        )
        session.add(member)
        session.flush()
        rule = _ensure_birthday_rule(session, member)
        if rule:
            session.flush()
            # 구성원/규칙 저장과 알림 재빌드를 같은 트랜잭션에서 수행해 부분 커밋을
            # 막는다(rules.py와 동일, audit #63).
            rebuild_for_rule(rule.id, session)

    return RedirectResponse("/members", status_code=303)


@router.get("/{member_id}/edit", response_class=HTMLResponse)
def edit_member_form(member_id: int, request: Request) -> HTMLResponse:
    with get_session() as session:
        member = session.get(FamilyMember, member_id)
        # 없는 id는 '추가' 폼을 렌더하지 않고 404 (audit #61).
        if member is None:
            raise HTTPException(status_code=404, detail="구성원을 찾을 수 없습니다.")
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
) -> RedirectResponse:
    with get_session() as session:
        member = session.get(FamilyMember, member_id)
        # 없는 id에 대한 조용한 no-op 대신 404로 알린다 (audit #61).
        if member is None:
            raise HTTPException(status_code=404, detail="구성원을 찾을 수 없습니다.")
        member.name = name.strip()
        member.telegram_user_id = parse_optional_int(telegram_user_id, "텔레그램 사용자 ID")
        member.birthday_solar = parse_optional_date(birthday_solar, "양력 생일")
        member.birthday_lunar = _parse_lunar(birthday_lunar_month, birthday_lunar_day)
        member.gender = _parse_gender(gender)
        member.active = bool(active)
        rule = _ensure_birthday_rule(session, member)
        if rule:
            session.flush()
            # 구성원/규칙 저장과 알림 재빌드를 같은 트랜잭션에서 수행해 부분 커밋을
            # 막는다(rules.py와 동일, audit #63).
            rebuild_for_rule(rule.id, session)

    return RedirectResponse("/members", status_code=303)


@router.delete("/{member_id}")
def delete_member(member_id: int) -> Response:
    with get_session() as session:
        member = session.get(FamilyMember, member_id)
        if member:
            # 생일 규칙은 config.member_id JSONB로만 참조돼 FK 캐스케이드가 닿지 않는다.
            # 구성원 삭제 시 연결된 생일 규칙을 함께 삭제하면 rule_id FK(ondelete CASCADE)로
            # 그 규칙의 pending 알림까지 정리된다 (audit #16).
            rules = session.scalars(
                select(ReminderRule).where(
                    ReminderRule.type == ReminderType.birthday,
                    ReminderRule.config["member_id"].as_integer() == member_id,
                )
            ).all()
            for rule in rules:
                session.delete(rule)
            session.delete(member)
    return Response(status_code=200)
