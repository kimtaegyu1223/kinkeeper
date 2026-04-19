from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from shared.generators.base import get_target_telegram_ids, upsert_notification
from shared.lunar import lunar_to_solar
from shared.models import FamilyMember, ReminderRule


def _next_birthday(birthday: date, from_date: date) -> date:
    """from_date 기준으로 다가오는 생일(올해 or 내년) 반환."""
    this_year = birthday.replace(year=from_date.year)
    if this_year >= from_date:
        return this_year
    return birthday.replace(year=from_date.year + 1)


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
    member_id = rule.config.get("member_id")
    if not member_id:
        return

    member = session.get(FamilyMember, member_id)
    if not member:
        return

    use_lunar = bool(rule.config.get("use_lunar", False))
    hour = int(rule.config.get("hour", 9))

    today = datetime.now(UTC).date()
    horizon = today + timedelta(days=horizon_days)

    # 올해/내년 두 해 모두 시도 (음력은 매년 양력 날짜가 달라짐)
    for year in (today.year, today.year + 1):
        bday_solar = _resolve_birthday_solar(member, use_lunar, year)
        if not bday_solar:
            continue
        # 음력이면 이미 해당 연도 날짜, 양력이면 연도 교체
        if not use_lunar:
            bday_solar = bday_solar.replace(year=year)

        if bday_solar < today or bday_solar > horizon:
            continue

        target_ids = get_target_telegram_ids(session, rule)
        name = member.name

        for lead in rule.lead_times_days:
            notify_date = bday_solar - timedelta(days=lead)
            if notify_date < today:
                continue
            scheduled_at = datetime(
                notify_date.year, notify_date.month, notify_date.day, hour, 0, tzinfo=UTC
            )
            bday_label = "음력" if use_lunar else "양력"
            if lead == 0:
                msg = f"🎂 오늘은 <b>{name}</b>님의 생일({bday_label})입니다! 축하해주세요 🎉"
            else:
                msg = (
                    f"🎂 <b>{name}</b>님의 생일({bday_label})이 <b>{lead}일 후</b>입니다!"
                    f" ({bday_solar.strftime('%m/%d')})"
                )
            for tid in target_ids:
                upsert_notification(session, rule, scheduled_at, tid, msg)
