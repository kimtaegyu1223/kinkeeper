import structlog
from telegram import Update
from telegram.ext import ContextTypes

from shared.config import settings

log = structlog.get_logger()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    user = update.effective_user
    user_id = user.id
    name = user.full_name

    # 본인에게: 환영 메시지 + ID 안내
    await update.message.reply_text(
        f"안녕하세요! 👨‍👩‍👧‍👦 <b>KinKeeper</b>입니다.\n\n"
        f"📋 회원님의 텔레그램 ID: <code>{user_id}</code>\n\n"
        f"이 숫자를 관리자에게 알려주시면 가족 알림 서비스에 등록됩니다.\n"
        f"사용 가능한 명령어를 보려면 /help 를 입력하세요.",
        parse_mode="HTML",
    )

    # 관리자 채널에 알림 (group_chat_id가 설정된 경우)
    if settings.group_chat_id and context.bot:
        try:
            await context.bot.send_message(
                chat_id=settings.group_chat_id,
                text=(
                    f"🔔 새 사용자가 봇을 시작했습니다!\n\n"
                    f"이름: <b>{name}</b>\n"
                    f"텔레그램 ID: <code>{user_id}</code>\n\n"
                    f"관리자 웹에서 가족 구성원으로 등록해주세요."
                ),
                parse_mode="HTML",
            )
            log.info("신규 사용자 알림 발송", user_id=user_id, name=name)
        except Exception as e:
            log.warning("신규 사용자 알림 발송 실패", error=str(e))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "<b>KinKeeper 명령어</b>\n\n"
        "/start — 시작\n"
        "/help — 명령어 목록\n"
        "/몸무게 [숫자] — 몸무게 기록 (예: /몸무게 67.2)\n"
        "/다음일정 — 앞으로 7일 이내 알림 확인\n\n"
        "일정 추가/수정은 관리자 웹에서 합니다.",
        parse_mode="HTML",
    )
