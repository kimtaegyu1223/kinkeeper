"""건강검진 봇 명령어 핸들러.

/내건강검진 — 본인 검진 현황 조회
/검진완료 [검사명] [날짜?] — 검진 기록 등록
"""

import asyncio
from datetime import UTC, date, datetime

import structlog
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from shared.db import get_session
from shared.models import FamilyMember, HealthCheckRecord, HealthCheckType

log = structlog.get_logger()


def _get_member(telegram_user_id: int) -> FamilyMember | None:
    with get_session() as session:
        return session.scalar(
            select(FamilyMember).where(
                FamilyMember.telegram_user_id == telegram_user_id,
                FamilyMember.active.is_(True),
            )
        )


def _get_health_status(member_id: int) -> str:
    """구성원의 전체 검진 현황 텍스트 생성."""
    today = datetime.now(UTC).date()

    with get_session() as session:
        member = session.get(FamilyMember, member_id)
        if not member:
            return "구성원 정보를 찾을 수 없습니다."

        check_types = session.scalars(
            select(HealthCheckType).where(HealthCheckType.active.is_(True))
        ).all()

        lines = [f"📋 <b>{member.name}</b>님 건강검진 현황\n"]

        for ct in check_types:
            # 성별 필터
            if ct.gender and member.gender != ct.gender:
                continue

            latest = session.scalar(
                select(HealthCheckRecord)
                .where(
                    HealthCheckRecord.member_id == member_id,
                    HealthCheckRecord.check_type_id == ct.id,
                )
                .order_by(HealthCheckRecord.checked_at.desc())
                .limit(1)
            )

            if latest is None:
                lines.append(f"❓ <b>{ct.name}</b> — 기록 없음")
                continue

            try:
                next_due = latest.checked_at.replace(year=latest.checked_at.year + ct.period_years)
            except ValueError:
                next_due = latest.checked_at.replace(
                    year=latest.checked_at.year + ct.period_years, day=28
                )

            days_left = (next_due - today).days
            last_str = latest.checked_at.strftime("%Y-%m-%d")
            next_str = next_due.strftime("%Y-%m-%d")

            if days_left < 0:
                status = f"⚠️ <b>{ct.name}</b>"
                lines.append(
                    f"{status} — 마지막: {last_str} | 다음: {next_str} "
                    f"(<b>{abs(days_left)}일 초과!</b>)"
                )
            elif days_left <= 30:
                status = f"🔜 <b>{ct.name}</b>"
                lines.append(
                    f"{status} — 마지막: {last_str} | 다음: {next_str} (<b>{days_left}일 후</b>)"
                )
            else:
                years_left = days_left // 365
                months_left = (days_left % 365) // 30
                period_str = ""
                if years_left:
                    period_str += f"{years_left}년 "
                if months_left:
                    period_str += f"{months_left}개월 "
                period_str = period_str.strip() + " 후"
                lines.append(
                    f"✅ <b>{ct.name}</b> — 마지막: {last_str} | 다음: {next_str} ({period_str})"
                )

        if len(lines) == 1:
            lines.append("등록된 검진 항목이 없습니다.")

        return "\n".join(lines)


def _record_check(member_id: int, check_name: str, checked_at: date) -> str:
    """검진 기록 저장. 결과 메시지 반환."""
    with get_session() as session:
        ct = session.scalar(select(HealthCheckType).where(HealthCheckType.name == check_name))
        if ct is None:
            # 유사 이름 검색
            all_types = session.scalars(
                select(HealthCheckType).where(HealthCheckType.active.is_(True))
            ).all()
            names = [t.name for t in all_types]
            suggestions = [n for n in names if check_name in n or n in check_name]
            hint = (
                "\n혹시 이 중 하나인가요?\n" + "\n".join(f"• {n}" for n in suggestions)
                if suggestions
                else f"\n등록된 검진 항목: {', '.join(names)}"
            )
            return f"'{check_name}' 검진 항목을 찾을 수 없습니다.{hint}"

        # 이미 같은 날 기록 있으면 무시
        existing = session.scalar(
            select(HealthCheckRecord).where(
                HealthCheckRecord.member_id == member_id,
                HealthCheckRecord.check_type_id == ct.id,
                HealthCheckRecord.checked_at == checked_at,
            )
        )
        if existing:
            return (
                f"✅ {ct.name} 검진이 이미 {checked_at.strftime('%Y-%m-%d')}로 기록되어 있습니다."
            )

        session.add(
            HealthCheckRecord(
                member_id=member_id,
                check_type_id=ct.id,
                checked_at=checked_at,
            )
        )
        log.info(
            "검진 기록 저장", member_id=member_id, check_type=ct.name, checked_at=str(checked_at)
        )
        return f"✅ <b>{ct.name}</b> 검진 기록 완료! ({checked_at.strftime('%Y-%m-%d')})"


async def health_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/내건강검진 — 본인 검진 현황."""
    if update.message is None or update.effective_user is None:
        return

    member = await asyncio.to_thread(_get_member, update.effective_user.id)
    if member is None:
        await update.message.reply_text(
            "등록된 가족 구성원을 찾을 수 없습니다.\n관리자에게 텔레그램 ID 등록을 요청하세요."
        )
        return

    text = await asyncio.to_thread(_get_health_status, member.id)
    await update.message.reply_text(text, parse_mode="HTML")


async def health_done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/검진완료 [검사명] [날짜(선택)] — 검진 기록 등록."""
    if update.message is None or update.effective_user is None:
        return

    text = update.message.text or ""
    # "/검진완료" 이후 파싱
    parts = text.split(maxsplit=2)

    if len(parts) < 2:
        await update.message.reply_text(
            "사용법: <code>/검진완료 위내시경</code>\n"
            "또는: <code>/검진완료 위내시경 2026-04-19</code>",
            parse_mode="HTML",
        )
        return

    check_name = parts[1]
    checked_at = datetime.now(UTC).date()

    if len(parts) >= 3:
        try:
            checked_at = date.fromisoformat(parts[2])
        except ValueError:
            await update.message.reply_text(
                "날짜 형식이 올바르지 않습니다. 예: <code>2026-04-19</code>",
                parse_mode="HTML",
            )
            return

    member = await asyncio.to_thread(_get_member, update.effective_user.id)
    if member is None:
        await update.message.reply_text(
            "등록된 가족 구성원을 찾을 수 없습니다.\n관리자에게 텔레그램 ID 등록을 요청하세요."
        )
        return

    result = await asyncio.to_thread(_record_check, member.id, check_name, checked_at)
    await update.message.reply_text(result, parse_mode="HTML")
