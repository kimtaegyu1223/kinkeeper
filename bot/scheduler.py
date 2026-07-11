"""봇 백그라운드 스케줄러 — 알림 발송·재생성 잡.

APScheduler로 1분마다 dispatch_pending(발송 시각이 된 pending을 텔레그램 발송)을,
매일 03시 rebuild(활성 규칙에서 예정 알림 재생성 + 보존기간 지난 종료 행 정리)를 돌린다.
"""

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


def _fetch_pending() -> list[tuple[int, int, str, datetime]]:
    """발송 대기 중인 알림 조회.

    (id, target_telegram_id, message, scheduled_at) 튜플 목록 반환.
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
        return [(r.id, r.target_telegram_id, r.message, r.scheduled_at) for r in rows]


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
    from shared.generators.health_check import rebuild_health_checks

    with get_session() as session:
        rebuild_upcoming(session, horizon_days=settings.schedule_horizon_days)
        rebuild_health_checks(session, horizon_days=settings.schedule_horizon_days)
        purged_count = _purge_old_notifications(session)
    log.info(
        "알림 예정 재생성 완료",
        horizon_days=settings.schedule_horizon_days,
        purged_count=purged_count,
    )


async def dispatch_pending() -> None:
    """1분마다 실행 — pending 알림을 텔레그램으로 발송."""
    pending = await asyncio.to_thread(_fetch_pending)
    if not pending:
        return

    log.info("발송 대기 알림 처리", count=len(pending))
    now = datetime.now(UTC)
    for notification_id, chat_id, message, scheduled_at in pending:
        # 한 행의 예외가 배치 전체를 중단시켜 후속 행이 재발송되는 것을 막는다 (audit #28).
        try:
            # 너무 오래 지난(다운타임 등) 알림은 발송하지 않고 취소한다 (audit #56).
            if now - scheduled_at > _STALE_AFTER:
                await asyncio.to_thread(_mark_cancelled, notification_id)
                continue

            success, error = await send_message(chat_id, message)
            await asyncio.to_thread(
                _mark_sent,
                notification_id,
                success,
                None if success else (error or "발송 실패"),
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
    # cron 발화 시각(03시)이 generator의 로컬 날짜 계산과 어긋나지 않도록 settings.tz를
    # 그대로 쓴다. 'Asia/Seoul' 하드코딩은 tz를 다른 존으로 바꿀 때 불일치를 낳는다 (audit #75).
    scheduler = AsyncIOScheduler(
        timezone=settings.tz,
        job_defaults={"misfire_grace_time": 3600, "coalesce": True},
    )
    # dispatch는 1분 주기라 다음 tick에 회복되므로 grace를 주기 이내로 둔다.
    scheduler.add_job(
        dispatch_pending, "interval", minutes=1, id="dispatch_pending", misfire_grace_time=59
    )
    scheduler.add_job(rebuild_upcoming_async, "cron", hour=3, minute=0, id="rebuild_upcoming")
    return scheduler
