from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.generators.base import upsert_notification
from shared.models import FamilyMember, ReminderRule, WeightLog


def _build_report(member: FamilyMember, session: Session, cadence: str) -> str | None:
    """주간(weekly) 또는 월간(monthly) 몸무게 리포트 메시지 생성."""
    now = datetime.now(UTC)
    since = now - timedelta(days=30) if cadence == "monthly" else now - timedelta(days=7)

    logs = session.scalars(
        select(WeightLog)
        .where(WeightLog.member_id == member.id, WeightLog.recorded_at >= since)
        .order_by(WeightLog.recorded_at)
    ).all()

    if not logs:
        return None

    latest = float(logs[-1].weight_kg)
    oldest = float(logs[0].weight_kg)
    diff = latest - oldest
    sign = "+" if diff >= 0 else ""
    period_label = "이번 달" if cadence == "monthly" else "이번 주"

    return (
        f"📊 <b>{member.name}</b>님 {period_label} 몸무게 리포트\n"
        f"현재: <b>{latest}kg</b> ({sign}{diff:.1f}kg)\n"
        f"기록 횟수: {len(logs)}회"
    )


def generate(rule: ReminderRule, session: Session, horizon_days: int = 60) -> None:  # noqa: ARG001
    cadence = rule.config.get("cadence", "weekly")
    weekday = int(rule.config.get("weekday", 0))  # 0=월, 6=일
    hour = int(rule.config.get("hour", 9))

    now = datetime.now(UTC)
    today = now.date()

    # 이번 주(또는 월) 기준 다음 발송일 계산
    if cadence == "monthly":
        next_day = today.replace(day=1)
        if next_day <= today:
            m = today.month + 1 if today.month < 12 else 1
            y = today.year if today.month < 12 else today.year + 1
            next_day = today.replace(year=y, month=m, day=1)
    else:
        days_ahead = (weekday - today.weekday()) % 7 or 7
        next_day = today + timedelta(days=days_ahead)

    scheduled_at = datetime(next_day.year, next_day.month, next_day.day, hour, 0, tzinfo=UTC)

    # 대상 구성원별 리포트 생성
    query = select(FamilyMember).where(
        FamilyMember.active.is_(True),
        FamilyMember.telegram_user_id.isnot(None),
    )
    if rule.target_member_ids:
        query = query.where(FamilyMember.id.in_(rule.target_member_ids))

    members = session.scalars(query).all()
    for member in members:
        msg = _build_report(member, session, cadence)
        if msg and member.telegram_user_id:
            upsert_notification(session, rule, scheduled_at, member.telegram_user_id, msg)
