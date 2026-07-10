from datetime import UTC, date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from shared.config import settings
from shared.generators._time import now_utc, scheduled_at_local, today_local
from shared.generators.base import get_target_telegram_ids, upsert_notification
from shared.lunar import lunar_to_solar
from shared.models import ReminderRule


def _resolve_event_date(use_lunar: bool, year: int, month: int, day: int) -> date | None:
    """음력이면 해당 연도 양력으로 변환, 양력이면 그대로 사용. 잘못된 날짜는 None."""
    if use_lunar:
        result = lunar_to_solar(year, month, day)
        if not result:
            return None
        y, m, d = result
        return date(y, m, d)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def generate(rule: ReminderRule, session: Session, horizon_days: int = 60) -> None:
    config = rule.config
    repeat = config.get("repeat")
    msg = config.get("message") or rule.title
    hour = int(config.get("hour", 9))

    target_ids = get_target_telegram_ids(session, rule)

    if repeat == "yearly":
        _generate_yearly(rule, session, horizon_days, config, hour, msg, target_ids)
    else:
        _generate_once(rule, session, config, msg, target_ids)


def _generate_once(
    rule: ReminderRule,
    session: Session,
    config: dict[str, object],
    msg: str,
    target_ids: list[int],
) -> None:
    run_at_str = str(config.get("run_at") or "")
    if not run_at_str:
        return

    run_at = datetime.fromisoformat(run_at_str)
    # datetime-local 폼은 타임존 없는 로컬(KST) 벽시계 문자열을 보내므로,
    # naive면 settings.tz로 해석한 뒤 UTC로 변환한다 (audit #6). 그렇지 않으면
    # UTC로 오해석해 KST 기준 9시간 늦게 발송된다.
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=ZoneInfo(settings.tz)).astimezone(UTC)

    if run_at <= datetime.now(UTC):
        return

    # 메시지는 자유 입력이므로 escape (yearly 경로는 _format_yearly_message에서 이미 escape)
    escaped_msg = escape(msg)
    for tid in target_ids:
        upsert_notification(session, rule, run_at, tid, escaped_msg)


def _generate_yearly(
    rule: ReminderRule,
    session: Session,
    horizon_days: int,
    config: dict[str, object],
    hour: int,
    msg: str,
    target_ids: list[int],
) -> None:
    month = int(str(config.get("month") or 1))
    day = int(str(config.get("day") or 1))
    use_lunar = bool(config.get("use_lunar", False))
    today = today_local()
    horizon = today + timedelta(days=horizon_days)
    now = now_utc()

    # 음력 11~12월 기일은 이듬해 양력 1~2월에 떨어지므로 today.year-1도 시도한다.
    for year in (today.year - 1, today.year, today.year + 1):
        event_date = _resolve_event_date(use_lunar, year, month, day)
        if event_date is None:
            continue

        if event_date < today or event_date > horizon:
            continue

        for lead in rule.lead_times_days:
            notify_date = event_date - timedelta(days=lead)
            if notify_date < today:
                continue
            scheduled_at = scheduled_at_local(notify_date, hour)
            # 오늘이지만 이미 지난 시각의 slot은 재생성하지 않는다 (audit #1).
            if notify_date == today and scheduled_at < now:
                continue
            message = _format_yearly_message(msg, lead, event_date)
            for tid in target_ids:
                upsert_notification(session, rule, scheduled_at, tid, message)


def _format_yearly_message(msg: str, lead: int, event_date: date) -> str:
    escaped = escape(msg)
    if lead == 0:
        return f"오늘은 <b>{escaped}</b>입니다."
    return f"<b>{escaped}</b> <b>{lead}일 전</b>입니다. ({event_date.strftime('%m/%d')})"
