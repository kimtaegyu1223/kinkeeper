"""건강검진 알림 generator.

reminder_rules 대신 health_check_types + health_check_records 를 직접 참조.
rebuild_health_checks() 를 scheduler에서 rebuild_upcoming() 과 함께 호출.
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config import settings
from shared.generators.base import upsert_notification_by_key
from shared.models import FamilyMember, HealthCheckRecord, HealthCheckType, MemberHealthCheckConfig


def _add_years(d: date, years: int) -> date:
    """날짜에 n년 더하기 (2월 29일 처리 포함)."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def _next_due(latest_checked: date | None, period_years: int, today: date) -> date:
    """다음 검진 예정일 계산."""
    if latest_checked is None:
        return today  # 기록 없음 → 지금 당장 검진 필요
    return _add_years(latest_checked, period_years)


def rebuild_health_checks(session: Session, horizon_days: int = 60) -> None:
    """모든 활성 구성원 × 활성 검진 항목 조합으로 알림 예약."""
    today = datetime.now(UTC).date()
    horizon = today + timedelta(days=horizon_days)
    group_chat_id = settings.group_chat_id

    members = session.scalars(select(FamilyMember).where(FamilyMember.active.is_(True))).all()
    check_types = session.scalars(
        select(HealthCheckType).where(HealthCheckType.active.is_(True))
    ).all()

    for member in members:
        for ct in check_types:
            if ct.gender and member.gender != ct.gender:
                continue

            config = session.scalar(
                select(MemberHealthCheckConfig).where(
                    MemberHealthCheckConfig.member_id == member.id,
                    MemberHealthCheckConfig.check_type_id == ct.id,
                )
            )
            if config is not None and not config.active:
                continue
            period = (
                config.period_years
                if (config and config.period_years is not None)
                else ct.period_years
            )

            latest_record = session.scalar(
                select(HealthCheckRecord)
                .where(
                    HealthCheckRecord.member_id == member.id,
                    HealthCheckRecord.check_type_id == ct.id,
                )
                .order_by(HealthCheckRecord.checked_at.desc())
                .limit(1)
            )
            latest_date = latest_record.checked_at if latest_record else None
            due_date = _next_due(latest_date, period, today)

            if due_date <= today:
                _schedule_overdue_nudge(session, member, ct, today, horizon, group_chat_id)
            elif due_date <= horizon:
                _schedule_upcoming(session, member, ct, due_date, today, group_chat_id)


def _schedule_upcoming(
    session: Session,
    member: FamilyMember,
    ct: HealthCheckType,
    due_date: date,
    today: date,
    chat_id: int,
) -> None:
    for lead in [30, 14, 7, 0]:
        notify_date = due_date - timedelta(days=lead)
        if notify_date < today:
            continue
        scheduled_at = datetime(
            notify_date.year, notify_date.month, notify_date.day, 9, 0, tzinfo=UTC
        )
        source_key = f"hc:upcoming:{member.id}:{ct.id}:{notify_date.isoformat()}"
        if lead == 0:
            msg = (
                f"🏥 <b>{member.name}</b>님, 오늘은 <b>{ct.name}</b> 검진일입니다!\n"
                f"검진 후 봇에게 알려주세요 → <code>/검진완료 {ct.name}</code>"
            )
        else:
            msg = (
                f"🏥 <b>{member.name}</b>님의 <b>{ct.name}</b> 검진이 "
                f"<b>{lead}일 후</b>입니다. ({due_date.strftime('%m/%d')})\n"
                f"예약을 미리 잡아두세요!"
            )
        upsert_notification_by_key(session, source_key, scheduled_at, chat_id, msg)


def _schedule_overdue_nudge(
    session: Session,
    member: FamilyMember,
    ct: HealthCheckType,
    today: date,
    horizon: date,
    chat_id: int,
) -> None:
    nudge_date = today + timedelta(days=7)
    if nudge_date > horizon:
        return
    scheduled_at = datetime(nudge_date.year, nudge_date.month, nudge_date.day, 9, 0, tzinfo=UTC)
    source_key = f"hc:overdue:{member.id}:{ct.id}:{nudge_date.isoformat()}"
    msg = (
        f"⚠️ <b>{member.name}</b>님, <b>{ct.name}</b> 검진 시기가 지났습니다!\n"
        f"검진 후 봇에게 알려주세요 → <code>/검진완료 {ct.name}</code>"
    )
    upsert_notification_by_key(session, source_key, scheduled_at, chat_id, msg)
