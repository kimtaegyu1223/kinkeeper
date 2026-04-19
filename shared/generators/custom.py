from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from shared.generators.base import get_target_telegram_ids, upsert_notification
from shared.models import ReminderRule


def generate(rule: ReminderRule, session: Session, horizon_days: int = 60) -> None:
    config = rule.config
    repeat = config.get("repeat")
    msg = config.get("message") or rule.title
    hour = int(config.get("hour", 9))

    target_ids = get_target_telegram_ids(session, rule)

    if repeat == "yearly":
        _generate_yearly(rule, session, horizon_days, config, hour, msg, target_ids)
    else:
        _generate_once(rule, session, config, hour, msg, target_ids)


def _generate_once(
    rule: ReminderRule,
    session: Session,
    config: dict[str, object],
    hour: int,
    msg: str,
    target_ids: list[int],
) -> None:
    run_at_str = str(config.get("run_at") or "")
    if not run_at_str:
        return

    run_at = datetime.fromisoformat(run_at_str)
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=UTC)

    # run_at에 이미 시각이 있으면 그대로, 없으면 hour 사용
    if run_at <= datetime.now(UTC):
        return

    for tid in target_ids:
        upsert_notification(session, rule, run_at, tid, msg)


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
    today = datetime.now(UTC).date()
    horizon = today + timedelta(days=horizon_days)

    for year in (today.year, today.year + 1):
        try:
            event_date = date(year, month, day)
        except ValueError:
            continue

        if event_date < today or event_date > horizon:
            continue

        for lead in rule.lead_times_days:
            notify_date = event_date - timedelta(days=lead)
            if notify_date < today:
                continue
            scheduled_at = datetime(
                notify_date.year, notify_date.month, notify_date.day, hour, 0, tzinfo=UTC
            )
            for tid in target_ids:
                upsert_notification(session, rule, scheduled_at, tid, msg)
