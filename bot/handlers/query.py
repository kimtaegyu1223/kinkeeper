import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from shared.db import get_session
from shared.enums import NotificationStatus
from shared.models import ScheduledNotification


def _get_upcoming(days: int = 7) -> list[ScheduledNotification]:
    now = datetime.now(UTC)
    until = now + timedelta(days=days)
    with get_session() as session:
        rows = session.scalars(
            select(ScheduledNotification)
            .where(
                ScheduledNotification.scheduled_at >= now,
                ScheduledNotification.scheduled_at <= until,
                ScheduledNotification.status == NotificationStatus.pending,
            )
            .order_by(ScheduledNotification.scheduled_at)
            .limit(10)
        ).all()
        # 세션 닫히기 전에 데이터 확보
        return list(rows)


async def upcoming_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    rows = await asyncio.to_thread(_get_upcoming)

    if not rows:
        await update.message.reply_text("앞으로 7일 이내 예정된 알림이 없습니다.")
        return

    lines = ["<b>📅 앞으로 7일 예정 알림</b>\n"]
    for row in rows:
        kst = row.scheduled_at.astimezone(UTC)
        lines.append(f"• {kst.strftime('%m/%d %H:%M')} — {row.message[:40]}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
