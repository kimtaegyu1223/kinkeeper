from collections.abc import Callable

from sqlalchemy.orm import Session

from shared.enums import ReminderType
from shared.generators import birthday, custom, diet_report, health_check, holiday
from shared.models import ReminderRule

GeneratorFn = Callable[[ReminderRule, Session, int], None]

_REGISTRY: dict[ReminderType, GeneratorFn] = {
    ReminderType.birthday: birthday.generate,
    ReminderType.holiday: holiday.generate,
    ReminderType.health_check: health_check.generate,
    ReminderType.custom: custom.generate,
    ReminderType.diet_report: diet_report.generate,
}


def rebuild_upcoming(session: Session, horizon_days: int = 60) -> None:
    """모든 활성 규칙에 대해 horizon_days 이내 예정 알림을 생성/보충."""
    from sqlalchemy import select

    rules = session.scalars(
        select(ReminderRule).where(ReminderRule.active.is_(True))
    ).all()

    for rule in rules:
        generator = _REGISTRY.get(rule.type)
        if generator:
            generator(rule, session, horizon_days)


def rebuild_for_rule(rule_id: int, session: Session, horizon_days: int = 60) -> None:
    """규칙 수정/추가 시 해당 규칙만 즉시 재생성."""
    rule = session.get(ReminderRule, rule_id)
    if not rule or not rule.active:
        return
    generator = _REGISTRY.get(rule.type)
    if generator:
        generator(rule, session, horizon_days)
