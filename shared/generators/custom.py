from datetime import UTC, datetime

from sqlalchemy.orm import Session

from shared.generators.base import get_target_telegram_ids, upsert_notification
from shared.models import ReminderRule


def generate(rule: ReminderRule, session: Session, horizon_days: int = 60) -> None:  # noqa: ARG001
    run_at_str = rule.config.get("run_at")
    if not run_at_str:
        return

    run_at = datetime.fromisoformat(run_at_str)
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=UTC)

    now = datetime.now(UTC)
    if run_at <= now:
        return

    target_ids = get_target_telegram_ids(session, rule)
    msg = rule.config.get("message", rule.title)

    for tid in target_ids:
        upsert_notification(session, rule, run_at, tid, msg)
