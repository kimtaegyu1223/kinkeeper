import asyncio
from datetime import UTC, datetime

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from shared.db import get_session
from shared.enums import NotificationStatus
from shared.models import ScheduledNotification
from shared.notifier import send_message

log = structlog.get_logger()


def _fetch_pending() -> list[tuple[int, int, str]]:
    """발송 대기 중인 알림 조회. (id, target_telegram_id, message) 튜플 목록 반환."""
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
        return [(r.id, r.target_telegram_id, r.message) for r in rows]


def _mark_sent(notification_id: int, success: bool, error: str | None = None) -> None:
    with get_session() as session:
        row = session.get(ScheduledNotification, notification_id)
        if row is None:
            return
        row.status = NotificationStatus.sent if success else NotificationStatus.failed
        row.sent_at = datetime.now(UTC)
        row.error = error


async def dispatch_pending() -> None:
    """1분마다 실행 — pending 알림을 텔레그램으로 발송."""
    pending = await asyncio.to_thread(_fetch_pending)
    if not pending:
        return

    log.info("발송 대기 알림 처리", count=len(pending))
    for notification_id, chat_id, message in pending:
        success = await send_message(chat_id, message)
        await asyncio.to_thread(
            _mark_sent,
            notification_id,
            success,
            None if success else "발송 실패",
        )


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    # 1분마다 pending 알림 발송
    scheduler.add_job(dispatch_pending, "interval", minutes=1, id="dispatch_pending")
    return scheduler
