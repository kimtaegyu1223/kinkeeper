from telegram import Update
from telegram.ext import ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "안녕하세요! 👨‍👩‍👧‍👦 <b>KinKeeper</b>입니다.\n\n"
        "가족 알림 시스템에 연결됐습니다.\n"
        "사용 가능한 명령어를 보려면 /help 를 입력하세요.",
        parse_mode="HTML",
    )


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
