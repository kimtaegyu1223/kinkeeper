from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from shared.generators.base import get_target_telegram_ids, upsert_notification
from shared.models import FamilyMember, ReminderRule


def _next_birthday(birthday: date, from_date: date) -> date:
    """from_date 기준으로 다가오는 생일(올해 or 내년) 반환."""
    this_year = birthday.replace(year=from_date.year)
    if this_year >= from_date:
        return this_year
    return birthday.replace(year=from_date.year + 1)


def generate(rule: ReminderRule, session: Session, horizon_days: int = 60) -> None:
    member_id = rule.config.get("member_id")
    if not member_id:
        return

    member = session.get(FamilyMember, member_id)
    if not member or not member.birthday_solar:
        return

    today = datetime.now(UTC).date()
    horizon = today + timedelta(days=horizon_days)
    next_bday = _next_birthday(member.birthday_solar, today)

    if next_bday > horizon:
        return

    target_ids = get_target_telegram_ids(session, rule)
    name = member.name

    for lead in rule.lead_times_days:
        notify_date = next_bday - timedelta(days=lead)
        if notify_date < today:
            continue
        scheduled_at = datetime(
            notify_date.year, notify_date.month, notify_date.day, 9, 0, tzinfo=UTC
        )
        if lead == 0:
            msg = f"🎂 오늘은 <b>{name}</b>님의 생일입니다! 축하해주세요 🎉"
        else:
            msg = (
                f"🎂 <b>{name}</b>님의 생일이 <b>{lead}일 후</b>입니다!"
                f" ({next_bday.strftime('%m/%d')})"
            )

        for tid in target_ids:
            upsert_notification(session, rule, scheduled_at, tid, msg)
