import asyncio
import time
from html import escape

import structlog
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from shared.config import settings
from shared.db import get_session
from shared.models import FamilyMember

log = structlog.get_logger()


# --- /start 남용 차단용 인메모리 상태 (단일 프로세스 PTB Application 전제) ---
# DB 지속화가 아니라 프로세스 수명 동안만 유지한다(재시작 시 초기화 허용). 레포가
# bot_data를 쓰지 않으므로 스케줄러 상수들과 같은 모듈 수준 상태로 둔다.
#
# 이 프로세스가 한 번이라도 그룹 알림을 보낸 미등록 user_id. 재호출 시 그룹 도배를 막는다.
_seen_user_ids: set[int] = set()
# 미등록 user_id -> 마지막 응답 시각(time.monotonic). 응답 쿨다운(도배 차단)에 쓴다.
_last_response_at: dict[int, float] = {}
# 미등록 사용자 응답 최소 간격(초). 이 간격 이내 재호출은 조용히 무시한다.
_START_COOLDOWN_SECONDS = 30.0
# 대량 user_id 유입 시 _last_response_at가 무한정 커지지 않도록, 이 수를 넘으면
# 쿨다운이 만료된 오래된 항목을 정리한다(메모리 고갈 방지).
_COOLDOWN_PRUNE_THRESHOLD = 10_000


def _is_registered_active_member(telegram_user_id: int) -> bool:
    """이 텔레그램 ID가 등록된 활성 가족 구성원인지 여부.

    등록 멤버는 '신규 사용자'가 아니므로 그룹 재알림 대상에서 제외한다 (query/health
    핸들러의 멤버 조회와 동일 패턴).
    """
    with get_session() as session:
        return (
            session.scalar(
                select(FamilyMember.id).where(
                    FamilyMember.telegram_user_id == telegram_user_id,
                    FamilyMember.active.is_(True),
                )
            )
            is not None
        )


def _on_cooldown(user_id: int, now: float) -> bool:
    """미등록 사용자가 아직 응답 쿨다운 중인지 여부."""
    last = _last_response_at.get(user_id)
    return last is not None and now - last < _START_COOLDOWN_SECONDS


def _mark_responded(user_id: int, now: float) -> None:
    """미등록 사용자에게 응답한 시각을 기록해 쿨다운을 시작한다."""
    if len(_last_response_at) >= _COOLDOWN_PRUNE_THRESHOLD:
        expired = [
            uid for uid, ts in _last_response_at.items() if now - ts >= _START_COOLDOWN_SECONDS
        ]
        for uid in expired:
            del _last_response_at[uid]
    _last_response_at[user_id] = now


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    user = update.effective_user
    user_id = user.id
    name = user.full_name

    # 미등록 사용자의 반복 /start는 쿨다운 중 조용히 무시한다(응답·그룹 알림 도배 차단).
    # 등록 멤버는 _last_response_at에 기록되지 않으므로 이 검사를 항상 통과한다.
    now = time.monotonic()
    if _on_cooldown(user_id, now):
        return

    # 등록된 활성 멤버는 '신규 사용자'가 아니므로 그룹 재알림 없이 환영만 회신한다.
    if await asyncio.to_thread(_is_registered_active_member, user_id):
        await update.message.reply_text(
            "안녕하세요! 👨‍👩‍👧‍👦 <b>KinKeeper</b>입니다.\n\n"
            "이미 가족 구성원으로 등록되어 있어요.\n"
            "사용 가능한 명령어를 보려면 /help 를 입력하세요.",
            parse_mode="HTML",
        )
        return

    # --- 미등록 사용자 ---
    _mark_responded(user_id, now)

    # 본인에게: 환영 메시지 + ID 안내
    await update.message.reply_text(
        f"안녕하세요! 👨‍👩‍👧‍👦 <b>KinKeeper</b>입니다.\n\n"
        f"📋 회원님의 텔레그램 ID: <code>{user_id}</code>\n\n"
        f"이 숫자를 관리자에게 알려주시면 가족 알림 서비스에 등록됩니다.\n"
        f"사용 가능한 명령어를 보려면 /help 를 입력하세요.",
        parse_mode="HTML",
    )

    # 관리자 채널에 알림 — 이 프로세스가 처음 보는 user_id일 때만 1회 발송한다.
    if settings.group_chat_id and context.bot and user_id not in _seen_user_ids:
        # 재시도로 그룹이 도배되지 않도록 발송 시도 전에 기록한다(재시작 시 초기화 허용).
        _seen_user_ids.add(user_id)
        try:
            await context.bot.send_message(
                chat_id=settings.group_chat_id,
                text=(
                    f"🔔 새 사용자가 봇을 시작했습니다!\n\n"
                    # full_name은 사용자가 프로필에서 임의 설정하므로 escape 필수
                    f"이름: <b>{escape(name)}</b>\n"
                    f"텔레그램 ID: <code>{user_id}</code>\n\n"
                    f"관리자 웹에서 가족 구성원으로 등록해주세요."
                ),
                parse_mode="HTML",
            )
            # 이름·프로필명은 로깅하지 않는다(journald PII 방지). user_id 대리 식별자만 남긴다.
            log.info("신규 사용자 알림 발송", user_id=user_id)
        except Exception as e:
            log.warning("신규 사용자 알림 발송 실패", error=str(e))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    commands = [
        "<b>KinKeeper 명령어</b>",
        "",
        "/start — 시작",
        "/help — 명령어 목록",
        "/다음일정 [일수] — 예정 알림 확인 (예: /다음일정 60)",
        "",
        "일정 추가/수정은 관리자 웹에서 합니다.",
    ]
    await update.message.reply_text(
        "\n".join(commands),
        parse_mode="HTML",
    )
