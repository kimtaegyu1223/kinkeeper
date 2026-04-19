from datetime import UTC, datetime

from sqlalchemy.orm import Session

from shared.models import ReminderRule, ScheduledNotification


def upsert_notification(
    session: Session,
    rule: ReminderRule,
    scheduled_at: datetime,
    target_telegram_id: int,
    message: str,
) -> None:
    """같은 rule_id + scheduled_at + target 조합이 없을 때만 insert."""
    from sqlalchemy import select

    from shared.enums import NotificationStatus

    exists = session.scalar(
        select(ScheduledNotification).where(
            ScheduledNotification.rule_id == rule.id,
            ScheduledNotification.scheduled_at == scheduled_at,
            ScheduledNotification.target_telegram_id == target_telegram_id,
            ScheduledNotification.status == NotificationStatus.pending,
        )
    )
    if exists:
        return

    session.add(
        ScheduledNotification(
            rule_id=rule.id,
            scheduled_at=scheduled_at,
            target_telegram_id=target_telegram_id,
            message=message,
        )
    )


def upsert_notification_by_key(
    session: Session,
    source_key: str,
    scheduled_at: datetime,
    target_telegram_id: int,
    message: str,
) -> None:
    """source_key 기반 중복 없이 insert (건강검진 등 rule 없는 알림용)."""
    from sqlalchemy import select

    from shared.enums import NotificationStatus

    exists = session.scalar(
        select(ScheduledNotification).where(
            ScheduledNotification.source_key == source_key,
            ScheduledNotification.status == NotificationStatus.pending,
        )
    )
    if exists:
        return

    session.add(
        ScheduledNotification(
            rule_id=None,
            source_key=source_key,
            scheduled_at=scheduled_at,
            target_telegram_id=target_telegram_id,
            message=message,
        )
    )


def get_target_telegram_ids(session: Session, rule: ReminderRule) -> list[int]:
    """그룹채널 ID만 반환 (모든 알림은 그룹에 발송)."""
    from shared.config import settings

    return [settings.group_chat_id]


def now_utc() -> datetime:
    return datetime.now(UTC)
