import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from shared.db import get_session
from shared.models import FamilyMember, WeightLog

log = structlog.get_logger()


def _get_member_by_telegram_id(telegram_user_id: int) -> FamilyMember | None:
    with get_session() as session:
        return session.scalar(
            select(FamilyMember).where(
                FamilyMember.telegram_user_id == telegram_user_id,
                FamilyMember.active.is_(True),
            )
        )


def _save_weight(member_id: int, weight_kg: float) -> WeightLog:
    with get_session() as session:
        log_entry = WeightLog(
            member_id=member_id,
            weight_kg=weight_kg,
            recorded_at=datetime.now(UTC),
        )
        session.add(log_entry)
        session.flush()
        session.expunge(log_entry)
        return log_entry


def _get_previous_weight(member_id: int) -> float | None:
    """이번 것 제외, 직전 기록 반환."""
    with get_session() as session:
        result = session.scalars(
            select(WeightLog)
            .where(WeightLog.member_id == member_id)
            .order_by(WeightLog.recorded_at.desc())
            .limit(2)
        ).all()
        if len(result) >= 2:
            return float(result[1].weight_kg)
        return None


async def weight_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    # 인자 파싱
    args = context.args or []
    if not args:
        await update.message.reply_text("사용법: /몸무게 67.2")
        return

    try:
        weight_kg = float(args[0])
    except ValueError:
        await update.message.reply_text("숫자를 입력해주세요. 예: /몸무게 67.2")
        return

    if not (20 <= weight_kg <= 300):
        await update.message.reply_text("올바른 몸무게 범위(20~300kg)를 입력해주세요.")
        return

    telegram_id = update.effective_user.id

    member = await asyncio.to_thread(_get_member_by_telegram_id, telegram_id)
    if member is None:
        await update.message.reply_text(
            "등록된 가족 구성원을 찾을 수 없습니다.\n관리자에게 텔레그램 ID 등록을 요청하세요."
        )
        return

    member_id = member.id
    await asyncio.to_thread(_save_weight, member_id, weight_kg)

    # 이번 주 남은 nudge 취소
    from datetime import timedelta

    from shared.enums import NotificationStatus
    from shared.models import ScheduledNotification

    today = datetime.now(UTC).date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    with get_session() as cancel_session:
        pending_nudges = cancel_session.scalars(
            select(ScheduledNotification).where(
                ScheduledNotification.source_key.like(f"diet:nudge:{member_id}:%"),
                ScheduledNotification.status == NotificationStatus.pending,
                ScheduledNotification.scheduled_at
                >= datetime(monday.year, monday.month, monday.day, tzinfo=UTC),
                ScheduledNotification.scheduled_at
                <= datetime(sunday.year, sunday.month, sunday.day, 23, 59, tzinfo=UTC),
            )
        ).all()
        for n in pending_nudges:
            n.status = NotificationStatus.cancelled

    prev = await asyncio.to_thread(_get_previous_weight, member_id)

    if prev is not None:
        diff = weight_kg - prev
        sign = "+" if diff >= 0 else ""
        diff_text = f"\n직전 대비 <b>{sign}{diff:.1f}kg</b>"
    else:
        diff_text = ""

    await update.message.reply_text(
        f"✅ 몸무게 기록 완료!\n<b>{weight_kg}kg</b>{diff_text}",
        parse_mode="HTML",
    )
    log.info("몸무게 기록", member_id=member_id, weight_kg=weight_kg)
