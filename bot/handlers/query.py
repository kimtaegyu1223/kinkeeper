import asyncio
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from shared.config import settings
from shared.dates import replace_year
from shared.db import get_session
from shared.enums import NotificationStatus
from shared.lunar import lunar_to_solar
from shared.models import FamilyMember, ScheduledNotification


def _get_upcoming(days: int | None = None, limit: int = 100) -> list[ScheduledNotification]:
    days = days or settings.schedule_horizon_days
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
            .limit(limit)
        ).all()
        # 세션 닫히기 전에 데이터 확보
        return list(rows)


def _parse_days_arg(args: Sequence[str] | None) -> int:
    if not args:
        return settings.schedule_horizon_days
    try:
        days = int(args[0])
    except ValueError:
        return settings.schedule_horizon_days
    return min(max(days, 1), 365)


def _preview_message(message: str) -> str:
    text = re.sub(r"<[^>]+>", "", message).replace("\n", " ")
    if len(text) <= 90:
        return text
    return f"{text[:87]}..."


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

    days = _parse_days_arg(context.args)
    rows = await asyncio.to_thread(_get_upcoming, days)

    if not rows:
        await update.message.reply_text(f"앞으로 {days}일 이내 예정된 알림이 없습니다.")
        return

    tz = ZoneInfo(settings.tz)
    lines = [f"<b>📅 앞으로 {days}일 예정 알림</b>\n"]
    for row in rows:
        local_time = row.scheduled_at.astimezone(tz)
        preview = escape(_preview_message(row.message))
        lines.append(f"• {local_time.strftime('%m/%d %H:%M')} - {preview}")

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

    today = datetime.now(UTC).date()
    this_year_bday = _next_birthday_solar(member, today)

    if this_year_bday is None:
        text = (
            f"<b>{member.name}</b>님의 생일이 등록되지 않았습니다.\n"
            "관리자에게 생일 등록을 요청하세요."
        )
        await update.message.reply_text(text, parse_mode="HTML")
        return

    days_until = (this_year_bday - today).days

    if days_until == 0:
        msg = f"🎂 오늘이 <b>{member.name}</b>님의 생일입니다! 🎉"
    elif days_until == 1:
        msg = f"🎂 내일이 <b>{member.name}</b>님의 생일입니다!"
    else:
        bday_str = this_year_bday.strftime("%m/%d")
        msg = f"🎂 <b>{member.name}</b>님의 생일은 <b>{days_until}일 후</b>입니다.\n({bday_str})"

    await update.message.reply_text(msg, parse_mode="HTML")


def _next_birthday_solar(member: FamilyMember, today: date) -> date | None:
    """구성원의 다가오는 양력 생일. 음력 전용 구성원은 음력→양력 변환.

    등록된 생일이 없으면 None.
    """
    if member.birthday_solar:
        # 올해 생일 계산 (2/29는 평년에 2/28로 폴백)
        this_year = replace_year(member.birthday_solar, today.year)
        if this_year < today:
            this_year = replace_year(member.birthday_solar, today.year + 1)
        return this_year

    if member.birthday_lunar:
        # 음력 11~12월 생일은 이듬해 양력 1~2월이므로 today.year-1도 후보에 포함
        candidates = []
        for year in (today.year - 1, today.year, today.year + 1):
            result = lunar_to_solar(year, member.birthday_lunar.month, member.birthday_lunar.day)
            if result is None:
                continue
            y, m, d = result
            cand = date(y, m, d)
            if cand >= today:
                candidates.append(cand)
        return min(candidates) if candidates else None

    return None
