from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from shared.generators.base import get_target_telegram_ids, upsert_notification
from shared.models import FamilyMember, ReminderRule


def generate(rule: ReminderRule, session: Session, horizon_days: int = 60) -> None:
    member_id = rule.config.get("member_id")
    period = rule.config.get("period", "yearly")  # yearly | biannual
    anchor_date_str = rule.config.get("anchor_date")

    if not member_id or not anchor_date_str:
        return

    member = session.get(FamilyMember, member_id)
    if not member:
        return

    anchor = date.fromisoformat(anchor_date_str)
    today = datetime.now(UTC).date()
    horizon = today + timedelta(days=horizon_days)

    interval_months = 6 if period == "biannual" else 12

    # anchor 기준으로 다음 검진일 계산
    check_date = anchor
    while check_date < today:
        m = check_date.month + interval_months
        y = check_date.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        check_date = check_date.replace(year=y, month=m)

    if check_date > horizon:
        return

    target_ids = get_target_telegram_ids(session, rule)
    name = member.name

    for lead in rule.lead_times_days:
        notify_date = check_date - timedelta(days=lead)
        if notify_date < today:
            continue
        scheduled_at = datetime(
            notify_date.year, notify_date.month, notify_date.day, 9, 0, tzinfo=UTC
        )
        if lead == 0:
            msg = f"🏥 오늘은 <b>{name}</b>님의 건강검진일입니다!"
        else:
            msg = (
                f"🏥 <b>{name}</b>님의 건강검진이 <b>{lead}일 후</b>입니다. "
                f"({check_date.strftime('%m/%d')}) 예약을 확인하세요."
            )
        for tid in target_ids:
            upsert_notification(session, rule, scheduled_at, tid, msg)
