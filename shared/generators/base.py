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


def get_target_telegram_ids(session: Session, rule: ReminderRule) -> list[int]:
    """rule.target_member_ids 기반으로 실제 telegram_user_id 목록 반환."""
    from sqlalchemy import select

    from shared.models import FamilyMember

    query = select(FamilyMember).where(
        FamilyMember.active.is_(True),
        FamilyMember.telegram_user_id.isnot(None),
    )
    if rule.target_member_ids:
        query = query.where(FamilyMember.id.in_(rule.target_member_ids))

    members = session.scalars(query).all()
    return [m.telegram_user_id for m in members if m.telegram_user_id]


def now_utc() -> datetime:
    return datetime.now(UTC)
