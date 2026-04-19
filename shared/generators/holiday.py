from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from shared.generators.base import get_target_telegram_ids, upsert_notification
from shared.lunar import lunar_to_solar
from shared.models import ReminderRule


def generate(rule: ReminderRule, session: Session, horizon_days: int = 60) -> None:
    lunar_month = rule.config.get("lunar_month")
    lunar_day = rule.config.get("lunar_day")
    holiday_name = rule.config.get("name", "명절")
    hour = int(rule.config.get("hour", 9))

    if not lunar_month or not lunar_day:
        return

    today = datetime.now(UTC).date()
    horizon = today + timedelta(days=horizon_days)

    for year in (today.year, today.year + 1):
        result = lunar_to_solar(year, lunar_month, lunar_day)
        if not result:
            continue
        y, m, d = result
        holiday_date = date(y, m, d)

        if holiday_date > horizon or holiday_date < today:
            continue

        target_ids = get_target_telegram_ids(session, rule)

        for lead in rule.lead_times_days:
            notify_date = holiday_date - timedelta(days=lead)
            if notify_date < today:
                continue
            scheduled_at = datetime(
                notify_date.year, notify_date.month, notify_date.day, hour, 0, tzinfo=UTC
            )
            if lead == 0:
                msg = f"🎊 오늘은 <b>{holiday_name}</b>입니다! 가족과 즐거운 시간 보내세요."
            elif lead <= 3:
                msg = (
                    f"🎊 <b>{holiday_name}</b>까지 <b>{lead}일</b> 남았습니다!"
                    f" ({holiday_date.strftime('%m/%d')})"
                )
            else:
                msg = (
                    f"🚆 <b>{holiday_name}</b>이 {lead}일 후입니다. "
                    f"({holiday_date.strftime('%m/%d')}) 교통편 예매를 서두르세요!"
                )
            for tid in target_ids:
                upsert_notification(session, rule, scheduled_at, tid, msg)
