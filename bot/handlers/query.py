import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from shared.db import get_session
from shared.enums import NotificationStatus
from shared.models import FamilyMember, ScheduledNotification


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


def _get_member_by_telegram_id(telegram_user_id: int) -> FamilyMember | None:
    """텔레그램 ID로 가족 구성원 조회."""
    with get_session() as session:
        return session.scalar(
            select(FamilyMember).where(
                FamilyMember.telegram_user_id == telegram_user_id,
                FamilyMember.active.is_(True),
            )
        )


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


async def birthday_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """내 생일 확인 명령어."""
    if update.message is None or update.effective_user is None:
        return

    telegram_id = update.effective_user.id
    member = await asyncio.to_thread(_get_member_by_telegram_id, telegram_id)

    if member is None:
        await update.message.reply_text(
            "등록된 가족 구성원을 찾을 수 없습니다.\n관리자에게 텔레그램 ID 등록을 요청하세요."
        )
        return

    if not member.birthday_solar:
        text = (
            f"<b>{member.name}</b>님의 생일이 등록되지 않았습니다.\n"
            "관리자에게 생일 등록을 요청하세요."
        )
        await update.message.reply_text(text, parse_mode="HTML")
        return

    bday = member.birthday_solar
    today = datetime.now(UTC).date()

    # 올해 생일 계산
    this_year_bday = bday.replace(year=today.year)
    if this_year_bday < today:
        this_year_bday = bday.replace(year=today.year + 1)

    days_until = (this_year_bday - today).days

    if days_until == 0:
        msg = f"🎂 오늘이 <b>{member.name}</b>님의 생일입니다! 🎉"
    elif days_until == 1:
        msg = f"🎂 내일이 <b>{member.name}</b>님의 생일입니다!"
    else:
        bday_str = this_year_bday.strftime("%m/%d")
        msg = f"🎂 <b>{member.name}</b>님의 생일은 <b>{days_until}일 후</b>입니다.\n({bday_str})"

    await update.message.reply_text(msg, parse_mode="HTML")
