from datetime import date, timedelta
from html import escape
from typing import cast

from sqlalchemy.orm import Session

from shared.config import settings
from shared.config_schemas import BirthdayConfig
from shared.dates import replace_year
from shared.generators._time import now_utc, scheduled_at_local, today_local
from shared.generators.base import upsert_notification
from shared.lunar import lunar_to_solar
from shared.models import FamilyMember, ReminderRule


def _resolve_birthday_solar(member: FamilyMember, use_lunar: bool, year: int) -> date | None:
    """음력/양력 설정에 따라 해당 연도의 양력 생일 반환."""
    if use_lunar and member.birthday_lunar:
        result = lunar_to_solar(year, member.birthday_lunar.month, member.birthday_lunar.day)
        if result:
            y, m, d = result
            return date(y, m, d)
        return None
    return member.birthday_solar


def generate(rule: ReminderRule, session: Session, horizon_days: int = 60) -> None:
    config = cast(BirthdayConfig, rule.config)
    member_id = config.get("member_id")
    if not member_id:
        return

    member = session.get(FamilyMember, member_id)
    # 비활성 구성원은 생일 알림 대상에서 제외한다. 다른 소비처(health_check/diet_report/
    # 조회 핸들러)가 모두 active 구성원만 대상으로 하는 것과 일관되게, 규칙이 어떤
    # 경로로 활성 상태가 되든 여기서 최종 방어한다 (audit #57).
    if not member or not member.active:
        return

    use_lunar = bool(config.get("use_lunar", False))
    hour = int(config.get("hour", 9))

    today = today_local()
    horizon = today + timedelta(days=horizon_days)
    now = now_utc()

    # 작년/올해/내년 세 해 모두 시도.
    # (음력은 매년 양력 날짜가 달라지고, 음력 11~12월 생일은 이듬해 양력 1~2월에
    #  떨어지므로 today가 연초일 때 today.year-1 음력 연도가 필요하다.)
    for year in (today.year - 1, today.year, today.year + 1):
        bday_solar = _resolve_birthday_solar(member, use_lunar, year)
        if not bday_solar:
            continue
        # 음력이면 이미 해당 연도 날짜, 양력이면 연도 교체 (2/29는 평년에 2/28로 폴백)
        if not use_lunar:
            bday_solar = replace_year(bday_solar, year)

        if bday_solar < today or bday_solar > horizon:
            continue

        # 이름은 임의 입력이므로 HTML 특수문자를 escape (parse_mode=HTML 발송)
        name = escape(member.name)

        for lead in rule.lead_times_days:
            notify_date = bday_solar - timedelta(days=lead)
            if notify_date < today:
                continue
            scheduled_at = scheduled_at_local(notify_date, hour)
            # 오늘이지만 이미 지난 시각의 slot은 재생성하지 않는다 (audit #1).
            if notify_date == today and scheduled_at < now:
                continue
            bday_label = "음력" if use_lunar else "양력"
            if lead == 0:
                msg = f"🎂 오늘은 <b>{name}</b>님의 생일({bday_label})입니다! 축하해주세요 🎉"
            else:
                msg = (
                    f"🎂 <b>{name}</b>님의 생일({bday_label})이 <b>{lead}일 후</b>입니다!"
                    f" ({bday_solar.strftime('%m/%d')})"
                )
            upsert_notification(session, rule, scheduled_at, settings.group_chat_id, msg)
