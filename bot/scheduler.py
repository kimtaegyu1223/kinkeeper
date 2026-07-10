import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.orm import Session

from shared.config import settings
from shared.db import get_session
from shared.enums import NotificationStatus
from shared.models import ScheduledNotification
from shared.notifier import send_message

log = structlog.get_logger()

# 이보다 오래 지난 pending 알림은 발송하지 않고 취소한다 (다운타임 후 묵은 알림 폭주 방지, #56).
_STALE_AFTER = timedelta(hours=24)
# 이 기간을 지난 종료 상태(sent/failed/cancelled) 행은 03시 rebuild 때 정리한다 (audit #30).
_RETENTION = timedelta(days=90)


def _fetch_pending() -> list[tuple[int, int, str, str | None, datetime]]:
    """발송 대기 중인 알림 조회.

    (id, target_telegram_id, message, source_key, scheduled_at) 튜플 목록 반환.
    """
    now = datetime.now(UTC)
    with get_session() as session:
        rows = session.scalars(
            select(ScheduledNotification)
            .where(
                ScheduledNotification.scheduled_at <= now,
                ScheduledNotification.status == NotificationStatus.pending,
            )
            .order_by(ScheduledNotification.scheduled_at)
            .limit(50)
        ).all()
        return [(r.id, r.target_telegram_id, r.message, r.source_key, r.scheduled_at) for r in rows]


def _mark_sent(notification_id: int, success: bool, error: str | None = None) -> None:
    """아직 pending인 행만 sent/failed로 갱신한다.

    fetch~발송 사이에 웹 rebuild 등이 행을 취소/삭제했을 수 있으므로 status=pending
    조건부 UPDATE로 덮어쓴다. 이미 pending이 아니면 아무것도 하지 않는다 (audit #27).
    """
    with get_session() as session:
        session.execute(
            update(ScheduledNotification)
            .where(
                ScheduledNotification.id == notification_id,
                ScheduledNotification.status == NotificationStatus.pending,
            )
            .values(
                status=NotificationStatus.sent if success else NotificationStatus.failed,
                sent_at=datetime.now(UTC),
                error=error,
            )
        )


def _mark_cancelled(notification_id: int) -> None:
    with get_session() as session:
        row = session.get(ScheduledNotification, notification_id)
        if row is None:
            return
        row.status = NotificationStatus.cancelled


def _cancel_pending_diet_notifications(session: Session) -> int:
    rows = session.scalars(
        select(ScheduledNotification).where(
            ScheduledNotification.source_key.like("diet:%"),
            ScheduledNotification.status == NotificationStatus.pending,
        )
    ).all()
    for row in rows:
        row.status = NotificationStatus.cancelled
    return len(rows)


def _has_weight_log_this_week(member_id: int) -> bool:
    """이번 주(월~일) 몸무게 기록이 있는지 확인."""
    from shared.models import WeightLog

    today = datetime.now(UTC).date()
    monday = today - __import__("datetime").timedelta(days=today.weekday())
    week_start = datetime(monday.year, monday.month, monday.day, tzinfo=UTC)
    with get_session() as session:
        result = session.scalar(
            select(WeightLog).where(
                WeightLog.member_id == member_id,
                WeightLog.recorded_at >= week_start,
            )
        )
        return result is not None


def _resolve_bmi_message(member_id: int) -> str | None:
    """BMI 리포트 메시지를 실시간으로 생성."""
    from shared.generators.diet_report import build_bmi_report
    from shared.models import FamilyMember

    with get_session() as session:
        member = session.get(FamilyMember, member_id)
        if not member or not member.height_cm:
            return None
        return build_bmi_report(member, session)


def _purge_old_notifications(session: Session) -> int:
    """보존기간을 지난 종료 상태(sent/failed/cancelled) 알림을 물리 삭제한다 (audit #30)."""
    cutoff = datetime.now(UTC) - _RETENTION
    result = session.execute(
        delete(ScheduledNotification).where(
            ScheduledNotification.status.in_(
                [
                    NotificationStatus.sent,
                    NotificationStatus.failed,
                    NotificationStatus.cancelled,
                ]
            ),
            ScheduledNotification.scheduled_at < cutoff,
        )
    )
    return cast("CursorResult[Any]", result).rowcount or 0


def _do_rebuild() -> None:
    from shared.generators import rebuild_upcoming
    from shared.generators.diet_report import rebuild_diet_reports
    from shared.generators.health_check import rebuild_health_checks

    with get_session() as session:
        rebuild_upcoming(session, horizon_days=settings.schedule_horizon_days)
        rebuild_health_checks(session, horizon_days=settings.schedule_horizon_days)
        cancelled_diet_count = 0
        if settings.weight_feature_enabled:
            rebuild_diet_reports(session, horizon_days=settings.schedule_horizon_days)
        else:
            cancelled_diet_count = _cancel_pending_diet_notifications(session)
        purged_count = _purge_old_notifications(session)
    log.info(
        "알림 예정 재생성 완료",
        horizon_days=settings.schedule_horizon_days,
        weight_feature_enabled=settings.weight_feature_enabled,
        cancelled_diet_count=cancelled_diet_count,
        purged_count=purged_count,
    )


async def dispatch_pending() -> None:
    """1분마다 실행 — pending 알림을 텔레그램으로 발송."""
    pending = await asyncio.to_thread(_fetch_pending)
    if not pending:
        return

    log.info("발송 대기 알림 처리", count=len(pending))
    now = datetime.now(UTC)
    for notification_id, chat_id, message, source_key, scheduled_at in pending:
        # 한 행의 예외가 배치 전체를 중단시켜 후속 행이 재발송되는 것을 막는다 (audit #28).
        try:
            # 너무 오래 지난(다운타임 등) 알림은 발송하지 않고 취소한다 (audit #56).
            if now - scheduled_at > _STALE_AFTER:
                await asyncio.to_thread(_mark_cancelled, notification_id)
                continue

            if (
                source_key
                and source_key.startswith("diet:")
                and not settings.weight_feature_enabled
            ):
                await asyncio.to_thread(_mark_cancelled, notification_id)
                continue

            # diet nudge: 이번 주 기록이 있으면 취소
            if source_key and source_key.startswith("diet:nudge:"):
                parts = source_key.split(":")
                # source_key format: diet:nudge:{member_id}:{date}
                try:
                    member_id = int(parts[2])
                except (IndexError, ValueError):
                    member_id = None
                if member_id is not None:
                    has_log = await asyncio.to_thread(_has_weight_log_this_week, member_id)
                    if has_log:
                        await asyncio.to_thread(_mark_cancelled, notification_id)
                        continue

            # BMI 리포트: 발송 직전 실시간 생성. 멤버 삭제/키 미등록 등으로 resolve에
            # 실패하면 placeholder 원문('__bmi_report__:{id}')이 그대로 나가지 않도록
            # 취소하고 스킵한다 (audit #35).
            if message.startswith("__bmi_report__:"):
                try:
                    member_id = int(message.split(":")[1])
                except (IndexError, ValueError):
                    member_id = None
                resolved = (
                    await asyncio.to_thread(_resolve_bmi_message, member_id)
                    if member_id is not None
                    else None
                )
                if not resolved:
                    log.warning(
                        "BMI 리포트 생성 실패 — 발송 취소",
                        notification_id=notification_id,
                        source_key=source_key,
                    )
                    await asyncio.to_thread(_mark_cancelled, notification_id)
                    continue
                message = resolved

            success = await send_message(chat_id, message)
            await asyncio.to_thread(
                _mark_sent,
                notification_id,
                success,
                None if success else "발송 실패",
            )
        except Exception:
            log.exception("알림 발송 처리 실패", notification_id=notification_id)
            continue


async def rebuild_upcoming_async() -> None:
    """매일 새벽 3시 실행 — 활성 규칙에서 예정 알림 재생성."""
    await asyncio.to_thread(_do_rebuild)


def create_scheduler() -> AsyncIOScheduler:
    # 기본 misfire_grace_time은 1초라, 잡 실행이 1초만 늦어도 그 회차가 스킵된다.
    # 하루 1회뿐인 03시 rebuild가 이렇게 스킵되면 그날 재생성이 통째로 누락되므로
    # grace time을 넉넉히 준다 (audit #34).
    scheduler = AsyncIOScheduler(
        timezone="Asia/Seoul",
        job_defaults={"misfire_grace_time": 3600, "coalesce": True},
    )
    # dispatch는 1분 주기라 다음 tick에 회복되므로 grace를 주기 이내로 둔다.
    scheduler.add_job(
        dispatch_pending, "interval", minutes=1, id="dispatch_pending", misfire_grace_time=59
    )
    scheduler.add_job(rebuild_upcoming_async, "cron", hour=3, minute=0, id="rebuild_upcoming")
    return scheduler
