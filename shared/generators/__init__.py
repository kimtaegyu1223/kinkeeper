from collections.abc import Callable

import structlog
from sqlalchemy.orm import Session

from shared.enums import NotificationStatus, ReminderType
from shared.generators import birthday, custom, holiday
from shared.models import ReminderRule, ScheduledNotification

log = structlog.get_logger()

GeneratorFn = Callable[[ReminderRule, Session, int], None]

_REGISTRY: dict[ReminderType, GeneratorFn] = {
    ReminderType.birthday: birthday.generate,
    ReminderType.holiday: holiday.generate,
    ReminderType.custom: custom.generate,
}


def rebuild_upcoming(session: Session, horizon_days: int = 60) -> None:
    """모든 활성 규칙에 대해 horizon_days 이내 예정 알림을 생성/보충."""
    from sqlalchemy import select

    _delete_pending_rule_notifications(session)
    rules = session.scalars(select(ReminderRule).where(ReminderRule.active.is_(True))).all()

    for rule in rules:
        generator = _REGISTRY.get(rule.type)
        if not generator:
            continue
        # 규칙 하나의 예외가 전체 재생성을 중단시키지 않도록 규칙별로 격리한다.
        try:
            generator(rule, session, horizon_days)
        except Exception:
            log.exception("규칙 알림 생성 실패", rule_id=rule.id, rule_type=rule.type)


def rebuild_for_rule(rule_id: int, session: Session, horizon_days: int = 60) -> None:
    """규칙 수정/추가 시 해당 규칙만 즉시 재생성."""
    _delete_pending_for_rule(session, rule_id)
    rule = session.get(ReminderRule, rule_id)
    if not rule or not rule.active:
        return
    generator = _REGISTRY.get(rule.type)
    if generator:
        generator(rule, session, horizon_days)


def _delete_pending_rule_notifications(session: Session) -> None:
    """rule 기반 pending 행을 물리 삭제한다.

    이전에는 cancelled로 소프트 변경했으나, dedup이 pending/sent만 보므로 매 rebuild마다
    동일 slot이 새로 insert되어 cancelled 행이 무한 누적됐다 (audit #30). 재생성 직전
    물리 삭제하면 활성 pending만 남는다. bulk delete는 즉시 실행되므로 이어지는 신규
    insert와 partial unique index가 충돌하지 않는다.
    """
    from sqlalchemy import delete

    session.execute(
        delete(ScheduledNotification).where(
            ScheduledNotification.rule_id.isnot(None),
            ScheduledNotification.status == NotificationStatus.pending,
        )
    )


def _delete_pending_for_rule(session: Session, rule_id: int) -> None:
    """해당 규칙의 pending 행을 물리 삭제한다 (audit #30)."""
    from sqlalchemy import delete

    session.execute(
        delete(ScheduledNotification).where(
            ScheduledNotification.rule_id == rule_id,
            ScheduledNotification.status == NotificationStatus.pending,
        )
    )
