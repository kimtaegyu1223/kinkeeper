"""건강검진 알림 generator.

reminder_rules 대신 health_check_types + health_check_records 를 직접 참조.
rebuild_health_checks() 를 scheduler에서 rebuild_upcoming() 과 함께 호출.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config import settings
from shared.enums import NotificationStatus
from shared.generators._time import now_utc, scheduled_at_local, today_local
from shared.generators.base import upsert_notification_by_key
from shared.models import (
    FamilyMember,
    HealthCheckRecord,
    HealthCheckType,
    MemberHealthCheckConfig,
    ScheduledNotification,
)


@dataclass(frozen=True)
class _HealthReportItem:
    member_id: int
    member_name: str
    check_name: str
    due_date: date
    latest_checked: date | None


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


def _cancel_stale_health_notifications(session: Session, desired_source_keys: set[str]) -> None:
    rows = session.scalars(
        select(ScheduledNotification).where(
            ScheduledNotification.source_key.like("hc:%"),
            ScheduledNotification.status == NotificationStatus.pending,
        )
    ).all()
    for row in rows:
        if row.source_key not in desired_source_keys:
            row.status = NotificationStatus.cancelled


def rebuild_health_checks(
    session: Session, horizon_days: int = 60, _today: date | None = None
) -> None:
    """월 1회 가족방 건강검진 리포트를 예약.

    _today: 테스트용 날짜 주입 (None이면 오늘 사용)
    """
    today = _today or today_local()
    horizon = today + timedelta(days=horizon_days)
    now = now_utc()
    group_chat_id = settings.group_chat_id
    report_items = _collect_report_items(session, today)
    desired_source_keys: set[str] = set()

    report_date = _first_report_date(today)
    while report_date <= horizon:
        next_report_date = _first_of_next_month(report_date)
        month_items = [item for item in report_items if item.due_date < next_report_date]
        if month_items:
            scheduled_at = scheduled_at_local(report_date)
            # 오늘이지만 이미 지난 시각의 slot은 재생성하지 않는다. sent 행은 pending 한정
            # 유니크 인덱스에 없어 upsert가 새 pending을 INSERT하므로, 당일 재시작 시
            # 재발송을 막으려면 rule 생성기와 동일한 시각 가드가 필요하다 (audit #1).
            if not (report_date == today and scheduled_at < now):
                source_key = f"hc:monthly:group:{report_date.isoformat()}"
                desired_source_keys.add(source_key)
                upsert_notification_by_key(
                    session,
                    source_key,
                    scheduled_at,
                    group_chat_id,
                    _format_monthly_report(month_items, report_date),
                )
        report_date = next_report_date

    _cancel_stale_health_notifications(session, desired_source_keys)


def _collect_report_items(session: Session, today: date) -> list[_HealthReportItem]:
    items: list[_HealthReportItem] = []

    members = session.scalars(select(FamilyMember).where(FamilyMember.active.is_(True))).all()
    check_types = session.scalars(
        select(HealthCheckType).where(HealthCheckType.active.is_(True))
    ).all()

    for member in members:
        for ct in check_types:
            if ct.gender and member.gender and member.gender != ct.gender:
                continue

            # 나이 제한 체크
            if ct.min_age is not None:
                if not member.birthday_solar and not member.birthday_lunar:
                    continue  # 생일 모르면 스킵
                # 음력 생일은 연도가 센티널(2000)이라 출생연도 의미가 없다. 양력 생일이
                # 있을 때만 나이를 계산해 필터하고, 음력만 있으면 나이 미상으로 보아
                # 보수적으로 포함한다(알림 누락보다 과다가 안전) (audit #17).
                if member.birthday_solar is not None:
                    bday = member.birthday_solar
                    age = (
                        today.year - bday.year - ((today.month, today.day) < (bday.month, bday.day))
                    )
                    if age < ct.min_age:
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

            items.append(
                _HealthReportItem(
                    member_id=member.id,
                    member_name=member.name,
                    check_name=ct.name,
                    due_date=due_date,
                    latest_checked=latest_date,
                )
            )

    return items


def _first_of_next_month(d: date) -> date:
    """다음 달 1일 반환."""
    if d.month == 12:
        return d.replace(year=d.year + 1, month=1, day=1)
    return d.replace(month=d.month + 1, day=1)


def _first_report_date(today: date) -> date:
    if today.day == 1:
        return today
    return _first_of_next_month(today)


def _format_monthly_report(items: Iterable[_HealthReportItem], report_date: date) -> str:
    grouped: dict[tuple[int, str], list[_HealthReportItem]] = defaultdict(list)
    for item in items:
        grouped[(item.member_id, item.member_name)].append(item)

    report_month = f"{report_date.year}년 {report_date.month}월"
    lines = [f"📋 <b>[{report_month} 건강검진]</b>"]
    for (_, member_name), member_items in sorted(grouped.items(), key=lambda row: row[0][1]):
        lines.append("")
        lines.append(f"<b>{escape(member_name)}</b>")
        for item in sorted(member_items, key=lambda row: (row.due_date, row.check_name)):
            lines.append(f"• {escape(item.check_name)}")

    return "\n".join(lines)
