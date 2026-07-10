from datetime import UTC, date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from shared.config import settings
from shared.generators.base import get_target_telegram_ids, upsert_notification
from shared.lunar import lunar_to_solar
from shared.models import ReminderRule


def _today_local() -> date:
    return datetime.now(ZoneInfo(settings.tz)).date()


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _scheduled_at_local(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=ZoneInfo(settings.tz)).astimezone(
        UTC
    )


def generate(rule: ReminderRule, session: Session, horizon_days: int = 60) -> None:
    lunar_month = rule.config.get("lunar_month")
    lunar_day = rule.config.get("lunar_day")
    # 명절 이름은 관리자 자유 입력이므로 HTML 특수문자를 escape (parse_mode=HTML 발송)
    holiday_name = escape(rule.config.get("name", "명절"))
    hour = int(rule.config.get("hour", 9))

    if not lunar_month or not lunar_day:
        return

    today = _today_local()
    horizon = today + timedelta(days=horizon_days)
    now = _now_utc()

    # 음력 11~12월 명절/기일은 이듬해 양력 1~2월에 떨어지므로 today.year-1도 시도한다.
    for year in (today.year - 1, today.year, today.year + 1):
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
            scheduled_at = _scheduled_at_local(notify_date, hour)
            # 오늘이지만 이미 지난 시각의 slot은 재생성하지 않는다 (audit #1).
            if notify_date == today and scheduled_at < now:
                continue
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
